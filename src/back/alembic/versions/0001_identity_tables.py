"""identity tables

Revision ID: 0001
Revises:
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clinics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("locale", sa.Text(), nullable=False, server_default="pt-BR"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("unit_system", sa.Text(), nullable=False, server_default="metric"),
        sa.Column("compliance_profile", sa.Text(), nullable=False, server_default="br"),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="America/Sao_Paulo"),
        sa.Column("anchors", postgresql.JSONB(), nullable=False),
        sa.Column("default_prescriptions", postgresql.JSONB(), nullable=False),
        sa.Column("plan_tier", sa.Text(), nullable=True),
        sa.Column("bed_limit", sa.Integer(), nullable=True),
        sa.Column("station_key_hash", sa.Text(), nullable=True),
        sa.Column("station_key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("unit_system IN ('metric', 'imperial')", name="ck_clinics_unit_system"),
        sa.UniqueConstraint("slug", name="uq_clinics_slug"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("locale", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("license_number", sa.Text(), nullable=True),
        sa.Column("license_authority", sa.Text(), nullable=True),
        sa.Column("pin_hash", sa.Text(), nullable=True),
        sa.Column(
            "permissions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint("role IN ('vet', 'tech', 'admin')", name="ck_memberships_role"),
        sa.UniqueConstraint("clinic_id", "user_id", name="uq_memberships_clinic_id_user_id"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("clinics")
