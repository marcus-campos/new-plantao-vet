"""shifts, shift notes and handover

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shifts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ends_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("membership_id", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=False),
        sa.Column(
            "is_vet_responsible", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_shifts_clinic_id", "shifts", ["clinic_id"])
    op.create_index("ix_shifts_membership_id", "shifts", ["membership_id"])
    op.create_index("ix_shifts_clinic_starts_at", "shifts", ["clinic_id", "starts_at"])

    op.create_table(
        "shift_notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("hospitalization_id", sa.Uuid(), nullable=False),
        sa.Column("shift_id", sa.Uuid(), sa.ForeignKey("shifts.id"), nullable=True),
        sa.Column("membership_id", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=True),
        sa.Column("author_name", sa.Text(), nullable=False),
        # Só a TRANSCRIÇÃO. O áudio bruto não tem coluna aqui e não é armazenado
        # em lugar nenhum (LGPD): o prontuário é append-only, e guardar a voz do
        # funcionário a tornaria inapagável para sempre.
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="typed"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('typed', 'audio')", name="ck_shift_notes_source"),
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_shift_notes_hospitalization_tenant",
        ),
    )
    op.create_index("ix_shift_notes_clinic_id", "shift_notes", ["clinic_id"])
    op.create_index("ix_shift_notes_hospitalization_id", "shift_notes", ["hospitalization_id"])
    op.create_index(
        "ix_shift_notes_clinic_hospitalization",
        "shift_notes",
        ["clinic_id", "hospitalization_id"],
    )

    op.create_table(
        "handover_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("hospitalization_id", sa.Uuid(), nullable=False),
        sa.Column("from_shift_id", sa.Uuid(), sa.ForeignKey("shifts.id"), nullable=True),
        sa.Column("to_shift_id", sa.Uuid(), sa.ForeignKey("shifts.id"), nullable=True),
        sa.Column(
            "skeleton", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("narrative", sa.Text(), nullable=True),
        # NULL = não revisado. É estado legítimo e visível, nunca um bloqueio.
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint("id", "clinic_id", name="uq_handover_reports_id_clinic"),
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_handover_reports_hospitalization_tenant",
        ),
    )
    op.create_index("ix_handover_reports_clinic_id", "handover_reports", ["clinic_id"])
    op.create_index(
        "ix_handover_reports_hospitalization_id", "handover_reports", ["hospitalization_id"]
    )
    op.create_index(
        "ix_handover_reports_clinic_from_shift",
        "handover_reports",
        ["clinic_id", "from_shift_id"],
    )

    op.create_table(
        "handover_acks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("handover_report_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=True),
        sa.Column("acked_at", sa.TIMESTAMP(timezone=True), nullable=False),
        # Termômetro de "carimbo em série": medido, nunca usado para bloquear.
        sa.Column("seconds_to_ack", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["handover_report_id", "clinic_id"],
            ["handover_reports.id", "handover_reports.clinic_id"],
            name="fk_handover_acks_report_tenant",
        ),
    )
    op.create_index("ix_handover_acks_clinic_id", "handover_acks", ["clinic_id"])
    op.create_index("ix_handover_acks_handover_report_id", "handover_acks", ["handover_report_id"])
    op.create_index(
        "ix_handover_acks_clinic_report", "handover_acks", ["clinic_id", "handover_report_id"]
    )


def downgrade() -> None:
    op.drop_table("handover_acks")
    op.drop_table("handover_reports")
    op.drop_table("shift_notes")
    op.drop_table("shifts")
