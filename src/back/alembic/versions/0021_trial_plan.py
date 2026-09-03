"""O plano de teste: 14 dias, 10 leitos, zero real.

`Plan.trial_days` já descrevia este caso ("um plano de teste é um plano com
trial_days > 0: quem entra nele começa em trial com a data de fim já
calculada") e nenhum plano do catálogo o exercia. É por ele que toda clínica
que se cadastra pelo site entra.

O limite de 10 leitos é suave, como todo bed_limit: nunca bloqueia uma
admissão, só avisa o administrador.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-03
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

TRIAL_CODE = "trial"


def upgrade() -> None:
    conn = op.get_bind()
    # Idempotente, como PlanService.ensure_defaults: um banco que já tem o
    # plano (criado à mão pelo back-office) não ganha um duplicado, e `code`
    # é unique — um INSERT cego derrubaria a migração.
    ja_existe = conn.execute(
        sa.text("SELECT 1 FROM plans WHERE code = :code"), {"code": TRIAL_CODE}
    ).first()
    if ja_existe:
        return
    conn.execute(
        sa.text(
            "INSERT INTO plans (id, code, name, bed_limit, price_minor, currency,"
            " trial_days, is_active, sort_order, notes, created_at)"
            " VALUES (:id, :code, 'Teste 14 dias', 10, 0, 'BRL', 14, true, 0, :notes, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "code": TRIAL_CODE,
            "notes": "Cadastro pelo site. 14 dias; depois a clínica vira somente-leitura.",
            "now": datetime.now(UTC),
        },
    )


def downgrade() -> None:
    # Só some se ninguém estiver nele: a chave estrangeira de clinics.plan_tier
    # recusaria, e apagar o plano de quem está testando seria perder o vínculo.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM plans WHERE code = :code"
            " AND NOT EXISTS (SELECT 1 FROM clinics WHERE plan_tier = :code)"
        ),
        {"code": TRIAL_CODE},
    )
