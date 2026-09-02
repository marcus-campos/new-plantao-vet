"""patient identifiers

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-31
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patient_identifiers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), sa.ForeignKey("patients.id"), nullable=False),
        # `kind` é dado, não enum: veterinária usa microchip/rga, saúde humana
        # usa cpf/cns/mrn. Quem valida é o perfil de compliance da clínica.
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        # O mesmo número não aponta para dois pacientes: é o que faz a busca por
        # microchip/CPF devolver UM paciente.
        sa.UniqueConstraint("clinic_id", "kind", "value", name="uq_patient_identifiers_value"),
    )
    op.create_index("ix_patient_identifiers_clinic_id", "patient_identifiers", ["clinic_id"])
    op.create_index("ix_patient_identifiers_patient_id", "patient_identifiers", ["patient_id"])
    op.create_index("ix_patient_identifiers_lookup", "patient_identifiers", ["clinic_id", "value"])


def downgrade() -> None:
    op.drop_index("ix_patient_identifiers_lookup", table_name="patient_identifiers")
    op.drop_index("ix_patient_identifiers_patient_id", table_name="patient_identifiers")
    op.drop_index("ix_patient_identifiers_clinic_id", table_name="patient_identifiers")
    op.drop_table("patient_identifiers")
