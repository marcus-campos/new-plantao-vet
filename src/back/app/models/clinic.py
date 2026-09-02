import enum
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UnitSystem(enum.StrEnum):
    metric = "metric"
    imperial = "imperial"


# Âncoras default (UFMS), chaveadas por MINUTOS de frequência.
DEFAULT_ANCHORS: dict[str, list[str]] = {
    "1440": ["10:00"],
    "720": ["10:00", "22:00"],
    "480": ["10:00", "18:00", "02:00"],
    "360": ["10:00", "16:00", "22:00", "04:00"],
}

# Cerimônias criadas automaticamente na admissão. name_key é chave de
# catálogo: o name gravado na prescrição é translate(name_key, clinic.locale).
DEFAULT_PRESCRIPTIONS: list[dict[str, Any]] = [
    {
        "name_key": "ceremony.owner_contact",
        "category": "care",
        "kind": "recurring",
        "frequency_minutes": 1440,
        "criticality": "normal",
        "anchor": "16:00",
    },
    {
        "name_key": "ceremony.daily_progress_note",
        "category": "care",
        "kind": "recurring",
        "frequency_minutes": 1440,
        "criticality": "normal",
        "anchor": "08:00",
    },
]


# O catálogo INICIAL de planos (spec §1): leito = paciente internado
# simultâneo, limite suave. Planos vivem na tabela `plans`, criados e
# aposentados pela plataforma; isto é só o que a migração e o seed semeiam
# quando a tabela está vazia. `PLAN_TIERS` continua como o atalho
# código → limite dos três de origem.
DEFAULT_PLANS: list[dict[str, object]] = [
    {"code": "starter", "name": "Starter", "bed_limit": 10, "price_minor": 29700, "sort_order": 1},
    {"code": "pro", "name": "Pro", "bed_limit": 25, "price_minor": 49700, "sort_order": 2},
    {
        "code": "enterprise",
        "name": "Enterprise",
        "bed_limit": None,
        "price_minor": 0,
        "sort_order": 3,
    },
]
PLAN_TIERS: dict[str, int | None] = {
    str(plan["code"]): plan["bed_limit"]  # type: ignore[misc]
    for plan in DEFAULT_PLANS
}

#: Ciclo de vida da assinatura. Só `suspended` e `cancelled` fecham a porta,
#: e fecham NO LOGIN: uma sessão aberta no meio do plantão não cai por causa
#: de boleto. Quem já está dentro termina o turno; quem chega depois vê o
#: motivo. `past_due` só avisa.
SUBSCRIPTION_STATUSES = ("trial", "active", "past_due", "suspended", "cancelled")


class Clinic(Base):
    __tablename__ = "clinics"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.Text)
    slug: Mapped[str] = mapped_column(sa.Text, unique=True)
    locale: Mapped[str] = mapped_column(sa.Text, default="pt-BR")
    currency: Mapped[str] = mapped_column(sa.String(3), default="BRL")
    unit_system: Mapped[UnitSystem] = mapped_column(
        sa.Enum(
            UnitSystem,
            name="unit_system",
            native_enum=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=UnitSystem.metric,
    )
    compliance_profile: Mapped[str] = mapped_column(sa.Text, default="br")
    timezone: Mapped[str] = mapped_column(sa.Text, default="America/Sao_Paulo")
    anchors: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=lambda: {key: list(value) for key, value in DEFAULT_ANCHORS.items()}
    )
    default_prescriptions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=lambda: [dict(item) for item in DEFAULT_PRESCRIPTIONS]
    )
    # Janelas de tolerância, em minutos (migração 0017).
    #
    # Eram três constantes no código, e a tela dizia "só leitura: nada aqui é
    # configurável pela clínica hoje". As janelas ISMP (30/60/120) são um bom
    # ponto de partida, não uma lei: uma UTI que trabalha com bomba de infusão
    # e um hotelzinho de pós-operatório não têm o mesmo conceito de atraso, e
    # uma janela errada aparece no sistema inteiro, porque "atrasada" é
    # DERIVADA da tolerância a cada leitura. Ficam como colunas, e não como um
    # JSON, porque cada uma tem significado próprio e é consultada por nome.
    tolerance_critical_minutes: Mapped[int] = mapped_column(sa.Integer, default=30)
    tolerance_normal_minutes: Mapped[int] = mapped_column(sa.Integer, default=60)
    tolerance_daily_minutes: Mapped[int] = mapped_column(sa.Integer, default=120)
    #: O código do plano (`plans.code`). Continua com este nome porque é o que
    #: toda trilha de auditoria já gravada chama de plano; virou chave
    #: estrangeira na migração 0020.
    plan_tier: Mapped[str | None] = mapped_column(
        sa.ForeignKey("plans.code", onupdate="CASCADE"), default=None
    )
    bed_limit: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    # Assinatura (migração 0019). Antes só existiam `plan_tier` e `bed_limit`,
    # editáveis pela PRÓPRIA clínica nas configurações: o cliente escolhia o
    # plano dele. O ciclo de vida da assinatura é de quem vende.
    subscription_status: Mapped[str] = mapped_column(sa.Text, default="trial")
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=None
    )
    suspended_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    #: Quem atende o telefone do lado do cliente. Suporte sem isto começa
    #: perguntando "com quem eu falo?".
    contact_name: Mapped[str | None] = mapped_column(sa.Text, default=None)
    contact_email: Mapped[str | None] = mapped_column(sa.Text, default=None)
    contact_phone: Mapped[str | None] = mapped_column(sa.Text, default=None)
    #: Anotações de suporte e comercial. Só a plataforma lê e escreve.
    support_notes: Mapped[str | None] = mapped_column(sa.Text, default=None)
    station_key_hash: Mapped[str | None] = mapped_column(sa.Text, default=None)
    station_key_version: Mapped[int] = mapped_column(sa.Integer, default=1)
    # Timbre do prontuário (migração 0014). A tela lia `clinic.address`,
    # `.phone` e `.tax_id` através de casts para dicionário e esses campos
    # nunca existiram: o documento entregue ao tutor saía sem endereço,
    # telefone nem CNPJ, justamente o que identifica quem responde por ele.
    address: Mapped[str | None] = mapped_column(sa.Text, default=None)
    phone: Mapped[str | None] = mapped_column(sa.Text, default=None)
    #: CNPJ no Brasil. Está em `AuditService.REDACTED`: identificador fiscal
    #: não precisa ficar copiado em cada linha da trilha.
    tax_id: Mapped[str | None] = mapped_column(sa.Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
