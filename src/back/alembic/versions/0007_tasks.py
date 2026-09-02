"""tasks

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column(
            "hospitalization_id",
            sa.Uuid(),
            sa.ForeignKey("hospitalizations.id"),
            nullable=False,
        ),
        sa.Column("prescription_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("criticality", sa.Text(), nullable=False),
        sa.Column("tolerance_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("executed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("executed_by", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=True),
        sa.Column("retroactive", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("early", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("outcome_reason", sa.Text(), nullable=True),
        sa.Column("values", postgresql.JSONB(), nullable=True),
        sa.Column("price_minor", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'partial', 'not_done', 'cancelled')",
            name="ck_tasks_status",
        ),
        sa.CheckConstraint(
            "criticality IN ('normal', 'critical')", name="ck_tasks_criticality"
        ),
        sa.ForeignKeyConstraint(
            ["prescription_id", "clinic_id"],
            ["prescriptions.id", "prescriptions.clinic_id"],
            name="fk_tasks_prescription_tenant",
        ),
    )
    op.create_index("ix_tasks_clinic_id", "tasks", ["clinic_id"])
    op.create_index("ix_tasks_hospitalization_id", "tasks", ["hospitalization_id"])
    op.create_index(
        "ix_tasks_clinic_status_scheduled", "tasks", ["clinic_id", "status", "scheduled_for"]
    )
    # Idempotência do aprazamento: resolve no banco a corrida entre a criação da
    # prescrição e o job de 48h (achado de engenharia 2 da revisão adversarial).
    op.create_index(
        "uq_tasks_prescription_scheduled",
        "tasks",
        ["prescription_id", "scheduled_for"],
        unique=True,
        postgresql_where=sa.text("prescription_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_tasks_prescription_scheduled", table_name="tasks")
    op.drop_index("ix_tasks_clinic_status_scheduled", table_name="tasks")
    op.drop_index("ix_tasks_hospitalization_id", table_name="tasks")
    op.drop_index("ix_tasks_clinic_id", table_name="tasks")
    op.drop_table("tasks")
