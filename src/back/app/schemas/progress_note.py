import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class ProgressNoteCreate(BaseModel):
    """SOAP da evolução. Todos os campos são opcionais isoladamente, mas a
    regra 'pelo menos um texto' vive no serviço (AppError validation_error)."""

    subjective: str | None = None
    findings: str | None = None
    assessment: str | None = None
    plan: str | None = None
    amends_progress_note_id: uuid.UUID | None = None

    @field_validator("subjective", "findings", "assessment", "plan")
    @classmethod
    def blank_is_null(cls, value: str | None) -> str | None:
        # "   " não é evolução: normaliza antes de qualquer regra.
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def texts(self) -> tuple[str | None, ...]:
        return (self.subjective, self.findings, self.assessment, self.plan)


class ProgressNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hospitalization_id: uuid.UUID
    membership_id: uuid.UUID
    author_name: str
    author_license: str | None
    author_license_authority: str | None
    subjective: str | None
    findings: str | None
    assessment: str | None
    plan: str | None
    amends_progress_note_id: uuid.UUID | None
    signed_at: datetime


class MissingProgressNoteAlert(BaseModel):
    hospitalization_id: uuid.UUID
    patient_name: str | None
    # Horas desde a ÚLTIMA evolução; null quando nunca houve nenhuma
    # (a internação entra no alerta pelo tempo desde a admissão).
    hours_since: float | None


class ComplianceAlerts(BaseModel):
    missing_progress_note: list[MissingProgressNoteAlert]
