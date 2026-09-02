import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class HandoverAck(Base):
    """Aceite explícito do receptor, paciente a paciente.

    `seconds_to_ack` mede o intervalo entre abrir o boletim e aceitá-lo. Não é
    métrica de vaidade: aceite em 2 segundos, paciente após paciente, é o
    "carimbo em série" — a assinatura de que ninguém leu nada. O número fica no
    registro para que a clínica enxergue o padrão, nunca para bloquear."""

    __tablename__ = "handover_acks"
    __table_args__ = (
        sa.Index("ix_handover_acks_clinic_report", "clinic_id", "handover_report_id"),
        sa.ForeignKeyConstraint(
            ["handover_report_id", "clinic_id"],
            ["handover_reports.id", "handover_reports.clinic_id"],
            name="fk_handover_acks_report_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    handover_report_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    membership_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    acked_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
    seconds_to_ack: Mapped[int | None] = mapped_column(sa.Integer, default=None)
