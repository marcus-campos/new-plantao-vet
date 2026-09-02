import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CRITICAL_TOLERANCE = 30
NORMAL_TOLERANCE = 60
DAILY_TOLERANCE = 120
DAILY_FREQUENCY_MINUTES = 1440


def default_tolerance(
    criticality: str, frequency_minutes: int | None, clinic: object | None = None
) -> int:
    """A janela de tolerância desta prescrição, em minutos.

    As janelas ISMP (spec §2: crítica 30, normal 60, diária ou mais espaçada
    120) são o DEFAULT, não a regra: a clínica ajusta as três nas
    configurações. Uma UTI com bomba de infusão e um hotelzinho de
    pós-operatório não têm o mesmo conceito de atraso, e a janela aparece no
    sistema inteiro porque "atrasada" é derivada dela a cada leitura.

    `clinic` é opcional para o chamador que ainda não tem a clínica em mãos
    (validação de payload, teste de unidade): sem ela valem os defaults."""
    if criticality == "critical":
        return getattr(clinic, "tolerance_critical_minutes", None) or CRITICAL_TOLERANCE
    if frequency_minutes is not None and frequency_minutes >= DAILY_FREQUENCY_MINUTES:
        return getattr(clinic, "tolerance_daily_minutes", None) or DAILY_TOLERANCE
    return getattr(clinic, "tolerance_normal_minutes", None) or NORMAL_TOLERANCE


class SchedulePreviewOut(BaseModel):
    """Os horários que ESTA prescrição vai criar, calculados pelo servidor.

    O cliente reimplementava o aprazamento inteiro – âncoras, offset, supressão
    da primeira dose – com uma tabela de âncoras CRAVADA no código, enquanto a
    tela de configurações deixava a clínica editar as dela. O preview mostrava
    horários que o servidor não ia criar. Uma regra, um lugar.
    """

    #: Já no fuso da clínica na hora de exibir; aqui trafega em UTC (ADR-0004).
    times: list[datetime]
    #: Quantas âncoras a "primeira dose agora" suprimiu – a explicação de por
    #: que a segunda dose não caiu no horário-padrão. Era calculada e jogada
    #: fora pelo cliente.
    suppressed: int = 0
    tolerance_minutes: int
    #: As âncoras que a clínica usa para esta frequência. Vazio = offset a
    #: partir de `starts_at`.
    anchors: list[str] = []


class PrescriptionCreate(BaseModel):
    kind: Literal["recurring", "continuous", "prn"]
    category: Literal["medication", "fluids", "monitoring", "nutrition", "care", "procedure"]
    name: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    frequency_minutes: int | None = Field(default=None, ge=5)
    duration_hours: int | None = Field(default=None, ge=1)
    criticality: Literal["normal", "critical"] = "normal"
    tolerance_minutes: int | None = Field(default=None, ge=1)
    first_dose_now: bool = False
    is_controlled: bool = False
    max_doses_24h: int | None = Field(default=None, ge=1)
    min_interval_minutes: int | None = Field(default=None, ge=1)
    price_minor: int | None = Field(default=None, ge=0)
    # Item do catálogo: o valor é COPIADO dele na criação. Reajustar a tabela
    # depois não mexe em prescrição nem em conta já lançada (spec §2).
    price_list_item_id: uuid.UUID | None = None
    starts_at: datetime | None = None

    @model_validator(mode="after")
    def check_kind(self) -> "PrescriptionCreate":
        if self.kind in ("recurring", "continuous") and self.frequency_minutes is None:
            raise ValueError("frequency_minutes é obrigatório para recurring e continuous")
        if self.kind == "prn" and self.frequency_minutes is not None:
            raise ValueError("prn não tem agenda: frequency_minutes deve ficar vazio")
        if self.kind == "continuous" and "rate_ml_h" not in self.details:
            raise ValueError("continuous exige details.rate_ml_h")
        return self

    def resolved_tolerance(self, clinic: object | None = None) -> int:
        return self.tolerance_minutes or default_tolerance(
            self.criticality, self.frequency_minutes, clinic
        )

    def resolved_starts_at(self) -> datetime:
        return self.starts_at or datetime.now(UTC)

    def resolved_ends_at(self) -> datetime | None:
        if self.duration_hours is None:
            return None
        return self.resolved_starts_at() + timedelta(hours=self.duration_hours)


class PrescriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hospitalization_id: uuid.UUID
    kind: str
    category: str
    name: str
    details: dict[str, Any]
    frequency_minutes: int | None
    criticality: str
    tolerance_minutes: int
    first_dose_now: bool
    is_controlled: bool
    max_doses_24h: int | None
    min_interval_minutes: int | None
    price_minor: int | None
    starts_at: datetime
    ends_at: datetime | None
    replaces_prescription_id: uuid.UUID | None
    price_list_item_id: uuid.UUID | None
    suspended_at: datetime | None


class PrescriptionAdjust(BaseModel):
    """Titulação: o que muda na nova versão. O que não vier é herdado da anterior."""

    name: str | None = None
    details: dict[str, Any] | None = None
    frequency_minutes: int | None = Field(default=None, ge=5)
    criticality: Literal["normal", "critical"] | None = None
    tolerance_minutes: int | None = Field(default=None, ge=1)
    price_minor: int | None = Field(default=None, ge=0)
    reason: str | None = None
