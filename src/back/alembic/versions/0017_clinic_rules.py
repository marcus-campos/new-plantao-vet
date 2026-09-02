"""Janelas de tolerância configuráveis pela clínica.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # As janelas ISMP (30/60/120) eram três constantes no código, e a tela de
    # configurações mostrava os números com a legenda "só leitura: nada aqui é
    # configurável pela clínica hoje". A janela decide o que é "atrasada", que
    # é derivada na leitura e aparece no sistema inteiro: no mural, na fila do
    # plantão, na passagem e no que o painel chama de atenção. Uma UTI com
    # bomba de infusão e um hotelzinho de pós-operatório não têm o mesmo
    # conceito de atraso.
    #
    # Os defaults preservam o comportamento de quem já está rodando: nenhuma
    # clínica existente muda de estado por causa desta migração.
    op.add_column(
        "clinics",
        sa.Column("tolerance_critical_minutes", sa.Integer(), nullable=False, server_default="30"),
    )
    op.add_column(
        "clinics",
        sa.Column("tolerance_normal_minutes", sa.Integer(), nullable=False, server_default="60"),
    )
    op.add_column(
        "clinics",
        sa.Column("tolerance_daily_minutes", sa.Integer(), nullable=False, server_default="120"),
    )


def downgrade() -> None:
    op.drop_column("clinics", "tolerance_daily_minutes")
    op.drop_column("clinics", "tolerance_normal_minutes")
    op.drop_column("clinics", "tolerance_critical_minutes")
