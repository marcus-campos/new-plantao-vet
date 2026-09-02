"""Aparelhos compartilhados com identidade própria, no lugar da chave única.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A chave de estação era UMA senha para a clínica inteira: revogar era
    # tudo ou nada, ninguém sabia quais aparelhos existiam, e o bloqueio por
    # erro de PIN vivia na memória do processo chaveado por um identificador
    # sorteado a cada login, então relogar zerava a contagem.
    #
    # A coluna `station_key_hash` da clínica CONTINUA existindo: há aparelho em
    # campo que só conhece ela, e derrubar todos de uma vez para estrear um
    # modelo de acesso seria a mesma falha que o modelo novo corrige.
    op.create_table(
        "station_devices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("enrollment_code_hash", sa.Text(), nullable=True),
        sa.Column("enrollment_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("secret_hash", sa.Text(), nullable=True),
        sa.Column("pin_failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pin_locked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=True),
        sa.Column("approved_by_name", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_station_devices_clinic_status", "station_devices", ["clinic_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_station_devices_clinic_status", table_name="station_devices")
    op.drop_table("station_devices")
