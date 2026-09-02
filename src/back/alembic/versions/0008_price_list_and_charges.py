"""price list and charges

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-31
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

CATEGORIES = "'medication', 'fluids', 'monitoring', 'nutrition', 'care', 'procedure'"


def upgrade() -> None:
    op.create_table(
        "price_list_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("is_daily_rate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("kennel_area", sa.Text(), nullable=True),
        sa.Column("is_controlled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint(
            f"category IN ({CATEGORIES})", name="ck_price_list_items_category"
        ),
        # Barreira de tenancy no banco: filhos apontam para (id, clinic_id).
        sa.UniqueConstraint("id", "clinic_id", name="uq_price_list_items_id_clinic"),
    )
    op.create_index("ix_price_list_items_clinic_id", "price_list_items", ["clinic_id"])
    op.create_index(
        "ix_price_list_items_clinic_active", "price_list_items", ["clinic_id", "is_active"]
    )

    op.create_table(
        "charge_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("hospitalization_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column(
            "price_list_item_id",
            sa.Uuid(),
            sa.ForeignKey("price_list_items.id"),
            nullable=True,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(6, 2), nullable=False, server_default=sa.text("1")),
        sa.Column("unit_price_minor", sa.Integer(), nullable=False),
        sa.Column("total_minor", sa.Integer(), nullable=False),
        sa.Column("charged_at", sa.TIMESTAMP(timezone=True), nullable=False),
        # Só a diária preenche: é a chave da idempotência por dia.
        sa.Column("accrual_date", sa.Date(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "source IN ('task_execution', 'daily_rate', 'manual')",
            name="ck_charge_items_source",
        ),
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_charge_items_hospitalization_tenant",
        ),
    )
    op.create_index("ix_charge_items_clinic_id", "charge_items", ["clinic_id"])
    op.create_index("ix_charge_items_hospitalization_id", "charge_items", ["hospitalization_id"])
    op.create_index(
        "ix_charge_items_clinic_hospitalization",
        "charge_items",
        ["clinic_id", "hospitalization_id"],
    )
    # Diária lançada UMA vez por dia de internação: idempotência no banco.
    op.create_index(
        "uq_charge_items_daily_accrual",
        "charge_items",
        ["hospitalization_id", "accrual_date"],
        unique=True,
        postgresql_where=sa.text("accrual_date IS NOT NULL"),
    )

    # Origem do preço. O valor cobrado continua sendo prescriptions.price_minor,
    # congelado na prescrição: reajuste no catálogo não mexe em conta lançada.
    op.add_column("prescriptions", sa.Column("price_list_item_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_prescriptions_price_list_item",
        "prescriptions",
        "price_list_items",
        ["price_list_item_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_prescriptions_price_list_item", "prescriptions", type_="foreignkey")
    op.drop_column("prescriptions", "price_list_item_id")
    op.drop_index("uq_charge_items_daily_accrual", table_name="charge_items")
    op.drop_index("ix_charge_items_clinic_hospitalization", table_name="charge_items")
    op.drop_index("ix_charge_items_hospitalization_id", table_name="charge_items")
    op.drop_index("ix_charge_items_clinic_id", table_name="charge_items")
    op.drop_table("charge_items")
    op.drop_index("ix_price_list_items_clinic_active", table_name="price_list_items")
    op.drop_index("ix_price_list_items_clinic_id", table_name="price_list_items")
    op.drop_table("price_list_items")
