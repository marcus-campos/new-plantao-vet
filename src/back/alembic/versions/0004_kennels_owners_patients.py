"""kennels owners patients

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-31
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kennels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("area", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_kennels_clinic_id", "kennels", ["clinic_id"])

    op.create_table(
        "owners",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("tax_id", sa.Text(), nullable=True),
        sa.Column("whatsapp_opt_in_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_owners_clinic_id", "owners", ["clinic_id"])

    op.create_table(
        "patients",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("owners.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("species", sa.Text(), nullable=False),
        sa.Column("breed", sa.Text(), nullable=True),
        # SEMPRE em kg (SI); a exibição em lb é do cliente (ADR-0004).
        sa.Column("weight_kg", sa.Numeric(6, 3), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_patients_clinic_id", "patients", ["clinic_id"])


def downgrade() -> None:
    op.drop_index("ix_patients_clinic_id", table_name="patients")
    op.drop_table("patients")
    op.drop_index("ix_owners_clinic_id", table_name="owners")
    op.drop_table("owners")
    op.drop_index("ix_kennels_clinic_id", table_name="kennels")
    op.drop_table("kennels")
