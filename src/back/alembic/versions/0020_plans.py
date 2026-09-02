"""Planos como dado: criados, aposentados e migrados pela plataforma.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-01
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

# Copiado, não importado: uma migração precisa continuar rodando igual
# mesmo depois que o código do modelo mudar.
DEFAULTS = [
    ("starter", "Starter", 10, 29700, 1),
    ("pro", "Pro", 25, 49700, 2),
    ("enterprise", "Enterprise", None, 0, 3),
]


def upgrade() -> None:
    # Era um dicionário no código. Quem vende não muda código para lançar um
    # plano de lançamento, nem para aposentá-lo depois.
    plans = op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("bed_limit", sa.Integer(), nullable=True),
        sa.Column("price_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("trial_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("retired_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        plans,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": name,
                "bed_limit": beds,
                "price_minor": price,
                "currency": "BRL",
                "trial_days": 0,
                "is_active": True,
                "sort_order": order,
                "created_at": now,
            }
            for code, name, beds, price, order in DEFAULTS
        ],
    )

    # Clínica com um plano que não existe no catálogo ("hospital", de um seed
    # antigo) não pode perder o valor nem quebrar a chave estrangeira: vira um
    # plano aposentado, com o limite que a clínica já tinha.
    conn = op.get_bind()
    orfaos = conn.execute(
        sa.text(
            "SELECT DISTINCT plan_tier, bed_limit FROM clinics "
            "WHERE plan_tier IS NOT NULL AND plan_tier NOT IN (SELECT code FROM plans)"
        )
    ).all()
    for code, beds in orfaos:
        conn.execute(
            sa.text(
                "INSERT INTO plans (id, code, name, bed_limit, price_minor, currency, trial_days,"
                " is_active, sort_order, created_at, retired_at)"
                " VALUES (:id, :code, :name, :beds, 0, 'BRL', 0, false, 99, :now, :now)"
            ),
            {"id": uuid.uuid4(), "code": code, "name": str(code).title(), "beds": beds, "now": now},
        )

    op.create_foreign_key(
        "fk_clinics_plan_tier_plans",
        "clinics",
        "plans",
        ["plan_tier"],
        ["code"],
        onupdate="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_clinics_plan_tier_plans", "clinics", type_="foreignkey")
    op.drop_table("plans")
