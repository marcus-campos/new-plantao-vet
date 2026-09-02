"""hospitalizations

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-31
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hospitalizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("kennel_id", sa.Uuid(), sa.ForeignKey("kennels.id"), nullable=True),
        sa.Column(
            "vet_membership_id", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=False
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("admitted_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column("consent_status", sa.Text(), nullable=False),
        sa.Column("consent_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'discharged', 'died', 'left_ama')",
            name="ck_hospitalizations_status",
        ),
        sa.CheckConstraint(
            "consent_status IN ('consent_recorded', 'emergency_no_consent')",
            name="ck_hospitalizations_consent_status",
        ),
        # Barreira de tenancy: prescriptions/tasks referenciam (id, clinic_id).
        sa.UniqueConstraint("id", "clinic_id", name="uq_hospitalizations_id_clinic"),
    )
    op.create_index("ix_hospitalizations_clinic_id", "hospitalizations", ["clinic_id"])
    op.create_index(
        "ix_hospitalizations_clinic_status", "hospitalizations", ["clinic_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_hospitalizations_clinic_status", table_name="hospitalizations")
    op.drop_index("ix_hospitalizations_clinic_id", table_name="hospitalizations")
    op.drop_table("hospitalizations")
