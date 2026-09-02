import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hospitalization_id: uuid.UUID
    prescription_id: uuid.UUID | None
    title: str
    category: str
    scheduled_for: datetime
    criticality: str
    tolerance_minutes: int
    status: str
    display_state: str
    executed_at: datetime | None
    executed_by: uuid.UUID | None
    retroactive: bool
    early: bool
    outcome_reason: str | None
    values: dict[str, Any] | None
    price_minor: int | None

    @classmethod
    def from_task(cls, task: Any, now: datetime) -> "TaskOut":
        from app.services.tasks import TaskService

        data = {
            field: getattr(task, field) for field in cls.model_fields if field != "display_state"
        }
        return cls(**data, display_state=TaskService.display_state(task, now))


class TaskExecute(BaseModel):
    #: A grade de monitoramento preenchida (`temperature_c`, `pain_score`, …)
    #: mais o texto livre que o resto do sistema já grava. O que é vital é
    #: conferido contra `app/vitals.py` na rota, que é onde a prescrição
    #: (quem declara a grade) está disponível.
    values: dict[str, Any] | None = None
    retroactive: bool = False
    performed_at: datetime | None = None
    partial: bool = False
    confirm_early: bool = False
    #: Segue mesmo com o aviso de jejum. Mesmo contrato do override de PRN:
    #: aviso auditado, nunca bloqueio duro: fricção gera workaround que
    #: falsifica o registro (pesquisa §4).
    override: bool = False

    @model_validator(mode="after")
    def check(self) -> "TaskExecute":
        if self.retroactive and self.performed_at is None:
            raise ValueError("performed_at é obrigatório quando retroactive=true")
        if self.partial and not (self.values or {}).get("dose_given"):
            raise ValueError("execução parcial exige values.dose_given")
        return self


class TaskNotDone(BaseModel):
    reason: Literal["refused", "fasting", "unavailable", "vet_order", "other"]
    values: dict[str, Any] | None = None

    @model_validator(mode="after")
    def check(self) -> "TaskNotDone":
        if self.reason == "other" and not (self.values or {}).get("outcome_detail"):
            raise ValueError("motivo 'other' exige values.outcome_detail")
        return self


class TaskAdHoc(BaseModel):
    prescription_id: uuid.UUID | None = None
    hospitalization_id: uuid.UUID | None = None
    title: str | None = None
    category: str = "care"
    values: dict[str, Any] | None = None
    override: bool = False

    @model_validator(mode="after")
    def check(self) -> "TaskAdHoc":
        if self.prescription_id is None and not (self.hospitalization_id and self.title):
            raise ValueError("informe prescription_id (PRN) ou hospitalization_id + title")
        return self
