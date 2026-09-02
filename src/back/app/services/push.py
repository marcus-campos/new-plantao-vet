"""Alerta no bolso: o único canal ATIVO do sistema.

Todo o resto do produto é escalonamento VISUAL: a fila reordena, o cartão fica
vermelho, o contador sobe. Aqui é diferente: aqui o telefone vibra no bolso de
quem está com as mãos dentro de outro paciente, e isso tem um custo que nenhuma
tela tem.

Daí o orçamento de alertas (pesquisa §4: só 5–13% dos alarmes de UTI são
acionáveis e 74–99% não exigem ação nenhuma; o efeito colateral é a equipe
deixar de ouvir os verdadeiros. É o SmartFlow apitando a cada 30 segundos, que
a spec nomeia como o que NÃO copiar). Notificação ativa existe para DUAS
coisas:

1. dose **crítica** fora da janela ISMP (`criticality == "critical"` e
   `display_state == "overdue"`);
2. intercorrência em que alguém pediu para avisar o veterinário.

Nada mais. Um terceiro motivo aqui é decisão de produto, não uma linha a mais.

Três garantias que este módulo não pode quebrar:

* **Sem credencial o push não acontece e nada quebra.** Sem `FCM_PROJECT` a
  chamada é no-op que devolve 0 e loga. Nenhuma execução clínica volta atrás
  porque a notificação falhou: o push é infraestrutura opcional.
* **Nunca afirmar um envio que não houve.** O número devolvido conta aparelhos
  que o provedor ACEITOU. Erro, timeout e credencial ausente contam zero e não
  gastam orçamento, para que a próxima tentativa ainda possa avisar.
* **A mesma pessoa não é acordada duas vezes pelo mesmo fato.** O dedupe é por
  evento, não por destinatário: um fato avisa todo mundo que precisa saber,
  uma vez só.
"""

import logging
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.i18n.catalog import translate
from app.models import Clinic, Hospitalization, Membership, Patient, Task, TaskStatus
from app.models.device import Device
from app.permissions import LICENSED_ROLE
from app.services.audit import ActorInfo
from app.services.board import BoardService
from app.services.providers import ProviderUnavailable
from app.services.providers.google_auth import FIREBASE_MESSAGING_SCOPE, access_token
from app.services.tasks import TaskService

logger = logging.getLogger(__name__)

#: A credencial e o escopo vêm de `providers/google_auth`: a emissão do token
#: de service account é a MESMA da Vertex e mora num lugar só. Uma cópia local
#: daria duas verdades sobre a mesma credencial, e a que ninguém lembra de
#: rotacionar é a que vaza.
FCM_ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"

#: Canal de notificação que o app cria no Android (`mobile/src/notifications.ts`).
#: Sem `channel_id` o Android entrega no canal padrão, sem som nem prioridade.
#: Um alerta de dose crítica que chega mudo não é alerta.
ANDROID_CHANNEL = "critical"

#: Teto de tempo de uma chamada ao FCM. Quem espera é uma requisição clínica
#: que já terminou: melhor desistir do aviso do que segurar a conexão.
TIMEOUT_SECONDS = 10.0

#: O app registra token do EXPO (`getExpoPushTokenAsync`), que o FCM não
#: conhece: mandado para lá, volta 400 em todo envio. É melhor pular e logar do
#: que gastar orçamento e contar como enviado o que ninguém recebeu.
EXPO_TOKEN_PREFIXES = ("ExponentPushToken[", "ExpoPushToken[")

#: Por quanto tempo um evento já avisado continua conhecido. Uma dose crítica
#: atrasada segue atrasada por dias; sem memória, cada varredura acordaria o
#: plantão de novo pelo mesmo atraso.
DEDUPE_TTL = timedelta(days=7)


class DeliveryResult(StrEnum):
    sent = "sent"
    failed = "failed"
    #: Token morto (app desinstalado, token rotacionado): não adianta insistir.
    retired = "retired"
    #: Token que este transporte não sabe entregar.
    skipped = "skipped"


@dataclass(frozen=True)
class Alert:
    """O que chega na tela de bloqueio, já no idioma da clínica."""

    title: str
    body: str
    #: Só string: o FCM recusa `data` com valor que não seja texto.
    data: dict[str, str]


