"""Envio de boletim ao tutor pela Meta Cloud API, a API oficial do WhatsApp.

Nunca um gateway não-oficial (biblioteca que automatiza o WhatsApp Web): é
violação dos termos e o número da clínica é banido, e um número banido leva
junto o canal com TODOS os tutores da clínica, não só o daquela conversa.

O `external_id` devolvido aqui é o que a Meta chama de `wamid`. É por ele que
o webhook de status casa a confirmação de entrega/leitura com a linha em
`owner_contacts` (delivered_at / read_at): sem guardar o wamid, o callback
chega e não tem onde pousar.

Nada aqui inventa resultado. Sem credencial levanta `WhatsAppNotConfigured`,
com falha do provedor levanta `WhatsAppError`, e quem chama grava a tentativa
como `failed`. O stub anterior devolvia `stub-<uuid>` e a rota gravava
`sent_at`: o prontuário passou a conter registro auditado de uma entrega que
nunca houve, que é pior do que não ter a funcionalidade.

## O TEMPLATE PRECISA SER APROVADO NO CONSOLE DA META: isto é trabalho humano

Fora da janela de 24h de atendimento (customer service window), a Meta só
aceita mensagem iniciada pela empresa em formato de **template aprovado**;
texto livre é recusado com erro 131047/132000. O boletim de internação é, por
definição, iniciado pela clínica, então o envio daqui é SEMPRE template.

Cadastrar em WhatsApp Manager → Modelos de mensagem:

* **Nome**: o valor de `WHATSAPP_TEMPLATE_NAME` (default `boletim_internacao`).
* **Categoria**: UTILITY (não MARKETING: boletim clínico é utilitário, e
  MARKETING é bloqueado por quem desativa promoções).
* **Idiomas**: um por locale que a clínica usa (`pt_BR`, `en_US`). O template é
  aprovado POR IDIOMA; falta o idioma, a API recusa com 132001.
* **Corpo**, com exatamente TRÊS variáveis, nesta ordem:

      Boletim de internação de {{1}}, da {{2}}.

      {{3}}

  {{1}} = nome do paciente · {{2}} = nome da clínica · {{3}} = o texto do
  boletim escrito pela equipe.

Enquanto o template não estiver APROVADO, todo envio falha e é gravado como
`failed` com o motivo da Meta. Isso é o desenhado: a clínica vê a mensagem não
saiu, em vez de acreditar que saiu.
"""

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Teto de tempo da chamada à Meta. Quem espera é o profissional com o tutor na
#: linha: melhor gravar a tentativa como falha em 15s do que pendurar a tela.
TIMEOUT_SECONDS = 15.0

#: Limite da Meta para cada variável de corpo do template. Acima disso a API
#: recusa a mensagem inteira (132000).
MAX_PARAM_CHARS = 1024

#: A Meta recusa variável com quebra de linha, tab ou 4+ espaços seguidos
#: (erro 132000, "parameter format does not match"). O boletim digitado tem
#: parágrafo; sem normalizar, todo envio real falharia.
_WHITESPACE = re.compile(r"\s+")

#: locale da clínica → código de idioma do template na Meta (underscore, não
#: hífen). Fora do mapa, converte `pt-BR` em `pt_BR` e deixa a Meta recusar se
#: o idioma não estiver aprovado. Nunca cai calado para outro idioma, porque
#: mandar boletim no idioma errado para o tutor é pior que não mandar.
_LOCALE_TO_META = {
    "pt-BR": "pt_BR",
    "pt": "pt_BR",
    "en": "en_US",
    "en-US": "en_US",
}

#: Status que a Meta emite no webhook. `deleted` e `warning` também aparecem e
#: não descrevem entrega: são ignorados em vez de virarem estado inventado.
CALLBACK_STATUSES = frozenset({"sent", "delivered", "read", "failed"})


