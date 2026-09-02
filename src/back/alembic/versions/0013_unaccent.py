"""unaccent para a busca de paciente

Nome brasileiro tem acento e ninguém digita acento na pressa do plantão:
sem isto, procurar "jose" não acha "José" e a recepção conclui que o
paciente não está cadastrado — e cadastra de novo.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade() -> None:
    # A extensão não é derrubada: outros objetos podem depender dela.
    pass
