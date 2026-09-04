"""O tour de boas-vindas: quando esta pessoa já o viu.

Fica no VÍNCULO e não no usuário porque o tour é diferente por papel — quem
administra a clínica precisa achar a gestão, quem prescreve precisa achar a
ficha, e quem executa precisa achar o plantão. Um veterinário que também
administra outra clínica vê os dois tours, cada um no seu lugar.

Guarda o INSTANTE e não um booleano: "quando" responde tudo que "se" responde,
e ainda diz quanto tempo a pessoa levou para chegar aqui.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-04
"""

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memberships",
        sa.Column("tour_done_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Quem já está dentro não é recebido com um tour de boas-vindas: seria
    # apresentar a casa a quem mora nela. Todo vínculo existente nasce com o
    # tour dado por visto.
    op.execute("UPDATE memberships SET tour_done_at = now()")


def downgrade() -> None:
    op.drop_column("memberships", "tour_done_at")
