"""O que precisa de atenção agora.

O painel antigo respondia "quantos pacientes e quantas tarefas existem" e
ordenava por nome do paciente, e o cartão vermelho podia ser o último da lista.
A pergunta certa é outra: **o que precisa de mim agora**, e a resposta tem de
vir ordenada por urgência, com o motivo em palavras.

Duas regras que este módulo não pode quebrar:

1. **Uma fila só.** O estado de uma tarefa vem de `TaskService.display_state`,
   a mesma função que a ficha usa. Painel e ficha divergirem é o bug que
   destruiu a confiança no concorrente (pesquisa §6).
2. **"Atrasada" não é persistida.** É derivada na leitura, aqui como em todo
   lugar.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Clinic, Hospitalization, Kennel, Patient, Task, TaskStatus
from app.models.membership import Membership
from app.models.shift import Shift
from app.services.progress_notes import ProgressNoteService
from app.services.tasks import TaskService

#: Quanto do futuro o painel enxerga. Um plantão inteiro à frente: às 22h o
#: plantonista precisa ver as âncoras de 02h e 04h.
LOOKAHEAD = timedelta(hours=12)

#: A "próxima hora": o balde que separa o que dá para respirar do que não dá.
NEXT_HOUR = timedelta(hours=1)

#: Por que este paciente precisa de atenção, do mais grave para o menos.
#: O número ordena a lista; o código vira frase no cliente (a API nunca devolve
#: prosa, ADR-0004).
ATTENTION_SEVERITY: dict[str, int] = {
    "critical_overdue": 40,
    "overdue": 30,
    "no_progress_note": 20,
    "due": 10,
}


@dataclass(frozen=True)
class Attention:
    reason: str
    severity: int
    #: Minutos de atraso (tarefa) ou horas sem evolução: o cliente decide a
    #: unidade pelo `reason`. Sem magnitude, "atrasada" não diferencia 5 min de
    #: 5 horas, e a fila não tem como se ordenar.
    magnitude: int | None = None
    task_title: str | None = None


def clinic_day_bounds(clinic: Clinic, now: datetime) -> tuple[datetime, datetime]:
    """O dia da CLÍNICA, não o do navegador nem o UTC.

    O extrato já agrupa por este dia (`ChargeService.statement`); o painel
    contava "hoje" pelo relógio de quem olha, então a mesma internação mostrava
    dias diferentes em duas telas."""
    tz = ZoneInfo(clinic.timezone)
    today: date = now.astimezone(tz).date()
    start = datetime.combine(today, time(0, 0), tzinfo=tz).astimezone(UTC)
    return start, start + timedelta(days=1)


class BoardService:
    @staticmethod
    async def current_shifts(
        session: AsyncSession, *, clinic_id: uuid.UUID, now: datetime
    ) -> list[Shift]:
        """Quem está de plantão AGORA: começou, não terminou e não foi fechado.

        Um turno fechado antes da hora acabou de verdade: quem fechou foi
        embora. Por isso `closed_at` vence `ends_at`."""
        return list(
            (
                await session.execute(
                    sa.select(Shift)
                    .where(
                        Shift.clinic_id == clinic_id,
                        Shift.starts_at <= now,
                        Shift.ends_at > now,
                        Shift.closed_at.is_(None),
                    )
                    .order_by(Shift.starts_at.asc())
                )
            ).scalars()
        )

    @staticmethod
    def _attention(
        pending: list[Task], now: datetime, hours_without_note: int | None
    ) -> Attention | None:
        """O motivo MAIS grave deste paciente. Um só: uma linha, uma razão.

        Listar todos os motivos devolveria a pessoa ao trabalho de comparar,
        que é justamente o que a ordenação existe para poupar."""
        pior: Attention | None = None
        for task in pending:
            estado = TaskService.display_state(task, now)
            if estado == "overdue":
                critica = str(task.criticality) == "critical"
                reason = "critical_overdue" if critica else "overdue"
                candidato = Attention(
                    reason=reason,
                    severity=ATTENTION_SEVERITY[reason],
                    magnitude=TaskService.minutes_late(task, now),
                    task_title=task.title,
                )
            elif estado == "due":
                candidato = Attention(
                    reason="due",
                    severity=ATTENTION_SEVERITY["due"],
                    magnitude=TaskService.minutes_late(task, now),
                    task_title=task.title,
                )
            else:
                continue
            if pior is None or (candidato.severity, candidato.magnitude or 0) > (
                pior.severity,
                pior.magnitude or 0,
            ):
                pior = candidato

        if hours_without_note is not None:
            nota = Attention(
                reason="no_progress_note",
                severity=ATTENTION_SEVERITY["no_progress_note"],
                magnitude=hours_without_note,
            )
            if pior is None or nota.severity > pior.severity:
                pior = nota
        return pior

    @staticmethod
    def bucket(task: Task, now: datetime) -> str:
        """AGORA · PRÓXIMA HORA · DEPOIS.

        O plantonista não precisa interpretar vinte horários: precisa saber o
        que é para já. Vencida e dentro da janela são a mesma coisa para quem
        está de pé ao lado do box: as duas são "agora"."""
        if TaskService.display_state(task, now) in ("due", "overdue"):
            return "now"
        if task.scheduled_for <= now + NEXT_HOUR:
            return "next_hour"
        return "later"

    @staticmethod
    async def build(
        session: AsyncSession,
        *,
        clinic: Clinic,
        now: datetime,
        viewer_membership_id: uuid.UUID | None = None,
    ) -> dict:
        internacoes = list(
            (
                await session.execute(
                    sa.select(Hospitalization, Patient, Kennel.name, Kennel.id)
                    .join(Patient, Patient.id == Hospitalization.patient_id)
                    .outerjoin(Kennel, Kennel.id == Hospitalization.kennel_id)
                    .where(
                        Hospitalization.clinic_id == clinic.id,
                        Hospitalization.status == "active",
                    )
                )
            ).all()
        )

        pendentes = list(
            (
                await session.execute(
                    sa.select(Task).where(
                        Task.clinic_id == clinic.id,
                        Task.status == TaskStatus.pending,
                        Task.scheduled_for <= now + LOOKAHEAD,
                    )
                )
            ).scalars()
        )
        por_internacao: dict[uuid.UUID, list[Task]] = {}
        for task in pendentes:
            por_internacao.setdefault(task.hospitalization_id, []).append(task)

        # "Feitas hoje" precisa do dia da clínica: sem isso o painel dizia
        # "9 de 12 feitas" contando pendentes, e o número que a enfermaria lê de
        # relance estava invertido.
        dia_inicio, dia_fim = clinic_day_bounds(clinic, now)
        do_dia = list(
            (
                await session.execute(
                    sa.select(Task.hospitalization_id, Task.status).where(
                        Task.clinic_id == clinic.id,
                        Task.scheduled_for >= dia_inicio,
                        Task.scheduled_for < dia_fim,
                        Task.status != TaskStatus.cancelled,
                    )
                )
            ).all()
        )
        feitas: dict[uuid.UUID, int] = {}
        previstas: dict[uuid.UUID, int] = {}
        for hospitalization_id, status in do_dia:
            previstas[hospitalization_id] = previstas.get(hospitalization_id, 0) + 1
            if str(status) in ("done", "partial"):
                feitas[hospitalization_id] = feitas.get(hospitalization_id, 0) + 1

        sem_evolucao = set(
            await ProgressNoteService.overdue_hospitalizations(
                session, clinic_id=clinic.id, now=now
            )
        )
        horas_sem_nota: dict[uuid.UUID, int] = {}
        for hospitalization_id in sem_evolucao:
            horas = await ProgressNoteService.hours_since_last(
                session, hospitalization_id=hospitalization_id, now=now
            )
            hospitalizacao = next(
                (h for h, _, _, _ in internacoes if h.id == hospitalization_id), None
            )
            if horas is None and hospitalizacao is not None:
                horas = (now - hospitalizacao.admitted_at).total_seconds() / 3600
            horas_sem_nota[hospitalization_id] = int(horas or 0)

        linhas: list[dict] = []
        total_due = total_overdue = total_on_time = 0
        baldes = {"now": 0, "next_hour": 0, "later": 0}

        for hospitalization, patient, kennel_name, kennel_id in internacoes:
            fila = sorted(
                por_internacao.get(hospitalization.id, []), key=lambda item: item.scheduled_for
            )
            counters = {"on_time": 0, "due": 0, "overdue": 0}
            for task in fila:
                estado = TaskService.display_state(task, now)
                counters[estado] += 1
                baldes[BoardService.bucket(task, now)] += 1
            total_due += counters["due"]
            total_overdue += counters["overdue"]
            total_on_time += counters["on_time"]

            attention = BoardService._attention(
                fila, now, horas_sem_nota.get(hospitalization.id)
            )
            linhas.append(
                {
                    "hospitalization_id": hospitalization.id,
                    "patient_id": patient.id,
                    "patient_name": patient.name,
                    "species": patient.species,
                    "kennel_id": kennel_id,
                    "kennel_name": kennel_name,
                    "admitted_at": hospitalization.admitted_at,
                    "next_task": fila[0] if fila else None,
                    "counters": {
                        **counters,
                        "done_today": feitas.get(hospitalization.id, 0),
                        "planned_today": previstas.get(hospitalization.id, 0),
                    },
                    "critical_overdue": any(
                        str(task.criticality) == "critical"
                        and TaskService.display_state(task, now) == "overdue"
                        for task in fila
                    ),
                    "attention": attention,
                }
            )

        # Pior primeiro. Depois do motivo vem a magnitude, depois o horário da
        # próxima tarefa: dois pacientes igualmente atrasados se desempatam por
        # quem vence antes, nunca por ordem alfabética.
        def ordem(linha: dict) -> tuple:
            attention: Attention | None = linha["attention"]
            proxima: Task | None = linha["next_task"]
            return (
                -(attention.severity if attention else 0),
                -(attention.magnitude or 0 if attention else 0),
                proxima.scheduled_for if proxima else datetime.max.replace(tzinfo=UTC),
                linha["patient_name"].lower(),
            )

        linhas.sort(key=ordem)

        turnos = await BoardService.current_shifts(session, clinic_id=clinic.id, now=now)
        nomes: dict[uuid.UUID, str] = {}
        if turnos:
            from app.models.user import User

            rows = await session.execute(
                sa.select(Membership.id, User.name)
                .join(User, User.id == Membership.user_id)
                .where(Membership.id.in_([turno.membership_id for turno in turnos]))
            )
            nomes = {membership_id: name for membership_id, name in rows}

        return {
            # O relógio é do servidor: o painel mostrava a hora do dispositivo
            # ao lado de dados que podiam estar velhos, e lia-se como "atualizado
            # agora".
            "now": now,
            # O fuso da clínica viaja junto para o cliente formatar as horas no
            # relógio da parede da clínica, não no do aparelho de quem olha.
            "timezone": clinic.timezone,
            "shifts": [
                {
                    "id": turno.id,
                    "name": turno.name,
                    "starts_at": turno.starts_at,
                    "ends_at": turno.ends_at,
                    "membership_id": turno.membership_id,
                    "member_name": nomes.get(turno.membership_id),
                    "is_vet_responsible": turno.is_vet_responsible,
                    "is_mine": turno.membership_id == viewer_membership_id,
                }
                for turno in turnos
            ],
            "totals": {
                "patients": len(internacoes),
                "due": total_due,
                "overdue": total_overdue,
                "attention": sum(1 for linha in linhas if linha["attention"] is not None),
                "now": baldes["now"],
                "next_hour": baldes["next_hour"],
                "later": baldes["later"],
            },
            "rows": linhas,
        }
