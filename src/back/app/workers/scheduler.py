from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.models import Clinic, Hospitalization, Prescription
from app.services.charges import ChargeService
from app.services.push import PushService
from app.services.scheduling import HORIZON_HOURS
from app.services.tasks import TaskService


async def accrue_open_stays(session_factory, *, now: datetime) -> int:
    """Lança a diária de cada internação ATIVA.

    `ChargeService.accrue_daily_rates` estava completo, idempotente pelo índice
    único parcial `(hospitalization_id, accrual_date)`, ciente da área do box,
    e era chamado só pelos testes. A diária é a maior linha da conta e nunca
    entrava: `PriceListItem.is_daily_rate`, `kennel_area` e a migração 0008
    existiam para ninguém.

    Idempotente de propósito: rodar de hora em hora não duplica nada, e uma
    instância que ficou fora do ar alcança os dias que perdeu."""
    lancados = 0
    async with session_factory() as session:
        stmt = sa.select(Hospitalization).where(
            Hospitalization.status == "active",
        )
        for hospitalization in list((await session.execute(stmt)).scalars()):
            clinic = await session.get(Clinic, hospitalization.clinic_id)
            if clinic is None:
                continue
            lancados += await ChargeService.accrue_daily_rates(
                session, hospitalization=hospitalization, clinic=clinic, now=now
            )
        await session.commit()
    return lancados


async def extend_scheduling_window(
    session_factory, *, now: datetime, horizon_hours: int = HORIZON_HOURS
) -> int:
    """Mantém a janela rolante de tarefas. Não existe verificador de atraso:
    'atrasada' é computada na leitura (TaskService.display_state)."""
    until = now + timedelta(hours=horizon_hours)
    created = 0
    async with session_factory() as session:
        stmt = (
            sa.select(Prescription.id)
            .join(Hospitalization, Hospitalization.id == Prescription.hospitalization_id)
            .where(
                Prescription.suspended_at.is_(None),
                Prescription.kind != "prn",
                sa.or_(Prescription.ends_at.is_(None), Prescription.ends_at > now),
                Hospitalization.status == "active",
            )
        )
        for prescription_id in list((await session.execute(stmt)).scalars()):
            # FOR UPDATE serializa com POST /prescriptions/{id}/suspend (Task 12):
            # sem isso, o job pode ressuscitar tarefas de uma prescrição suspensa.
            locked = await session.scalar(
                sa.select(Prescription).where(Prescription.id == prescription_id).with_for_update()
            )
            if locked is None or locked.suspended_at is not None:
                continue
            hospitalization = await session.get(Hospitalization, locked.hospitalization_id)
            if hospitalization is None or hospitalization.status != "active":
                continue
            clinic = await session.get(Clinic, locked.clinic_id)
            created += await TaskService.materialize(
                session, prescription=locked, clinic=clinic, until=until
            )
        await session.commit()
    return created


async def hourly(session_factory, *, now: datetime) -> dict[str, int]:
    """As duas coisas que precisam acontecer sozinhas, de hora em hora."""
    return {
        "tasks": await extend_scheduling_window(session_factory, now=now),
        "daily_rates": await accrue_open_stays(session_factory, now=now),
    }


def build_scheduler(session_factory) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    # Dose crítica fora da janela é um dos DOIS únicos motivos de notificação
    # ativa do sistema (o outro é a intercorrência com "avisar o veterinário").
    # A cada 5 min porque a janela ISMP de uma dose time-critical é de ±30 min.
    # De hora em hora o aviso chegaria depois de já não servir. O dedupe garante
    # um push por dose, uma vez só.
    scheduler.add_job(
        lambda: PushService.sweep_all_clinics(session_factory, now=datetime.now(UTC)),
        trigger="interval",
        minutes=5,
        id="push_critical_overdue",
        max_instances=1,
    )
    scheduler.add_job(
        lambda: hourly(session_factory, now=datetime.now(UTC)),
        trigger="interval",
        hours=1,
        id="extend_scheduling_window",
        max_instances=1,
    )
    return scheduler
