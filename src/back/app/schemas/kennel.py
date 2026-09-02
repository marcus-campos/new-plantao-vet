import uuid

from pydantic import BaseModel, ConfigDict, Field


class KennelCreate(BaseModel):
    name: str = Field(min_length=1)
    area: str | None = None


class KennelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    area: str | None = None
    is_active: bool | None = None


class KennelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    area: str | None
    is_active: bool
