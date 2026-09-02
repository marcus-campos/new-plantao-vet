"""owner_contacts

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-31
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_contacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("hospitalization_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("owners.id"), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False, server_default="outbound"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("read_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("membership_id", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=True),
        sa.Column("author_name", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint(
            "channel IN ('phone', 'whatsapp', 'in_person')", name="ck_owner_contacts_channel"
        ),
        sa.CheckConstraint(
            "direction IN ('outbound', 'inbound')", name="ck_owner_contacts_direction"
        ),
        # Barreira de tenancy no banco: o filho aponta para (id, clinic_id)
        # da internação, então nenhuma linha cruza clínicas nem por bug.
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_owner_contacts_hospitalization_tenant",
        ),
    )
    op.create_index("ix_owner_contacts_clinic_id", "owner_contacts", ["clinic_id"])
    op.create_index(
        "ix_owner_contacts_hospitalization_sent",
        "owner_contacts",
        ["hospitalization_id", "sent_at"],
    )
    # O external_id (wamid) é a chave do webhook de status da Meta Cloud API:
    # é por ele que delivered_at/read_at acham a linha.
    op.create_index(
        "ix_owner_contacts_external_id",
        "owner_contacts",
        ["external_id"],
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_owner_contacts_external_id", table_name="owner_contacts")
    op.drop_index("ix_owner_contacts_hospitalization_sent", table_name="owner_contacts")
    op.drop_index("ix_owner_contacts_clinic_id", table_name="owner_contacts")
    op.drop_table("owner_contacts")
