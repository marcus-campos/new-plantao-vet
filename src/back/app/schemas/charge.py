import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChargeItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID | None
    price_list_item_id: uuid.UUID | None
    description: str
    quantity: Decimal
    unit_price_minor: int
    total_minor: int
    charged_at: datetime
    source: str


class ChargeDay(BaseModel):
    date: date
    total_minor: int
    items: list[ChargeItemOut]


class StatementOut(BaseModel):
    hospitalization_id: uuid.UUID
    currency: str
    total_minor: int
    days: list[ChargeDay]


class ManualChargeCreate(BaseModel):
    """Item lançado à mão. Com price_list_item_id, descrição e preço vêm do
    catálogo (e são COPIADOS aqui); sem ele, os dois são obrigatórios."""

    price_list_item_id: uuid.UUID | None = None
    description: str | None = Field(default=None, min_length=1)
    quantity: Decimal = Field(default=Decimal("1"), gt=0, le=Decimal("9999.99"))
    unit_price_minor: int | None = Field(default=None, ge=0)
    charged_at: datetime | None = None

    @model_validator(mode="after")
    def check_origin(self) -> "ManualChargeCreate":
        if self.price_list_item_id is None and (
            self.description is None or self.unit_price_minor is None
        ):
            raise ValueError("sem price_list_item_id, description e unit_price_minor são exigidos")
        return self
