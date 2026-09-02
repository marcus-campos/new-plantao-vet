import uuid
from datetime import UTC, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class HospitalizationStatus(StrEnum):
    active = "active"
    discharged = "discharged"
    died = "died"
    left_ama = "left_ama"


class ConsentStatus(StrEnum):
    consent_recorded = "consent_recorded"
    emergency_no_consent = "emergency_no_consent"


def _enum(enum_cls: type[StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Hospitalization(Base):
    __tablename__ = "hospitalizations"
    __table_args__ = (
        # Barreira de tenancy no banco: filhos apontam para (id, clinic_id).
        sa.UniqueConstraint("id", "clinic_id", name="uq_hospitalizations_id_clinic"),
        sa.Index("ix_hospitalizations_clinic_status", "clinic_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("patients.id"))
    kennel_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("kennels.id"), default=None)
    vet_membership_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("memberships.id"))
    status: Mapped[HospitalizationStatus] = mapped_column(
        _enum(HospitalizationStatus, "hospitalization_status"),
        default=HospitalizationStatus.active,
    )
    admitted_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
    ended_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    outcome_note: Mapped[str | None] = mapped_column(sa.Text, default=None)
    consent_status: Mapped[ConsentStatus] = mapped_column(_enum(ConsentStatus, "consent_status"))
    consent_reason: Mapped[str | None] = mapped_column(sa.Text, default=None)
    # Jejum é ESTADO da internação, não motivo de uma tarefa: sem ele, "em jejum
    # desde 22h" só existia como texto de uma não-realizada já passada, e a
    # próxima refeição continuava sendo oferecida como se nada tivesse mudado.
    fasting_since: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=None
    )
    fasting_reason: Mapped[str | None] = mapped_column(sa.Text, default=None)
