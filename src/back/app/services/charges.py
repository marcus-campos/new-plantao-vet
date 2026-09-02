import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.charge_item import ChargeItem, ChargeSource
from app.models.clinic import Clinic
from app.models.hospitalization import Hospitalization
from app.models.kennel import Kennel
from app.models.prescription import Prescription
from app.models.price_list_item import PriceListItem
from app.models.task import Task
from app.services.audit import ActorInfo, AuditService

# Execução parcial sem número utilizável: metade da dose é a convenção da spec.
HALF = Decimal("0.5")
ONE = Decimal("1")


class ChargeService:
    @staticmethod
    def total_minor(unit_price_minor: int, quantity: Decimal) -> int:
        """Dinheiro é inteiro na unidade menor: a fração vira centavo inteiro aqui,
        uma única vez, no lançamento."""
        return int(
            (Decimal(unit_price_minor) * quantity).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )

    @staticmethod
    def partial_quantity(task: Task) -> Decimal:
        """`dose_given` é a FRAÇÃO da dose prescrita que foi administrada.

        Qualquer coisa fora de (0, 1] (texto, zero, número maior que a dose
        inteira) cai na metade: cobrar mais que a dose prescrita por causa de um
        campo livre seria pior que arredondar."""
        raw: Any = (task.values or {}).get("dose_given")
        if raw is None or isinstance(raw, bool):
            return HALF
        try:
            quantity = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return HALF
        if quantity <= 0 or quantity > ONE:
            return HALF
        return quantity

    @staticmethod
    async def record_execution(
        session: AsyncSession,
        *,
        task: Task,
        hospitalization_id: uuid.UUID,
        clinic_id: uuid.UUID,
        actor: ActorInfo | None = None,
    ) -> ChargeItem | None:
        """Lança na conta o que foi de fato executado.

        O preço vem da TAREFA (cópia da prescrição, que é cópia do catálogo):
        nenhuma leitura da tabela de preços entra aqui. Sem preço na tarefa não
        há lançamento: cerimônia e monitoramento não custam."""
        if task.price_minor is None:
            return None
        if task.status == "done":
            quantity = ONE
        elif task.status == "partial":
            quantity = ChargeService.partial_quantity(task)
        else:
            # not_done / cancelled / pending: nada executado, nada cobrado.
            return None

        price_list_item_id = None
        if task.prescription_id is not None:
            prescription = await session.get(Prescription, task.prescription_id)
            if prescription is not None:
                price_list_item_id = prescription.price_list_item_id

        item = ChargeItem(
            clinic_id=clinic_id,
            hospitalization_id=hospitalization_id,
            task_id=task.id,
            price_list_item_id=price_list_item_id,
            description=task.title,
            quantity=quantity,
            unit_price_minor=task.price_minor,
            total_minor=ChargeService.total_minor(task.price_minor, quantity),
            charged_at=task.executed_at or datetime.now(UTC),
            source=ChargeSource.task_execution,
        )
        session.add(item)
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=clinic_id,
            actor=actor,
            action="charge_recorded",
            entity_type="charge_item",
            entity_id=item.id,
            after=AuditService.snapshot(item),
            extra={"task_id": str(task.id), "task_status": str(task.status)},
        )
        return item

    @staticmethod
    async def daily_rate_item(
        session: AsyncSession, *, clinic_id: uuid.UUID, area: str | None
    ) -> PriceListItem | None:
        """Diária da área do box; sem item para a área, cai na diária genérica
        (kennel_area nulo). Clínica sem diária cadastrada simplesmente não lança."""
        base = sa.select(PriceListItem).where(
            PriceListItem.clinic_id == clinic_id,
            PriceListItem.is_daily_rate.is_(True),
            PriceListItem.is_active.is_(True),
        )
        if area is not None:
            item = (
                await session.execute(base.where(PriceListItem.kennel_area == area))
            ).scalars().first()
            if item is not None:
                return item
        return (
            (await session.execute(base.where(PriceListItem.kennel_area.is_(None))))
            .scalars()
            .first()
        )

    @staticmethod
    async def accrue_daily_rates(
        session: AsyncSession, *, hospitalization: Hospitalization, clinic: Clinic, now: datetime
    ) -> int:
        """Lança a diária de cada dia de internação (data LOCAL da clínica).

        Idempotente pelo índice único parcial (hospitalization_id, accrual_date):
        rodar duas vezes no mesmo dia não duplica nada."""
        area = None
        if hospitalization.kennel_id is not None:
            kennel = await session.get(Kennel, hospitalization.kennel_id)
            area = kennel.area if kennel is not None else None
        item = await ChargeService.daily_rate_item(session, clinic_id=clinic.id, area=area)
        if item is None:
            return 0

        tz = ZoneInfo(clinic.timezone)
        last_moment = min(now, hospitalization.ended_at or now)
        first_day = hospitalization.admitted_at.astimezone(tz).date()
        last_day = last_moment.astimezone(tz).date()
        if last_day < first_day:
            return 0

        rows = []
        day = first_day
        while day <= last_day:
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "clinic_id": clinic.id,
                    "hospitalization_id": hospitalization.id,
                    "task_id": None,
                    "price_list_item_id": item.id,
                    "description": item.name,
                    "quantity": ONE,
                    "unit_price_minor": item.price_minor,
                    "total_minor": item.price_minor,
                    "charged_at": ChargeService._day_start(day, tz),
                    "accrual_date": day,
                    "source": ChargeSource.daily_rate.value,
                }
            )
            day += timedelta(days=1)

        stmt = (
            insert(ChargeItem)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["hospitalization_id", "accrual_date"],
                # O índice é PARCIAL: o Postgres exige repetir o predicado aqui.
                index_where=sa.text("accrual_date IS NOT NULL"),
            )
            .returning(ChargeItem.id)
        )
        created = list((await session.execute(stmt)).scalars())
        await session.flush()
        return len(created)

    @staticmethod
    def _day_start(day: date, tz: ZoneInfo) -> datetime:
        return datetime.combine(day, time(0, 0), tzinfo=tz).astimezone(UTC)

    @staticmethod
    async def statement(
        session: AsyncSession, *, hospitalization_id: uuid.UUID, clinic_id: uuid.UUID
    ) -> dict:
        """Extrato: total e itens agrupados pelo dia LOCAL da clínica."""
        clinic = await session.get(Clinic, clinic_id)
        tz = ZoneInfo(clinic.timezone) if clinic is not None else UTC
        rows = list(
            (
                await session.execute(
                    sa.select(ChargeItem)
                    .where(
                        ChargeItem.clinic_id == clinic_id,
                        ChargeItem.hospitalization_id == hospitalization_id,
                    )
                    .order_by(ChargeItem.charged_at.asc(), ChargeItem.id.asc())
                )
            ).scalars()
        )
        days: dict[date, list[ChargeItem]] = {}
        for row in rows:
            days.setdefault(row.charged_at.astimezone(tz).date(), []).append(row)
        return {
            "hospitalization_id": hospitalization_id,
            "currency": clinic.currency if clinic is not None else "BRL",
            "total_minor": sum(row.total_minor for row in rows),
            "days": [
                {
                    "date": day,
                    "total_minor": sum(row.total_minor for row in items),
                    "items": items,
                }
                for day, items in sorted(days.items())
            ],
        }
