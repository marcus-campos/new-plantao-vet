import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Clinic, Hospitalization, Prescription, Task, TaskStatus
from app.services.scheduling import SchedulingService

#: `details["dose"]` é texto de prescrição ("25 mg/kg", "0,5 mL"): número na
#: frente, unidade atrás. Só o que casa aqui entra na SOMA: dose que não dá
#: para ler vira contagem sem total, nunca um total inventado.
_DOSE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(.*?)\s*$")


@dataclass(frozen=True)
class DrugDoseCounter:
    """Quanto deste fármaco este paciente recebeu: o contador que a pesquisa
    (§5.8) aponta como dor explícita e não resolvida do concorrente, com 51% dos
    erros de medicação sendo dose errada."""

    drug: str
    #: Execuções contadas: `partial` conta como UMA administração (houve
    #: contato com o fármaco), mas entra na soma pela fração realmente dada.
    count_24h: int
    count_total: int
    #: `None` quando a dose prescrita não é legível como número, ou quando o
    #: mesmo fármaco foi prescrito em unidades diferentes ao longo da internação
    #: (titulação de mg para mg/kg): somar mg com mL seria um número falso.
    dose_sum_24h: Decimal | None
    dose_sum_total: Decimal | None
    dose_unit: str | None


class TaskService:
    @staticmethod
    async def materialize(
        session: AsyncSession, *, prescription: Prescription, clinic: Clinic, until: datetime
    ) -> int:
        """Grava a janela de tarefas da prescrição. Idempotente pelo índice único
        parcial (prescription_id, scheduled_for): o segundo INSERT não faz nada."""
        candidates = SchedulingService.generate(prescription, clinic, until)
        if not candidates:
            return 0
        rows = [
            {
                # O default=uuid4 do model só roda no INSERT via ORM; aqui o
                # INSERT é em massa, então o id é gerado agora.
                "id": task.id or uuid.uuid4(),
                "clinic_id": task.clinic_id,
                "hospitalization_id": task.hospitalization_id,
                "prescription_id": task.prescription_id,
                "title": task.title,
                "category": task.category,
                "scheduled_for": task.scheduled_for,
                "criticality": task.criticality,
                "tolerance_minutes": task.tolerance_minutes,
                "status": "pending",
                "price_minor": task.price_minor,
            }
            for task in candidates
        ]
        stmt = (
            insert(Task)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["prescription_id", "scheduled_for"],
                # O índice é PARCIAL: o Postgres exige repetir o predicado aqui.
                index_where=sa.text("prescription_id IS NOT NULL"),
            )
            .returning(Task.id)
        )
        created = list((await session.execute(stmt)).scalars())
        await session.flush()
        return len(created)

    @staticmethod
    async def cancel_pending(
        session: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        prescription_id: uuid.UUID | None = None,
        hospitalization_id: uuid.UUID | None = None,
    ) -> int:
        """Cancela TODA tarefa ainda pendente, futura ou já vencida.

        Uma dose vencida e não dada de um fármaco suspenso não pode continuar
        acionável: administrá-la depois da suspensão seria erro. Execuções
        (done/partial/not_done) nunca são tocadas: o passado do prontuário é
        imutável (ADR-0003)."""
        stmt = (
            sa.update(Task)
            .where(
                Task.clinic_id == clinic_id,
                Task.status == "pending",
            )
            .values(status="cancelled")
            .returning(Task.id)
        )
        if prescription_id is not None:
            stmt = stmt.where(Task.prescription_id == prescription_id)
        if hospitalization_id is not None:
            stmt = stmt.where(Task.hospitalization_id == hospitalization_id)
        return len(list((await session.execute(stmt)).scalars()))

    @staticmethod
    def display_state(task: Task, now: datetime) -> str:
        """'Atrasada' NUNCA é persistida (spec §2): é derivada na leitura, para
        que board e ficha jamais divirjam, o bug fatal do concorrente."""
        if task.status != TaskStatus.pending:
            return str(task.status)
        if now < task.scheduled_for:
            return "on_time"
        if now <= task.scheduled_for + timedelta(minutes=task.tolerance_minutes):
            return "due"
        return "overdue"

    @staticmethod
    def minutes_late(task: Task, now: datetime) -> int | None:
        """Quanto tempo passou da hora, em minutos. `None` quando ainda não venceu.

        O estado sozinho não diferencia uma dose 5 minutos atrasada de uma
        atrasada há 5 horas, e é a magnitude que decide qual paciente é
        atendido primeiro. Conta a partir de `scheduled_for`, não do fim da
        janela: a janela diz quando vira exceção, não quando a dose era devida.
        """
        if task.status != TaskStatus.pending or now <= task.scheduled_for:
            return None
        return int((now - task.scheduled_for).total_seconds() // 60)

    @staticmethod
    def default_window(clinic: Clinic, now: datetime) -> tuple[datetime, datetime]:
        """Um turno inteiro à frente e um para trás.

        À frente porque às 22h o plantonista precisa enxergar as âncoras de 02h e
        04h. Para trás porque atrasado é a coisa MAIS importante da fila: uma
        janela que começasse em `now` esconderia toda dose vencida, o erro
        clássico de eMAR que a pesquisa aponta (§4)."""
        return now - timedelta(hours=12), now + timedelta(hours=12)

    @staticmethod
    def queue_criteria(
        now: datetime, window_start: datetime, window_end: datetime
    ) -> sa.ColumnElement[bool]:
        """O que a fila do plantão mostra: a janela MAIS toda pendente vencida.

        Atrasada não expira. O painel conta toda pendente vencida sem limite
        inferior. Se a fila e a ficha cortassem em 12h ou 24h, a dose de
        anteontem sumiria da tela e continuaria no contador: a pessoa daria
        baixa em tudo que vê, o atraso não se moveria, e o sistema pareceria
        quebrado. Uma definição só, usada por fila e ficha."""
        return sa.or_(
            sa.and_(Task.scheduled_for >= window_start, Task.scheduled_for <= window_end),
            sa.and_(Task.status == "pending", Task.scheduled_for < now),
        )

    @staticmethod
    async def transition(
        session: AsyncSession, *, task_id: uuid.UUID, clinic_id: uuid.UUID, values: dict
    ) -> Task:
        """Transição ATÔMICA (brief): um único UPDATE condicional. Se outra pessoa
        baixou a tarefa primeiro, zero linhas voltam e o segundo recebe 409. Sem
        isso, painel e app registrariam a mesma dose duas vezes."""
        stmt = (
            sa.update(Task)
            .where(Task.id == task_id, Task.clinic_id == clinic_id, Task.status == "pending")
            .values(**values)
            .returning(Task)
        )
        task = (await session.execute(stmt)).scalar_one_or_none()
        if task is None:
            raise AppError("task_already_processed", 409)
        return task

    @staticmethod
    async def check_prn_guardrails(
        session: AsyncSession, *, prescription: Prescription, now: datetime
    ) -> dict | None:
        """Aviso, nunca bloqueio duro (pesquisa §4: fricção gera workaround que
        falsifica o registro). Quem decide é o profissional; o sistema audita."""
        if prescription.min_interval_minutes:
            ultima = await session.scalar(
                sa.select(sa.func.max(Task.executed_at)).where(
                    Task.prescription_id == prescription.id,
                    Task.status.in_(["done", "partial"]),
                )
            )
            if ultima is not None:
                minutos = (now - ultima).total_seconds() / 60
                if minutos < prescription.min_interval_minutes:
                    return {
                        "rule": "min_interval_minutes",
                        "required_minutes": prescription.min_interval_minutes,
                        "elapsed_minutes": int(minutos),
                    }
        if prescription.max_doses_24h:
            doses = await session.scalar(
                sa.select(sa.func.count())
                .select_from(Task)
                .where(
                    Task.prescription_id == prescription.id,
                    Task.status.in_(["done", "partial"]),
                    Task.executed_at >= now - timedelta(hours=24),
                )
            )
            if doses >= prescription.max_doses_24h:
                return {
                    "rule": "max_doses_24h",
                    "max": prescription.max_doses_24h,
                    "given": doses,
                }
        return None

    @staticmethod
    async def check_fasting_guardrail(
        session: AsyncSession, *, task: Task, clinic_id: uuid.UUID
    ) -> dict | None:
        """Alimentar um paciente em jejum: aviso, nunca bloqueio duro.

        O jejum é ordem clínica com hora marcada (pré-anestésico, vômito, pós-op)
        e a próxima refeição continuava sendo oferecida na fila como qualquer
        outra. Mas quem está ao lado do box pode ter motivo para alimentar assim
        mesmo, e um bloqueio duro produziria o workaround que falsifica o
        registro (pesquisa §4). Mesmo contrato de `check_prn_guardrails`."""
        if str(task.category) != "nutrition":
            return None
        hospitalization = await session.get(Hospitalization, task.hospitalization_id)
        if hospitalization is None or hospitalization.clinic_id != clinic_id:
            return None
        if hospitalization.fasting_since is None:
            return None
        return {
            "since": hospitalization.fasting_since.isoformat(),
            "reason": hospitalization.fasting_reason,
        }

    @staticmethod
    async def dose_counters(
        session: AsyncSession, *, hospitalization_id: uuid.UUID, now: datetime
    ) -> list[DrugDoseCounter]:
        """O acumulado por fármaco em 24h e na internação inteira.

        Agrega por `details["drug"]` normalizado, que existe na prescrição
        exatamente para isto (spec §2). Só `done` e `partial` contam: o que não
        foi administrado não entra em contador de dose, senão o número que o vet
        usa para decidir a próxima dose passa a incluir o que o animal recusou.
        """
        from app.services.charges import ChargeService

        rows = list(
            (
                await session.execute(
                    sa.select(Task, Prescription)
                    .join(Prescription, Prescription.id == Task.prescription_id)
                    .where(
                        Task.hospitalization_id == hospitalization_id,
                        Task.status.in_(["done", "partial"]),
                    )
                )
            ).all()
        )
        limite = now - timedelta(hours=24)
        acumulado: dict[str, dict] = {}
        for task, prescription in rows:
            drug = (prescription.details or {}).get("drug")
            if not isinstance(drug, str) or not drug.strip():
                continue
            # `details.drug` já é gravado normalizado; a defesa aqui evita que
            # "Dipirona " e "dipirona" virem duas linhas no contador.
            drug = drug.strip().lower()
            bucket = acumulado.setdefault(
                drug,
                {
                    "count_24h": 0,
                    "count_total": 0,
                    "sum_24h": Decimal(0),
                    "sum_total": Decimal(0),
                    "unit": None,
                    "readable": True,
                },
            )
            recente = task.executed_at is not None and task.executed_at >= limite
            bucket["count_total"] += 1
            if recente:
                bucket["count_24h"] += 1

            amount, unit = TaskService._parse_dose((prescription.details or {}).get("dose"))
            if amount is None:
                bucket["readable"] = False
                continue
            if bucket["unit"] is None:
                bucket["unit"] = unit
            elif bucket["unit"] != unit:
                bucket["readable"] = False
                continue
            # Fração da dose prescrita que foi de fato administrada: a mesma
            # convenção da conta, para que contador e extrato não divirjam.
            fracao = Decimal(1) if task.status == TaskStatus.done else (
                ChargeService.partial_quantity(task)
            )
            dada = amount * fracao
            bucket["sum_total"] += dada
            if recente:
                bucket["sum_24h"] += dada

        return [
            DrugDoseCounter(
                drug=drug,
                count_24h=bucket["count_24h"],
                count_total=bucket["count_total"],
                dose_sum_24h=bucket["sum_24h"] if bucket["readable"] else None,
                dose_sum_total=bucket["sum_total"] if bucket["readable"] else None,
                dose_unit=bucket["unit"] if bucket["readable"] else None,
            )
            for drug, bucket in sorted(acumulado.items())
        ]

    @staticmethod
    def _parse_dose(raw: object) -> tuple[Decimal | None, str | None]:
        if isinstance(raw, int | float) and not isinstance(raw, bool):
            return Decimal(str(raw)), ""
        if not isinstance(raw, str):
            return None, None
        match = _DOSE.match(raw)
        if match is None:
            return None, None
        # Vírgula decimal: a prescrição é digitada em pt-BR ("0,5 mL").
        return Decimal(match.group(1).replace(",", ".")), match.group(2)
