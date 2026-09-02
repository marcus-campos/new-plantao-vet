import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ProgressNote(Base):
    """Evolução diária (CFMV Res. 1321/2020 Art. 2º VIII).

    IMUTÁVEL depois de assinada: não existe PATCH. Correção é uma NOVA evolução
    apontando para a anterior em `amends_progress_note_id` — o adendo versionado
    do ADR-0003, nunca edição destrutiva.
    """

    __tablename__ = "progress_notes"
    __table_args__ = (
        # Leitura do prontuário e o alerta de 24h são sempre por internação,
        # do mais recente para o mais antigo.
        sa.Index(
            "ix_progress_notes_clinic_hospitalization_signed",
            "clinic_id",
            "hospitalization_id",
            sa.text("signed_at DESC"),
        ),
        # Barreira de tenancy no banco (mesmo padrão de prescriptions/tasks).
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_progress_notes_hospitalization_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    hospitalization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("memberships.id"))
    # Nome e registro são COPIADOS no ato da assinatura: o prontuário exportado
    # cinco anos depois não pode depender do cadastro atual do profissional.
    author_name: Mapped[str] = mapped_column(sa.Text)
    author_license: Mapped[str | None] = mapped_column(sa.Text, default=None)
    author_license_authority: Mapped[str | None] = mapped_column(sa.Text, default=None)
    subjective: Mapped[str | None] = mapped_column(sa.Text, default=None)
    findings: Mapped[str | None] = mapped_column(sa.Text, default=None)
    assessment: Mapped[str | None] = mapped_column(sa.Text, default=None)
    plan: Mapped[str | None] = mapped_column(sa.Text, default=None)
    amends_progress_note_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("progress_notes.id"), default=None
    )
    signed_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
