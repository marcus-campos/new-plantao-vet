"""Histórico de contato ordenável e webhook indexado.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Uma tentativa que FALHOU não tem `sent_at`, e `sent_at` era a única
    # coluna de tempo da tabela. Duas falhas em dias diferentes ficavam sem
    # ordem entre si, num log cuja razão de existir é a cronologia do contato
    # com o tutor (comunicação é fator contribuinte em 80% dos processos de
    # negligência veterinária, pesquisa §2.3).
    colunas = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("owner_contacts")}
    if "created_at" in colunas:
        return
    op.add_column(
        "owner_contacts",
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute("UPDATE owner_contacts SET created_at = COALESCE(sent_at, now())")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_owner_contacts_hospitalization_created "
        "ON owner_contacts (hospitalization_id, created_at)"
    )
    # O webhook da Meta procura pelo wamid a cada callback, e a Meta repete os
    # callbacks. Sem índice, cada repetição é uma varredura da tabela inteira.
    #
    # Parcial: só o canal WhatsApp tem wamid. IF NOT EXISTS porque a trilha do
    # WhatsApp já o criou fora da migração em ambientes de desenvolvimento.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_owner_contacts_external_id "
        "ON owner_contacts (external_id) WHERE external_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_owner_contacts_external_id", table_name="owner_contacts")
    op.drop_index("ix_owner_contacts_hospitalization_created", table_name="owner_contacts")
    op.drop_column("owner_contacts", "created_at")
