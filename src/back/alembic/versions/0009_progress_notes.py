"""progress_notes

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-31
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "progress_notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("hospitalization_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=False),
        # Autoria congelada na linha: o PDF de daqui a 5 anos não pode depender
        # do cadastro atual do profissional (perfil br + retenção).
        sa.Column("author_name", sa.Text(), nullable=False),
        sa.Column("author_license", sa.Text(), nullable=True),
        sa.Column("author_license_authority", sa.Text(), nullable=True),
        sa.Column("subjective", sa.Text(), nullable=True),
        sa.Column("findings", sa.Text(), nullable=True),
        sa.Column("assessment", sa.Text(), nullable=True),
        sa.Column("plan", sa.Text(), nullable=True),
        # Correção é adendo versionado (ADR-0003): aponta para a anterior,
        # nunca reescreve.
        sa.Column(
            "amends_progress_note_id",
            sa.Uuid(),
            sa.ForeignKey("progress_notes.id"),
            nullable=True,
        ),
        sa.Column("signed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        # Barreira de tenancy no banco.
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_progress_notes_hospitalization_tenant",
        ),
    )
    op.create_index("ix_progress_notes_clinic_id", "progress_notes", ["clinic_id"])
    op.create_index(
        "ix_progress_notes_hospitalization_id", "progress_notes", ["hospitalization_id"]
    )
    op.create_index(
        "ix_progress_notes_clinic_hospitalization_signed",
        "progress_notes",
        ["clinic_id", "hospitalization_id", sa.text("signed_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_progress_notes_clinic_hospitalization_signed", table_name="progress_notes")
    op.drop_index("ix_progress_notes_hospitalization_id", table_name="progress_notes")
    op.drop_index("ix_progress_notes_clinic_id", table_name="progress_notes")
    op.drop_table("progress_notes")
