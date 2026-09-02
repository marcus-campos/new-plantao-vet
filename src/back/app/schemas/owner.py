import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OwnerCreate(BaseModel):
    name: str = Field(min_length=1)
    # E.164: '+' seguido de 8 a 15 dígitos.
    phone_e164: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    tax_id: str | None = None


class OwnerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    phone_e164: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")
    tax_id: str | None = None
    whatsapp_opt_in_at: datetime | None = None
    is_active: bool | None = None


class OwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone_e164: str
    tax_id: str | None
    whatsapp_opt_in_at: datetime | None
    is_active: bool
