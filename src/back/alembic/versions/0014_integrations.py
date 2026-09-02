"""Integrações reais: WhatsApp com estado de envio, push por dispositivo, timbre da clínica e jejum.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

STATUSES = "'queued', 'sent', 'delivered', 'read', 'failed'"


def upgrade() -> None:
    # --- WhatsApp: parar de afirmar entrega que não houve --------------------
    #
    # `sent_at` era gravado sempre, mesmo quando o cliente era um stub que
    # devolvia "stub-<uuid>": o prontuário passava a conter registro auditado
    # de um envio que não aconteceu. Agora o estado é explícito e o instante do
    # envio só existe quando o provedor confirmou.
    op.add_column(
        "owner_contacts",
        sa.Column("status", sa.Text(), nullable=False, server_default="sent"),
    )
    op.add_column("owner_contacts", sa.Column("failure_reason", sa.Text(), nullable=True))
    op.alter_column("owner_contacts", "sent_at", nullable=True)
    op.create_check_constraint(
        "ck_owner_contacts_status", "owner_contacts", f"status IN ({STATUSES})"
    )

    # --- Push: o token do aparelho tinha de morar em algum lugar --------------
    #
    # O app pedia permissão de notificação, obtia o token do Expo e o jogava
    # fora: não havia rota para registrá-lo nem tabela para guardá-lo, então o
    # "alerta no bolso" não existia em nenhuma forma.
    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column(
            "membership_id", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=False
        ),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Um token pertence a um aparelho: reinstalar troca o token, e o mesmo
        # token nunca pode notificar duas pessoas.
        sa.UniqueConstraint("token", name="uq_devices_token"),
    )
    op.create_index("ix_devices_membership", "devices", ["membership_id", "is_active"])

    # --- Timbre do prontuário -----------------------------------------------
    #
    # A tela do prontuário lia `clinic.address`, `.phone` e `.tax_id` através de
    # casts para dicionário, campos que nunca existiram. O documento entregue
    # ao tutor saía sem endereço, telefone nem CNPJ.
    op.add_column("clinics", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("clinics", sa.Column("phone", sa.Text(), nullable=True))
    op.add_column("clinics", sa.Column("tax_id", sa.Text(), nullable=True))

    # --- Jejum ---------------------------------------------------------------
    #
    # A spec diz que `category` existe para "o jejum bloquear `nutrition`" e as
    # configurações desenhadas trazem o botão. Não havia estado de jejum em
    # lugar nenhum do schema.
    op.add_column(
        "hospitalizations",
        sa.Column("fasting_since", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("hospitalizations", sa.Column("fasting_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("hospitalizations", "fasting_reason")
    op.drop_column("hospitalizations", "fasting_since")
    op.drop_column("clinics", "tax_id")
    op.drop_column("clinics", "phone")
    op.drop_column("clinics", "address")
    op.drop_index("ix_devices_membership", table_name="devices")
    op.drop_table("devices")
    op.drop_constraint("ck_owner_contacts_status", "owner_contacts", type_="check")
    op.alter_column("owner_contacts", "sent_at", nullable=False)
    op.drop_column("owner_contacts", "failure_reason")
    op.drop_column("owner_contacts", "status")