class AlertBudget:
    """O teto de alertas, em memória do PROCESSO.

    Duas contas: quantos alertas cada pessoa recebeu na última hora
    (`push_max_per_hour`) e quais eventos já foram avisados. É o que separa
    "alerta" de "ruído": sem teto, uma noite ruim com seis pacientes graves
    vira vinte vibrações e a equipe silencia o aplicativo, que é a falha que
    este módulo inteiro existe para evitar.

    Limitação conhecida e deliberada: a API roda em um processo só (um
    `uvicorn`), então este estado é suficiente e não custa uma tabela nem um
    Redis. Rodar dois workers duplicaria o teto; nesse dia isto vira tabela,
    e o resto do módulo não muda.
    """

    def __init__(self) -> None:
        self._per_membership: dict[uuid.UUID, deque[datetime]] = {}
        self._events: dict[str, datetime] = {}

    def allows(self, membership_id: uuid.UUID, now: datetime) -> bool:
        janela = self._per_membership.get(membership_id)
        if janela is None:
            return True
        limite = now - timedelta(hours=1)
        while janela and janela[0] < limite:
            janela.popleft()
        return len(janela) < settings.push_max_per_hour

    def charge(self, membership_id: uuid.UUID, now: datetime) -> None:
        """Só é cobrado o alerta que o provedor ACEITOU: um provedor fora do ar
        não pode consumir o orçamento de quem não foi avisado."""
        self._per_membership.setdefault(membership_id, deque()).append(now)

    def already_sent(self, event_key: str, now: datetime) -> bool:
        limite = now - DEDUPE_TTL
        for key, momento in list(self._events.items()):
            if momento < limite:
                del self._events[key]
        return event_key in self._events

    def mark_sent(self, event_key: str, now: datetime) -> None:
        self._events[event_key] = now

    def reset(self) -> None:
        self._per_membership.clear()
        self._events.clear()


#: Instância de módulo: o orçamento é do processo, e o teste o zera.
budget = AlertBudget()


def _text(key: str, locale: str, fallback: str, **params: Any) -> str:
    """O texto do alerta no idioma da clínica, com queda para o dado cru.

    `translate` levanta `KeyError` de propósito: chave ausente é bug pego no
    teste, nunca produção silenciosa. Aqui essa regra não pode valer: uma chave
    faltando no catálogo calaria um alerta de dose crítica. O fallback é o dado
    bruto (nome do paciente, título da tarefa), que informa mesmo sem frase.
    """
    try:
        return translate(key, locale, **params)
    except (KeyError, IndexError):
        logger.warning("push: chave i18n ausente (%s); usando o dado cru", key)
        return fallback


