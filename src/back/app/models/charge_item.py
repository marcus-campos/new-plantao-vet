import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.prescription import _enum

# Importado para registrar `price_list_items` no metadata antes que o mapper
# resolva a FK abaixo (o __init__ de app.models ainda não conhece a tabela).
from app.models.price_list_item import PriceListItem  # noqa: F401


class ChargeSource(StrEnum):
    task_execution = "task_execution"
    daily_rate = "daily_rate"
    manual = "manual"


class ChargeItem(Base):
    """Linha da conta da internação. Valor CONGELADO no lançamento."""

    __tablename__ = "charge_items"
    __table_args__ = (
        sa.Index("ix_charge_items_clinic_hospitalization", "clinic_id", "hospitalization_id"),
        # Idempotência da diária: um lançamento por dia de internação, garantido
        # pelo banco — o job pode rodar quantas vezes quiser no mesmo dia.
        sa.Index(
            "uq_charge_items_daily_accrual",
            "hospitalization_id",
            "accrual_date",
            unique=True,
            postgresql_where=sa.text("accrual_date IS NOT NULL"),
        ),
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_charge_items_hospitalization_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    hospitalization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("tasks.id"), default=None)
    price_list_item_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("price_list_items.id"), default=None
    )
    description: Mapped[str] = mapped_column(sa.Text)
    quantity: Mapped[Decimal] = mapped_column(sa.Numeric(6, 2), default=Decimal("1"))
    unit_price_minor: Mapped[int] = mapped_column(sa.Integer)
    total_minor: Mapped[int] = mapped_column(sa.Integer)
    charged_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
    # Só a diária preenche: é a chave da idempotência por dia (data LOCAL da clínica).
    accrual_date: Mapped[date | None] = mapped_column(sa.Date, default=None)
    source: Mapped[ChargeSource] = mapped_column(_enum(ChargeSource, "charge_source"))
