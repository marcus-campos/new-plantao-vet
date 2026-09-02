import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class HandoverReport(Base):
    """Boletim de passagem, um por paciente internado.

    Duas camadas com garantias diferentes: `skeleton` é DETERMINÍSTICO (contado
    do banco, sempre verdadeiro, nunca depende de IA) e `narrative` é o resumo
    redigido a partir das notas. Se a narrativa faltar ou estiver errada, o
    esqueleto sozinho já entrega a passagem.

    `reviewed_at` NULL não bloqueia e não esconde nada: o plantão seguinte lê o
    boletim inteiro com o selo "não revisado" e a omissão fica auditada."""

    __tablename__ = "handover_reports"
    __table_args__ = (
        sa.UniqueConstraint("id", "clinic_id", name="uq_handover_reports_id_clinic"),
        sa.Index("ix_handover_reports_clinic_from_shift", "clinic_id", "from_shift_id"),
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_handover_reports_hospitalization_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    hospitalization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    from_shift_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("shifts.id"), default=None
    )
    to_shift_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("shifts.id"), default=None)
    skeleton: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    narrative: Mapped[str | None] = mapped_column(sa.Text, default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
