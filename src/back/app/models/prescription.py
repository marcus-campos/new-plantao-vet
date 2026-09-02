import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PrescriptionKind(StrEnum):
    recurring = "recurring"
    continuous = "continuous"
    prn = "prn"


class PrescriptionCategory(StrEnum):
    medication = "medication"
    fluids = "fluids"
    monitoring = "monitoring"
    nutrition = "nutrition"
    care = "care"
    procedure = "procedure"


class Criticality(StrEnum):
    normal = "normal"
    critical = "critical"


def _enum(enum_cls: type[StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Prescription(Base):
    __tablename__ = "prescriptions"
    __table_args__ = (
        sa.UniqueConstraint("id", "clinic_id", name="uq_prescriptions_id_clinic"),
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_prescriptions_hospitalization_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    hospitalization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    kind: Mapped[PrescriptionKind] = mapped_column(_enum(PrescriptionKind, "prescription_kind"))
    category: Mapped[PrescriptionCategory] = mapped_column(
        _enum(PrescriptionCategory, "prescription_category")
    )
    name: Mapped[str] = mapped_column(sa.Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # MINUTOS (achado clínico 1): UTI monitora a cada 15–60 min.
    frequency_minutes: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    duration_hours: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    criticality: Mapped[Criticality] = mapped_column(
        _enum(Criticality, "criticality"), default=Criticality.normal
    )
    tolerance_minutes: Mapped[int] = mapped_column(sa.Integer)
    first_dose_now: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    is_controlled: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    max_doses_24h: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    min_interval_minutes: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    # O preço é COPIADO, nunca referenciado para leitura: price_minor é o valor
    # congelado no momento da prescrição; price_list_item_id guarda só a origem.
    # Reajustar a tabela de preços não altera conta já lançada.
    price_minor: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    # Sem sa.ForeignKey no ORM de propósito: price_list_items não é importado
    # pelo __init__ de app.models, e a FK só existe no banco (migração 0008).
    price_list_item_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, default=None)
    starts_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    replaces_prescription_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("prescriptions.id"), default=None
    )
    suspended_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    suspended_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
