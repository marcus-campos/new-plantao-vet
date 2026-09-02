import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.hospitalization import HospitalizationOut, PatientSummary
from app.schemas.prescription import PrescriptionOut
from app.schemas.progress_note import ProgressNoteOut


class RecordAuthor(BaseModel):
    """Nome + registro profissional: o perfil `br` exige os dois em cada ato."""

    name: str
    license_number: str | None = None
    license_authority: str | None = None


class RecordExecution(BaseModel):
    """Uma tarefa efetivamente executada (feita, parcial ou não feita com motivo).
    O que não foi feito e por quê é parte do prontuário, não omissão."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: str
    scheduled_for: datetime
    status: str
    executed_at: datetime | None
    outcome_reason: str | None
    values: dict[str, Any] | None
    author: RecordAuthor | None = None


class RecordOut(BaseModel):
    """Prontuário completo para exportação (o front vira PDF).

    Seção ausente = `null` (não pedida em `include`); seção pedida e vazia = `[]`.
    """

    generated_at: datetime
    clinic_name: str
    patient: PatientSummary | None
    owner_name: str | None
    hospitalization: HospitalizationOut
    vet: RecordAuthor | None
    progress_notes: list[ProgressNoteOut] | None = None
    tasks: list[RecordExecution] | None = None
    prescriptions: list[PrescriptionOut] | None = None
    charges: list[dict[str, Any]] | None = None
