"""audit entries

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column(
            "actor_membership_id",
            sa.Uuid(),
            sa.ForeignKey("memberships.id"),
            nullable=True,
        ),
        sa.Column("actor_name", sa.Text(), nullable=False),
        sa.Column("actor_license", sa.Text(), nullable=True),
        sa.Column("actor_license_authority", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("prev_hash", sa.Text(), nullable=False),
        sa.Column("entry_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    # Índices da spec §4: paginação por cursor (id DESC) e busca por entidade.
    op.create_index(
        "ix_audit_entries_clinic_id_id_desc",
        "audit_entries",
        ["clinic_id", sa.text("id DESC")],
    )
    op.create_index(
        "ix_audit_entries_clinic_entity",
        "audit_entries",
        ["clinic_id", "entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_entries_clinic_entity", table_name="audit_entries")
    op.drop_index("ix_audit_entries_clinic_id_id_desc", table_name="audit_entries")
    op.drop_table("audit_entries")
