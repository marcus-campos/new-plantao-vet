"""Operador da plataforma e ciclo de vida da assinatura.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Não existia ninguém "do lado de fora": todas as rotas são escopadas por
    # clínica, e por construção ninguém enxergava mais de uma. Vender e dar
    # suporte exige alguém que enxergue todas, sem ser membro de nenhuma.
    op.add_column(
        "users",
        sa.Column("is_platform_operator", sa.Boolean(), nullable=False, server_default="false"),
    )
    # A assinatura tinha só `plan_tier` e `bed_limit`, editáveis pela própria
    # clínica. Quem já está rodando entra como `active`: ninguém vira "trial"
    # por causa de uma migração.
    op.add_column(
        "clinics",
        sa.Column("subscription_status", sa.Text(), nullable=False, server_default="active"),
    )
    op.add_column("clinics", sa.Column("trial_ends_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("clinics", sa.Column("suspended_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("clinics", sa.Column("contact_name", sa.Text()))
    op.add_column("clinics", sa.Column("contact_email", sa.Text()))
    op.add_column("clinics", sa.Column("contact_phone", sa.Text()))
    op.add_column("clinics", sa.Column("support_notes", sa.Text()))


def downgrade() -> None:
    for col in (
        "support_notes",
        "contact_phone",
        "contact_email",
        "contact_name",
        "suspended_at",
        "trial_ends_at",
        "subscription_status",
    ):
        op.drop_column("clinics", col)
    op.drop_column("users", "is_platform_operator")
