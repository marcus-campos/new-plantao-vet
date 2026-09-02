"""prescriptions

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prescriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("hospitalization_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        # MINUTOS: monitoramento de UTI a cada 15–30 min precisa caber aqui.
        sa.Column("frequency_minutes", sa.Integer(), nullable=True),
        sa.Column("duration_hours", sa.Integer(), nullable=True),
        sa.Column("criticality", sa.Text(), nullable=False, server_default="normal"),
        sa.Column("tolerance_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "first_dose_now", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_controlled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("max_doses_24h", sa.Integer(), nullable=True),
        sa.Column("min_interval_minutes", sa.Integer(), nullable=True),
        sa.Column("price_minor", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ends_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "replaces_prescription_id",
            sa.Uuid(),
            sa.ForeignKey("prescriptions.id"),
            nullable=True,
        ),
        sa.Column("suspended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("suspended_by", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=True),
        sa.CheckConstraint(
            "kind IN ('recurring', 'continuous', 'prn')", name="ck_prescriptions_kind"
        ),
        sa.CheckConstraint(
            "category IN ('medication', 'fluids', 'monitoring', 'nutrition', 'care', 'procedure')",
            name="ck_prescriptions_category",
        ),
        sa.CheckConstraint(
            "criticality IN ('normal', 'critical')", name="ck_prescriptions_criticality"
        ),
        sa.UniqueConstraint("id", "clinic_id", name="uq_prescriptions_id_clinic"),
        # Barreira de tenancy no banco: a internação tem de ser da MESMA clínica.
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_prescriptions_hospitalization_tenant",
        ),
    )
    op.create_index("ix_prescriptions_clinic_id", "prescriptions", ["clinic_id"])
    op.create_index("ix_prescriptions_hospitalization_id", "prescriptions", ["hospitalization_id"])


def downgrade() -> None:
    op.drop_index("ix_prescriptions_hospitalization_id", table_name="prescriptions")
    op.drop_index("ix_prescriptions_clinic_id", table_name="prescriptions")
    op.drop_table("prescriptions")