class WhatsAppError(RuntimeError):
    """Falha de envio, já reduzida a um motivo curto e gravável.

    `reason` vai para `owner_contacts.failure_reason`: é diagnóstico do
    provedor guardado no log de contatos, não texto de interface. O que a UI
    traduz é o CÓDIGO de erro da resposta (ADR-0004)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class WhatsAppNotConfigured(WhatsAppError):
    """Sem credencial da Meta. Não é erro de programação: a integração é
    opcional e a clínica que não a contratou usa o sistema inteiro sem ela."""

    def __init__(self) -> None:
        super().__init__("not_configured")


@dataclass(frozen=True)
class StatusUpdate:
    """Uma confirmação de status vinda do webhook, já normalizada."""

    external_id: str
    status: str
    at: datetime
    reason: str | None = None


def _meta_language(locale: str) -> str:
    return _LOCALE_TO_META.get(locale, locale.replace("-", "_"))


def _param(value: str, *, field: str) -> str:
    text = _WHITESPACE.sub(" ", value).strip()
    if not text:
        raise WhatsAppError(f"empty_param:{field}")
    if len(text) > MAX_PARAM_CHARS:
        # Truncar entregaria ao tutor um boletim diferente do que o prontuário
        # diz que foi enviado. Falha explícita: a equipe encurta e reenvia.
        raise WhatsAppError(f"param_too_long:{field}")
    return text


def _reason_from_response(response: httpx.Response) -> str:
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        error = {}
    code = error.get("code", response.status_code)
    message = error.get("message") or response.text or ""
    return f"meta_{code}: {message}"[:500]


class WhatsAppClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # Costura de teste: `httpx.MockTransport` entra aqui e nenhuma chamada
        # da suíte sai para a rede. Em produção fica None (transporte padrão).
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(settings.whatsapp_phone_number_id and settings.whatsapp_token)

    async def send_bulletin(
        self,
        to_e164: str,
        *,
        patient_name: str,
        clinic_name: str,
        body: str,
        locale: str,
    ) -> str:
        """Envia o template do boletim e devolve o `wamid` da Meta.

        Levanta `WhatsAppNotConfigured` sem credencial e `WhatsAppError` em
        qualquer falha. Nunca devolve id sintético: um id que a Meta não
        conhece nunca receberia callback de entrega, e a linha ficaria
        eternamente "enviada" sem confirmação nenhuma."""
        if not self.configured:
            raise WhatsAppNotConfigured()

        parameters = [
            _param(patient_name, field="patient_name"),
            _param(clinic_name, field="clinic_name"),
            _param(body, field="body"),
        ]
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            # A Meta aceita E.164 sem o "+"; com ele algumas contas devolvem
            # 131026 ("recipient not found") em vez de entregar.
            "to": to_e164.lstrip("+"),
            "type": "template",
            "template": {
                "name": settings.whatsapp_template_name,
                "language": {"code": _meta_language(locale)},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": p} for p in parameters],
                    }
                ],
            },
        }
        url = (
            f"https://graph.facebook.com/{settings.whatsapp_api_version}"
            f"/{settings.whatsapp_phone_number_id}/messages"
        )
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            # Rede fora não pode virar 500 nem exceção não tratada: o envio ao
            # tutor é infraestrutura opcional, o resto do plantão segue.
            raise WhatsAppError(f"network: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise WhatsAppError(_reason_from_response(response))
        try:
            messages = (response.json() or {}).get("messages") or []
            external_id = messages[0]["id"]
        except (ValueError, LookupError, TypeError) as exc:
            # 2xx sem wamid: a Meta aceitou algo que não sabemos casar com o
            # webhook. Gravar como enviado seria afirmar entrega sem prova.
            raise WhatsAppError("missing_wamid") from exc
        logger.info("whatsapp_sent to=%s wamid=%s", to_e164, external_id)
        return external_id


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """`X-Hub-Signature-256` = HMAC-SHA256 do corpo CRU com o app secret.

    O webhook é público por definição, e a assinatura É a autenticação. Sem ela
    qualquer um escreve `delivered_at`/`read_at` no prontuário de uma clínica.
    Sem `whatsapp_app_secret` configurado nada é aceito: não dá para
    autenticar, então não se grava."""
    secret = settings.whatsapp_app_secret
    if not secret or not header:
        return False
    scheme, _, digest = header.partition("=")
    if scheme != "sha256" or not digest:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    # compare_digest e não "==": comparação de string vaza o prefixo correto
    # por tempo de resposta.
    return hmac.compare_digest(expected, digest)


def verify_handshake(mode: str | None, token: str | None) -> bool:
    """Handshake de verificação da assinatura do webhook (GET)."""
    expected = settings.whatsapp_verify_token
    if not expected or mode != "subscribe" or not token:
        return False
    return hmac.compare_digest(expected, token)


def _callback_moment(raw: object) -> datetime:
    # A Meta manda unix seconds em string. Sem timestamp usável fica o instante
    # do processamento: aproximação honesta; inventar um passado seria pior.
    try:
        return datetime.fromtimestamp(int(str(raw)), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _callback_reason(status: dict) -> str | None:
    errors = status.get("errors") or []
    if not errors or not isinstance(errors[0], dict):
        return None
    first = errors[0]
    title = first.get("title") or first.get("message") or ""
    return f"meta_{first.get('code', 'unknown')}: {title}"[:500]


def parse_status_updates(payload: dict) -> list[StatusUpdate]:
    """Achata `entry[].changes[].value.statuses[]` do callback da Meta.

    Parsing defensivo de propósito: a Meta acrescenta campos sem aviso e um
    payload autêntico com forma inesperada não pode virar 500, porque 500 faz a
    Meta reenviar em loop e, depois de muitas falhas, desinscrever o webhook."""
    updates: list[StatusUpdate] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            for status in value.get("statuses") or []:
                if not isinstance(status, dict):
                    continue
                external_id = status.get("id")
                name = status.get("status")
                if not external_id or name not in CALLBACK_STATUSES:
                    continue
                updates.append(
                    StatusUpdate(
                        external_id=str(external_id),
                        status=str(name),
                        at=_callback_moment(status.get("timestamp")),
                        reason=_callback_reason(status),
                    )
                )
    return updates


# Instância de módulo: a rota depende desta e o teste pode trocá-la.
whatsapp_client = WhatsAppClient()
