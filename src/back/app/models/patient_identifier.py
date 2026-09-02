import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PatientIdentifier(Base):
    """Como este paciente é identificado fora do sistema.

    Identificador é DADO, não coluna: `kind` é uma string validada contra o
    perfil de compliance da clínica. Assim o mesmo schema serve à veterinária
    (microchip, RGA) e à saúde humana (CPF, CNS) — muda o perfil, não a tabela.
    """

    __tablename__ = "patient_identifiers"
    __table_args__ = (
        # O mesmo número não pode apontar para dois pacientes da mesma clínica:
        # é isso que faz a busca por microchip/CPF devolver UM paciente.
        sa.UniqueConstraint("clinic_id", "kind", "value", name="uq_patient_identifiers_value"),
        sa.Index("ix_patient_identifiers_lookup", "clinic_id", "value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("patients.id"), index=True)
    kind: Mapped[str] = mapped_column(sa.Text)
    value: Mapped[str] = mapped_column(sa.Text)
