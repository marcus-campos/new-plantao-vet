"""Posologia por espécie: a calculadora de dose.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A concentração é propriedade da APRESENTAÇÃO, não do fármaco: "Ondansetrona
    # 2 mg/ml" trazia esse número dentro do nome, onde nenhum cálculo alcança.
    op.add_column(
        "price_list_items",
        sa.Column("concentration_mg_per_ml", sa.Numeric(12, 4), nullable=True),
    )

    # A posologia é do par (apresentação, espécie): gato e cão metabolizam
    # diferente, e alguns fármacos seguros no cão são fatais no gato.
    op.create_table(
        "dose_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column(
            "price_list_item_id",
            sa.Uuid(),
            sa.ForeignKey("price_list_items.id"),
            nullable=False,
        ),
        # NULL = vale para qualquer espécie. A regra mais específica vence.
        sa.Column("species", sa.Text(), nullable=True),
        sa.Column("route", sa.Text(), nullable=True),
        sa.Column("dose_min_per_kg", sa.Numeric(12, 4), nullable=True),
        sa.Column("dose_max_per_kg", sa.Numeric(12, 4), nullable=True),
        sa.Column("dose_default_per_kg", sa.Numeric(12, 4), nullable=True),
        # Dose POR ANIMAL, não por peso: clorfeniramina 1–2 mg/gato, atenolol
        # 6,25–12,5 mg/gato. Multiplicar isso pelo peso é erro de dose.
        sa.Column("fixed_dose_mg", sa.Numeric(12, 4), nullable=True),
        # Teto absoluto: alguns fármacos não escalam linearmente com o peso.
        sa.Column("max_total_mg", sa.Numeric(12, 4), nullable=True),
        sa.Column("frequency_minutes", sa.Integer(), nullable=True),
        # Contraindicação de espécie (carprofeno em gato, por exemplo). AVISA,
        # nunca bloqueia: a decisão é de quem tem registro no conselho.
        sa.Column(
            "is_contraindicated", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("warning", sa.Text(), nullable=True),
        # Raças com sensibilidade conhecida (ABCB1/MDR1 em Collie, Pastor
        # Australiano, Border Collie; galgos e propofol). Raça é texto livre, e
        # a lista é curada pela clínica.
        sa.Column("breed_warning", sa.Text(), nullable=True),
        sa.Column("breeds", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        # SEM revisão, a interface não pré-preenche e diz que não foi conferida.
        # O sistema não pode afirmar uma dose que nenhum veterinário assinou.
        sa.Column(
            "reviewed_by", sa.Uuid(), sa.ForeignKey("memberships.id"), nullable=True
        ),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reviewed_by_name", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "price_list_item_id", "species", "route", name="uq_dose_rules_item_species_route"
        ),
    )
    op.create_index("ix_dose_rules_clinic_item", "dose_rules", ["clinic_id", "price_list_item_id"])


def downgrade() -> None:
    op.drop_index("ix_dose_rules_clinic_item", table_name="dose_rules")
    op.drop_table("dose_rules")
    op.drop_column("price_list_items", "concentration_mg_per_ml")