class PushClient:
    """Transporte FCM HTTP v1. Só HTTP: nenhuma regra de produto mora aqui."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # Costura de teste: `httpx.MockTransport` entra aqui e nenhuma chamada
        # da suíte sai para a rede.
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(settings.fcm_project)

    async def bearer(self) -> str | None:
        """Token de acesso da service account, no escopo do FCM."""
        try:
            return await access_token(FIREBASE_MESSAGING_SCOPE)
        except ProviderUnavailable:
            # Sem JSON de service account não há push, e não há erro: a
            # integração é opcional e a clínica que não a configurou usa o
            # sistema inteiro sem ela.
            logger.info("push: sem credencial do Google, nada será enviado")
            return None
        except Exception:
            # Credencial inválida derruba o aviso, nunca o plantão.
            logger.exception("push: falha ao obter token da service account")
            return None

    async def send_many(self, tokens: list[str], alert: Alert) -> dict[str, DeliveryResult]:
        """Entrega `alert` em cada token. Nunca levanta: devolve o que houve."""
        resultados: dict[str, DeliveryResult] = {}
        if not self.configured:
            return {token: DeliveryResult.skipped for token in tokens}
        entregaveis = [token for token in tokens if not self.is_expo_token(token)]
        for token in tokens:
            if self.is_expo_token(token):
                resultados[token] = DeliveryResult.skipped
        if not entregaveis:
            if tokens:
                logger.warning(
                    "push: %d token(s) do Expo ignorados — o FCM não os entrega", len(tokens)
                )
            return resultados

        token_acesso = await self.bearer()
        if token_acesso is None:
            return resultados | {token: DeliveryResult.failed for token in entregaveis}

        url = FCM_ENDPOINT.format(project=settings.fcm_project)
        headers = {"Authorization": f"Bearer {token_acesso}"}
        async with httpx.AsyncClient(
            transport=self._transport, timeout=TIMEOUT_SECONDS
        ) as client:
            for token in entregaveis:
                resultados[token] = await self._send_one(
                    client, url=url, headers=headers, token=token, alert=alert
                )
        return resultados

    @staticmethod
    def is_expo_token(token: str) -> bool:
        return token.startswith(EXPO_TOKEN_PREFIXES)

    async def _send_one(
        self,
        client: httpx.AsyncClient,
        *,
        url: str,
        headers: dict[str, str],
        token: str,
        alert: Alert,
    ) -> DeliveryResult:
        payload = {
            "message": {
                "token": token,
                "notification": {"title": alert.title, "body": alert.body},
                "data": alert.data,
                "android": {
                    # Dose crítica atrasada acorda a tela; o Android segura
                    # prioridade normal até o aparelho sair do repouso.
                    "priority": "HIGH",
                    "notification": {"channel_id": ANDROID_CHANNEL, "sound": "default"},
                },
                "apns": {
                    "headers": {"apns-priority": "10"},
                    "payload": {
                        "aps": {"sound": "default", "interruption-level": "time-sensitive"}
                    },
                },
                # O mesmo token, quando é do NAVEGADOR (web push via FCM). O
                # FCM ignora este bloco para token de iOS/Android, então ele
                # pode ir sempre. `link` é o que abre ao tocar na notificação:
                # a ficha do paciente, não a home. `requireInteraction` segura
                # o aviso na tela até alguém olhar, que é o que uma dose
                # crítica atrasada pede.
                "webpush": {
                    "headers": {"Urgency": "high"},
                    "notification": {
                        "icon": "/icons/icon-192.png",
                        "badge": "/icons/icon-192.png",
                        "requireInteraction": True,
                        "tag": alert.data.get("event", "plantaovet"),
                    },
                    "fcm_options": {"link": alert.data.get("url", "/plantao")},
                },
            }
        }
        try:
            response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.warning("push: rede fora (%s)", type(exc).__name__)
            return DeliveryResult.failed
        if response.status_code < 400:
            return DeliveryResult.sent
        if _token_is_dead(response):
            logger.info("push: token morto, desativando aparelho")
            return DeliveryResult.retired
        logger.warning("push: FCM recusou (%s) %s", response.status_code, response.text[:300])
        return DeliveryResult.failed


def _token_is_dead(response: httpx.Response) -> bool:
    """O token não existe mais: desinstalação ou rotação do aparelho.

    O FCM devolve 404/UNREGISTERED, e 400/INVALID_ARGUMENT quando o token é
    inválido. Insistir nesses dois é gastar orçamento com um aparelho que não
    existe; o resto (403, 5xx) é falha transitória e o token continua vivo."""
    if response.status_code == 404:
        return True
    if response.status_code != 400:
        return False
    try:
        detalhe = (response.json() or {}).get("error") or {}
    except ValueError:
        return False
    return str(detalhe.get("status")) in ("INVALID_ARGUMENT", "NOT_FOUND", "UNREGISTERED")


#: Instância de módulo: o serviço depende desta e o teste pode trocá-la.
push_client = PushClient()


class PushService:
    @staticmethod
    async def notify(
        session: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        membership_ids: list[uuid.UUID],
        alert: Alert,
        event_key: str | None = None,
        now: datetime | None = None,
    ) -> int:
        """Entrega `alert` aos aparelhos ativos das pessoas indicadas.

        Devolve quantos aparelhos o provedor aceitou: 0 quando o push está
        desligado, quando ninguém tem aparelho ou quando o provedor falhou.
        NUNCA levanta: quem chama acabou de registrar um ato clínico e esse ato
        não pode ser desfeito por uma notificação."""
        try:
            return await PushService._notify(
                session,
                clinic_id=clinic_id,
                membership_ids=membership_ids,
                alert=alert,
                event_key=event_key,
                now=now or datetime.now(UTC),
            )
        except Exception:
            logger.exception("push: envio falhou")
            return 0

    @staticmethod
    async def _notify(
        session: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        membership_ids: list[uuid.UUID],
        alert: Alert,
        event_key: str | None,
        now: datetime,
    ) -> int:
        if not push_client.configured:
            logger.info(
                "push desligado (FCM_PROJECT ausente): %d destinatário(s) não avisados",
                len(membership_ids),
            )
            return 0
        if event_key is not None and budget.already_sent(event_key, now):
            return 0

        # dict.fromkeys preserva a ordem e tira repetido: a mesma pessoa em dois
        # turnos abertos não recebe dois alertas do mesmo fato.
        destinatarios = [
            membership_id
            for membership_id in dict.fromkeys(membership_ids)
            if budget.allows(membership_id, now)
        ]
        if not destinatarios:
            logger.info("push: orçamento esgotado para todos os destinatários")
            return 0

        aparelhos = list(
            (
                await session.execute(
                    sa.select(Device).where(
                        Device.clinic_id == clinic_id,
                        Device.membership_id.in_(destinatarios),
                        Device.is_active.is_(True),
                    )
                )
            ).scalars()
        )
        if not aparelhos:
            logger.info("push: ninguém com aparelho registrado para receber o alerta")
            return 0

        resultados = await push_client.send_many([a.token for a in aparelhos], alert)

        entregues = 0
        avisados: set[uuid.UUID] = set()
        mortos: list[uuid.UUID] = []
        for aparelho in aparelhos:
            resultado = resultados.get(aparelho.token, DeliveryResult.failed)
            if resultado is DeliveryResult.sent:
                entregues += 1
                avisados.add(aparelho.membership_id)
            elif resultado is DeliveryResult.retired:
                mortos.append(aparelho.id)

        if mortos:
            await session.execute(
                sa.update(Device).where(Device.id.in_(mortos)).values(is_active=False)
            )
            # Commit próprio: quem chama já fechou a transação clínica dele, e
            # um token morto que sobrevive ao request é tentativa repetida para
            # sempre.
            await session.commit()

        for membership_id in avisados:
            budget.charge(membership_id, now)
        if entregues and event_key is not None:
            budget.mark_sent(event_key, now)
        return entregues

    # ---- destinatários ----------------------------------------------------

    @staticmethod
    async def on_duty(
        session: AsyncSession, *, clinic_id: uuid.UUID, now: datetime
    ) -> list[uuid.UUID]:
        """Quem está de plantão agora. `BoardService.current_shifts` já responde
        isso para o painel, e duas definições de "quem está aqui" divergiriam."""
        turnos = await BoardService.current_shifts(session, clinic_id=clinic_id, now=now)
        return list(dict.fromkeys(turno.membership_id for turno in turnos))

    @staticmethod
    async def vets_to_alert(
        session: AsyncSession, *, clinic_id: uuid.UUID, now: datetime
    ) -> list[uuid.UUID]:
        """Os veterinários de plantão e, se a escala estiver vazia, TODOS os
        veterinários ativos da clínica.

        Uma convulsão às 3h com a escala em branco não pode ficar em silêncio:
        escala vazia é falha de cadastro, não ausência de responsável."""
        de_plantao = await PushService.on_duty(session, clinic_id=clinic_id, now=now)
        if de_plantao:
            turnos = await BoardService.current_shifts(session, clinic_id=clinic_id, now=now)
            responsaveis = {t.membership_id for t in turnos if t.is_vet_responsible}
            papeis = dict(
                (
                    await session.execute(
                        sa.select(Membership.id, Membership.role).where(
                            Membership.id.in_(de_plantao), Membership.is_active.is_(True)
                        )
                    )
                ).all()
            )
            vets = [
                membership_id
                for membership_id in de_plantao
                if membership_id in responsaveis
                or str(papeis.get(membership_id)) == LICENSED_ROLE
            ]
            if vets:
                return vets
        return list(
            (
                await session.execute(
                    sa.select(Membership.id).where(
                        Membership.clinic_id == clinic_id,
                        Membership.role == LICENSED_ROLE,
                        Membership.is_active.is_(True),
                    )
                )
            ).scalars()
        )

    # ---- os dois motivos --------------------------------------------------

    @staticmethod
    async def notify_intercurrence(
        session: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        task: Task,
        actor: ActorInfo,
        now: datetime | None = None,
    ) -> int:
        """O switch "avisar o veterinário" cumprindo o que promete.

        O app grava `values.notify_vet` desde sempre e NADA no backend lia esse
        campo: o técnico registrava a convulsão das 3h, marcava o aviso, e
        ninguém era avisado. Mentira em caminho de segurança é pior que
        funcionalidade ausente."""
        now = now or datetime.now(UTC)
        try:
            clinic = await session.get(Clinic, clinic_id)
            paciente = await PushService._patient_name(session, task)
            destinatarios = [
                membership_id
                for membership_id in await PushService.vets_to_alert(
                    session, clinic_id=clinic_id, now=now
                )
                # Quem registrou já sabe: avisar o próprio autor é ruído puro.
                if membership_id != actor.membership_id
            ]
        except Exception:
            logger.exception("push: não foi possível montar o aviso de intercorrência")
            return 0

        locale = clinic.locale if clinic else "pt-BR"
        nota = str((task.values or {}).get("note") or "").strip()
        titulo = _text(
            "push.intercurrence.title", locale, paciente, patient=paciente
        )
        if nota:
            corpo = _text(
                "push.intercurrence.body_with_note",
                locale,
                f"{task.title}: {nota}",
                title=task.title,
                note=nota,
                author=actor.name,
            )
        else:
            corpo = _text(
                "push.intercurrence.body",
                locale,
                task.title,
                title=task.title,
                author=actor.name,
            )
        alert = Alert(
            title=titulo,
            body=corpo,
            data={
                "kind": "intercurrence",
                "task_id": str(task.id),
                "hospitalization_id": str(task.hospitalization_id),
            },
        )
        return await PushService.notify(
            session,
            clinic_id=clinic_id,
            membership_ids=destinatarios,
            alert=alert,
            event_key=f"intercurrence:{task.id}",
            now=now,
        )

    @staticmethod
    async def sweep_critical_overdue(
        session: AsyncSession, *, clinic: Clinic, now: datetime
    ) -> int:
        """Avisa cada dose CRÍTICA que passou da janela ISMP.

        Uma vez por dose (`event_key` pelo id da tarefa): a dose continua
        atrasada até alguém baixá-la, e repetir o aviso a cada varredura é
        exatamente o apito de 30 em 30 segundos que a spec manda não copiar.
        Dose normal atrasada NÃO entra aqui; ela escala no painel."""
        linhas = (
            await session.execute(
                sa.select(Task, Patient.name)
                .join(Hospitalization, Hospitalization.id == Task.hospitalization_id)
                .join(Patient, Patient.id == Hospitalization.patient_id)
                .where(
                    Task.clinic_id == clinic.id,
                    Task.status == TaskStatus.pending,
                    Task.criticality == "critical",
                    Task.scheduled_for < now,
                    Hospitalization.status == "active",
                )
                .order_by(Task.scheduled_for.asc())
            )
        ).all()

        atrasadas = [
            (task, paciente)
            for task, paciente in linhas
            # A MESMA função que a fila e o painel usam: se aqui divergisse, o
            # aparelho apitaria por uma dose que a tela mostra no prazo.
            if TaskService.display_state(task, now) == "overdue"
        ]
        if not atrasadas:
            return 0

        destinatarios = await PushService.on_duty(session, clinic_id=clinic.id, now=now)
        if not destinatarios:
            destinatarios = await PushService.vets_to_alert(
                session, clinic_id=clinic.id, now=now
            )

        entregues = 0
        for task, paciente in atrasadas:
            minutos = TaskService.minutes_late(task, now) or 0
            alert = Alert(
                title=_text(
                    "push.critical_overdue.title", clinic.locale, paciente, patient=paciente
                ),
                body=_text(
                    "push.critical_overdue.body",
                    clinic.locale,
                    f"{paciente} · {task.title}",
                    patient=paciente,
                    task=task.title,
                    minutes=minutos,
                ),
                data={
                    "kind": "critical_overdue",
                    "task_id": str(task.id),
                    "hospitalization_id": str(task.hospitalization_id),
                },
            )
            entregues += await PushService.notify(
                session,
                clinic_id=clinic.id,
                membership_ids=destinatarios,
                alert=alert,
                event_key=f"critical_overdue:{task.id}",
                now=now,
            )
        return entregues

    @staticmethod
    async def sweep_all_clinics(session_factory, *, now: datetime) -> int:
        """A varredura no formato dos jobs de `app/workers/scheduler.py`.

        Falta UMA linha lá para ligar isto. Enquanto ela não existe, a dose
        crítica atrasada só escala no painel."""
        entregues = 0
        async with session_factory() as session:
            clinicas = list((await session.execute(sa.select(Clinic))).scalars())
            for clinic in clinicas:
                entregues += await PushService.sweep_critical_overdue(
                    session, clinic=clinic, now=now
                )
        return entregues

    @staticmethod
    async def _patient_name(session: AsyncSession, task: Task) -> str:
        """Nome do paciente. O alerta sem ele obrigaria a abrir o app para saber
        de quem se trata, e no meio da noite isso é o alerta falhando."""
        nome = await session.scalar(
            sa.select(Patient.name)
            .join(Hospitalization, Hospitalization.patient_id == Patient.id)
            .where(Hospitalization.id == task.hospitalization_id)
        )
        return nome or ""
