import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PatientCreate(BaseModel):
    name: str = Field(min_length=1)
    species: str = Field(min_length=1)
    owner_id: uuid.UUID
    breed: str | None = None
    weight_kg: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None


class PatientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    species: str | None = None
    breed: str | None = None
    weight_kg: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None
    is_active: bool | None = None


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    species: str
    breed: str | None
    weight_kg: Decimal | None
    notes: str | None
    is_active: bool


class PatientIdentifierIn(BaseModel):
    kind: str
    value: str


class PatientIdentifierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    value: str


class PatientRegister(BaseModel):
    """Cadastro completo: paciente + responsável + identificadores, num passo.

    Quem atende na recepção não deve precisar visitar três telas para começar
    um atendimento."""

    name: str = Field(min_length=1)
    species: str = Field(min_length=1)
    breed: str | None = None
    weight_kg: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None
    identifiers: list[PatientIdentifierIn] = Field(default_factory=list)
    # Um dos dois: responsável já cadastrado, ou os dados para criar um novo.
    owner_id: uuid.UUID | None = None
    owner_name: str | None = None
    owner_phone_e164: str | None = None
    owner_tax_id: str | None = None

    @model_validator(mode="after")
    def check_owner(self) -> "PatientRegister":
        if self.owner_id is None and not (self.owner_name and self.owner_phone_e164):
            raise ValueError("informe owner_id ou owner_name + owner_phone_e164")
        return self


class PatientSearchHit(BaseModel):
    id: uuid.UUID
    name: str
    species: str
    breed: str | None
    owner_id: uuid.UUID
    owner_name: str
    identifiers: list[PatientIdentifierOut]
    #: Preenchido quando o paciente JÁ está internado – abre a ficha em vez de
    #: internar de novo.
    active_hospitalization_id: uuid.UUID | None
