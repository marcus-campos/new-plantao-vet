import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["medication", "fluids", "monitoring", "nutrition", "care", "procedure"]


class PriceListItemCreate(BaseModel):
    code: str | None = None
    name: str = Field(min_length=1)
    category: Category
    unit: str = Field(min_length=1)
    price_minor: int = Field(ge=0)
    is_daily_rate: bool = False
    kennel_area: str | None = None
    is_controlled: bool = False
    #: mg/ml da apresentação: o número que vira volume na seringa.
    concentration_mg_per_ml: Decimal | None = Field(default=None, ge=0)


class PriceListItemUpdate(BaseModel):
    code: str | None = None
    name: str | None = Field(default=None, min_length=1)
    category: Category | None = None
    unit: str | None = Field(default=None, min_length=1)
    price_minor: int | None = Field(default=None, ge=0)
    is_daily_rate: bool | None = None
    kennel_area: str | None = None
    is_controlled: bool | None = None
    is_active: bool | None = None
    #: mg/ml da apresentação: o número que vira volume na seringa.
    concentration_mg_per_ml: Decimal | None = Field(default=None, ge=0)


class PriceListItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str | None
    name: str
    category: str
    unit: str
    price_minor: int
    is_daily_rate: bool
    kennel_area: str | None
    is_controlled: bool
    is_active: bool
    #: mg/ml da apresentação: o número que vira volume na seringa.
    concentration_mg_per_ml: Decimal | None
    #: Quantas posologias CONFERIDAS este item tem.
    #:
    #: Vive na listagem porque a lacuna é acionável: um fármaco sem posologia
    #: conferida não pré-preenche dose nenhuma, e quem administra o catálogo
    #: não tinha como saber quais faltam sem abrir um por um.
    reviewed_dose_rules: int = 0


class DoseRuleIn(BaseModel):
    """A posologia que a clínica cadastra para uma apresentação.

    Vive junto do item de preço porque é lá que o fármaco já é definido uma vez:
    dois lugares para definir o mesmo remédio seria a duplicação que esta
    revisão inteira combateu."""

    species: str | None = None
    route: str | None = None
    dose_min_per_kg: Decimal | None = Field(default=None, ge=0)
    dose_max_per_kg: Decimal | None = Field(default=None, ge=0)
    dose_default_per_kg: Decimal | None = Field(default=None, ge=0)
    #: Dose por ANIMAL. Multiplicá-la pelo peso é o erro que ela evita.
    fixed_dose_mg: Decimal | None = Field(default=None, ge=0)
    max_total_mg: Decimal | None = Field(default=None, ge=0)
    frequency_minutes: int | None = Field(default=None, ge=5)
    is_contraindicated: bool = False
    warning: str | None = None
    breed_warning: str | None = None
    #: Raças que disparam o aviso, separadas por vírgula.
    breeds: str | None = None
    source: str | None = None
    notes: str | None = None
    #: Marcar como conferida é um ato: só quem tem registro no conselho pode, e
    #: fica com nome e data. Sem isso, a interface não pré-preenche.
    reviewed: bool = False
    #: Desligar em vez de apagar: a posologia já foi usada em prescrições que
    #: continuam no prontuário, e o histórico precisa continuar explicável.
    is_active: bool = True


class DoseRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    price_list_item_id: uuid.UUID
    species: str | None
    route: str | None
    dose_min_per_kg: Decimal | None
    dose_max_per_kg: Decimal | None
    dose_default_per_kg: Decimal | None
    fixed_dose_mg: Decimal | None
    max_total_mg: Decimal | None
    frequency_minutes: int | None
    is_contraindicated: bool
    warning: str | None
    breed_warning: str | None
    breeds: str | None
    source: str | None
    notes: str | None
    reviewed_at: datetime | None
    reviewed_by_name: str | None
    is_active: bool


class DosePreviewRequest(BaseModel):
    price_list_item_id: uuid.UUID
    hospitalization_id: uuid.UUID
    #: O veterinário sempre pode digitar outro valor, e o cálculo o acompanha.
    dose_per_kg: Decimal | None = Field(default=None, ge=0)


class DosePreviewOut(BaseModel):
    """O resultado E o caminho até ele.

    O que se confere não é o número: é a conta. "0,27 ml" sozinho não se
    verifica; "0,15 mg/kg × 3,6 kg ÷ 2 mg/ml" se verifica num relance."""

    dose_per_kg: Decimal | None
    weight_kg: Decimal | None
    dose_mg: Decimal | None
    concentration_mg_per_ml: Decimal | None
    volume_ml: Decimal | None
    #: Códigos, nunca prosa (ADR-0004).
    warnings: list[str] = []
    #: Texto escrito pela clínica: não é traduzido (spec §3.6).
    notes: list[str] = []
    rule_id: uuid.UUID | None = None
    reviewed: bool = False
    reviewed_by_name: str | None = None
    dose_min_per_kg: Decimal | None = None
    dose_max_per_kg: Decimal | None = None
    frequency_minutes: int | None = None
    species: str | None = None
    unit_label: str | None = None
