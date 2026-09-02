import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.handover import HandoverReportOut


class ShiftCreate(BaseModel):
    name: str = Field(min_length=1)
    starts_at: datetime
    ends_at: datetime
    membership_id: uuid.UUID
    is_vet_responsible: bool = False

    @model_validator(mode="after")
    def check(self) -> "ShiftCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at deve ser posterior a starts_at")
        return self


class ShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    starts_at: datetime
    ends_at: datetime
    membership_id: uuid.UUID
    is_vet_responsible: bool
    closed_at: datetime | None


class ShiftClose(BaseModel):
    to_shift_id: uuid.UUID | None = None


class ShiftClosed(BaseModel):
    shift: ShiftOut
    reports: list[HandoverReportOut]
    # Ids dos boletins que o turno deixou sem aprovação. Vai na resposta porque
    # a omissão é para ser VISTA, não escondida — e já está auditada.
    missing_review: list[uuid.UUID]


class ShiftNoteCreate(BaseModel):
    text: str = Field(min_length=1)
    # `audio` significa "transcrito de áudio". O áudio em si não sobe nem é
    # armazenado (LGPD, spec §2): o cliente transcreve, o profissional confirma
    # e só o texto chega aqui.
    source: Literal["typed", "audio"] = "typed"
    shift_id: uuid.UUID | None = None


class ShiftNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hospitalization_id: uuid.UUID
    shift_id: uuid.UUID | None
    membership_id: uuid.UUID | None
    author_name: str
    text: str
    source: str
    created_at: datetime
