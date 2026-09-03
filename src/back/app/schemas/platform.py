"""Contratos do back-office: o que quem vende e dá suporte vê e faz."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.signup import senha_cabe_no_bcrypt

SubscriptionStatus = Literal["trial", "active", "past_due", "suspended", "cancelled"]


class PlatformLoginRequest(BaseModel):
    email: str
    password: str


class PlatformMeOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str


class PlatformClinicRow(BaseModel):
    """Uma clínica na lista. Os contadores respondem as três perguntas de quem
    vende: está usando? tem gente? tem paciente?"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    plan_tier: str | None
    bed_limit: int | None
    subscription_status: str
    trial_ends_at: datetime | None
    contact_name: str | None
    created_at: datetime
    members: int = 0
    active_hospitalizations: int = 0
    #: Último ato registrado na trilha da clínica. Sem isto a lista não diz
    #: quem parou de usar, que é o cliente que vai cancelar.
    last_activity_at: datetime | None = None


class PlatformMemberOut(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    email: str
    role: str
    license_number: str | None
    license_authority: str | None
    has_pin: bool
    is_active: bool


class PlatformDeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: str
    last_seen_at: datetime | None
    pin_locked_at: datetime | None


class PlatformAuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_name: str
    action: str
    entity_type: str
    created_at: datetime


class PlatformClinicOut(PlatformClinicRow):
    """A ficha da clínica para o suporte: quem é, quem está nela, o que
    aconteceu por último."""

    locale: str
    currency: str
    timezone: str
    compliance_profile: str
    contact_email: str | None
    contact_phone: str | None
    support_notes: str | None
    suspended_at: datetime | None
    station_key_version: int
    members_list: list[PlatformMemberOut] = []
    devices: list[PlatformDeviceOut] = []
    recent_audit: list[PlatformAuditOut] = []


class PlatformClinicCreate(BaseModel):
    """Onboarding de um cliente: a clínica e o primeiro administrador dela.

    A senha do administrador sai da resposta EM CLARO, uma vez: é o que se
    entrega ao cliente. Aqui fica só o hash, como toda senha do sistema."""

    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
    plan_tier: str = "starter"
    #: Sem informar, o limite vem do plano.
    bed_limit: int | None = Field(default=None, ge=0)
    subscription_status: SubscriptionStatus = "trial"
    trial_days: int = Field(default=30, ge=0, le=365)
    locale: str = "pt-BR"
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    timezone: str = "America/Sao_Paulo"
    compliance_profile: str = "br"
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    admin_name: str = Field(min_length=2, max_length=120)
    admin_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    #: Vazio = o sistema sorteia uma e devolve.
    admin_password: str | None = Field(default=None, min_length=8)

    @field_validator("admin_password")
    @classmethod
    def _valida_senha(cls, value: str | None) -> str | None:
        # Mesmo limite do cadastro público (`schemas/signup.py`), pelo mesmo
        # motivo — bytes, não caracteres — mas só quando o suporte DIGITA uma
        # senha: vazio é o caminho comum (o sistema sorteia) e não passa por
        # `.encode()`.
        return senha_cabe_no_bcrypt(value) if value is not None else value


class PlatformClinicCreated(BaseModel):
    clinic: PlatformClinicOut
    admin_email: str
    #: Em claro UMA vez. Depois disto só existe o hash.
    admin_password: str


class PlatformClinicUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    plan_tier: str | None = None
    bed_limit: int | None = Field(default=None, ge=0)
    subscription_status: SubscriptionStatus | None = None
    trial_ends_at: datetime | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    support_notes: str | None = None


class PasswordReset(BaseModel):
    #: Em claro UMA vez: é o que o suporte dita ao telefone.
    temporary_password: str


# --- Planos ------------------------------------------------------------------


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    bed_limit: int | None
    price_minor: int
    currency: str
    trial_days: int
    is_active: bool
    sort_order: int
    notes: str | None
    created_at: datetime
    retired_at: datetime | None
    #: Quantas clínicas estão neste plano agora. É o número que decide se dá
    #: para apagar, e quanto uma migração vai mover.
    clinics: int = 0


class PlanCreate(BaseModel):
    """Um plano novo. `code` é chave e não muda depois: é o que toda trilha já
    gravada chama de plano."""

    code: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,30}$")
    name: str = Field(min_length=2, max_length=60)
    bed_limit: int | None = Field(default=None, ge=0)
    price_minor: int = Field(default=0, ge=0)
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    #: Maior que zero faz dele um plano de TESTE: quem entra começa em trial.
    trial_days: int = Field(default=0, ge=0, le=365)
    sort_order: int = 0
    notes: str | None = Field(default=None, max_length=500)


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    bed_limit: int | None = Field(default=None, ge=0)
    price_minor: int | None = Field(default=None, ge=0)
    trial_days: int | None = Field(default=None, ge=0, le=365)
    is_active: bool | None = None
    sort_order: int | None = None
    notes: str | None = Field(default=None, max_length=500)


class PlanMigrate(BaseModel):
    #: O plano de destino. Precisa estar ativo.
    to: str
    #: Aposentar o plano de origem depois de esvaziá-lo (o caso do "fundador").
    retire_source: bool = True


class PlanMigrated(BaseModel):
    moved: int
    source: PlanOut
    target: PlanOut
