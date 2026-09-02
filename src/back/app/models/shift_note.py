import uuid
from datetime import UTC, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ShiftNoteSource(StrEnum):
    typed = "typed"
    audio = "audio"


def _enum(enum_cls: type[StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda e: [m.value for m in e],
    )


class ShiftNote(Base):
    """Nota de plantão: o insumo humano do boletim.

    LGPD (spec §2): quando `source == audio`, o áudio bruto NUNCA é armazenado,
    nem aqui, nem em bucket, nem em coluna binária. O cliente transcreve, o
    profissional confirma o texto e SÓ a transcrição chega ao servidor e ao
    prontuário. Voz de funcionário é dado pessoal, e o prontuário é
    append-only: guardar o áudio o tornaria inapagável para sempre."""

    __tablename__ = "shift_notes"
    __table_args__ = (
        sa.Index("ix_shift_notes_clinic_hospitalization", "clinic_id", "hospitalization_id"),
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_shift_notes_hospitalization_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    hospitalization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    shift_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("shifts.id"), default=None)
    membership_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    # Nome copiado no ato: a nota é registro de prontuário e não pode mudar de
    # autor porque a pessoa trocou de nome ou saiu da clínica depois.
    author_name: Mapped[str] = mapped_column(sa.Text)
    text: Mapped[str] = mapped_column(sa.Text)
    source: Mapped[ShiftNoteSource] = mapped_column(
        _enum(ShiftNoteSource, "shift_note_source"), default=ShiftNoteSource.typed
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
