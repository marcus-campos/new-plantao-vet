import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Shift(Base):
    """Escala de plantão.

    Serve a três coisas ao mesmo tempo (spec §2): define QUEM recebe a passagem,
    permite detectar "turno trocou sem boletim" e é a evidência de conformidade
    na fiscalização (quem estava de plantão, com registro profissional)."""

    __tablename__ = "shifts"
    __table_args__ = (sa.Index("ix_shifts_clinic_starts_at", "clinic_id", "starts_at"),)

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    name: Mapped[str] = mapped_column(sa.Text)
    starts_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True))
    membership_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("memberships.id"), index=True)
    # O responsável técnico do turno; um turno pode ter vários escalados e apenas
    # um responsável. A marcação é por linha, não por tabela à parte.
    is_vet_responsible: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    closed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
