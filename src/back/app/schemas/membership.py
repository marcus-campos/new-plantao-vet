import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.membership import Role


class MembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    email: str
    role: Role
    license_number: str | None
    license_authority: str | None
    # Nunca o hash: a API só informa SE existe PIN definido.
    has_pin: bool
    is_active: bool


class MembershipCreate(BaseModel):
    name: str = Field(min_length=1)
    # str + pattern simples: o projeto não carrega email-validator
    # (LoginRequest segue a mesma escolha).
    email: str = Field(min_length=3, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8)
    role: Role
    license_number: str | None = None
    license_authority: str | None = None


class MembershipUpdate(BaseModel):
    role: Role | None = None
    license_number: str | None = None
    license_authority: str | None = None
    is_active: bool | None = None


class MembershipRosterOut(BaseModel):
    """Roster enxuto, legível por QUALQUER membro da clínica.

    A escala precisa mostrar quem está de plantão para todo mundo, não só para o
    admin. Por isso aqui não vai e-mail nem `has_pin` — só o que identifica o
    profissional num turno."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    role: Role
    license_number: str | None
    license_authority: str | None
    is_active: bool
