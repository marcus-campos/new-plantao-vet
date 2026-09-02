import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.prescription import PrescriptionCategory, _enum


class PriceListItem(Base):
    """Catálogo de preços da clínica (spec §"Tabela de preços").

    O item é a ORIGEM do preço, nunca a fonte de leitura da conta: quem prescreve
    copia `price_minor` para a prescrição e a conta usa a cópia. Reajustar aqui
    não pode mexer em nada já lançado."""

    __tablename__ = "price_list_items"
    __table_args__ = (
        # Barreira de tenancy no banco: filhos apontam para (id, clinic_id).
        sa.UniqueConstraint("id", "clinic_id", name="uq_price_list_items_id_clinic"),
        sa.Index("ix_price_list_items_clinic_active", "clinic_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    code: Mapped[str | None] = mapped_column(sa.Text, default=None)
    name: Mapped[str] = mapped_column(sa.Text)
    category: Mapped[PrescriptionCategory] = mapped_column(
        _enum(PrescriptionCategory, "prescription_category")
    )
    unit: Mapped[str] = mapped_column(sa.Text)
    price_minor: Mapped[int] = mapped_column(sa.Integer)
    # Diária por área do box (UTI, internação geral, isolamento): o lançamento
    # diário procura o item com is_daily_rate e kennel_area igual à área do box.
    is_daily_rate: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    #: mg por ml da APRESENTAÇÃO. Estava dentro do nome ("Ondansetrona 2 mg/ml"),
    #: onde nenhum cálculo alcança, e é o número que transforma miligramas no
    #: volume que a pessoa aspira na seringa.
    concentration_mg_per_ml: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(12, 4), default=None
    )
    kennel_area: Mapped[str | None] = mapped_column(sa.Text, default=None)
    is_controlled: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
