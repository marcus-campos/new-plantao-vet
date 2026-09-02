import uuid
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DoseRule(Base):
    """A posologia de uma apresentação, por espécie.

    Existe porque **51% dos erros de medicação são de dose** (pesquisa §5.8) e a
    calculadora de dose está na lista de paridade obrigatória do mercado (§2.7):
    todo concorrente tem, e aqui o veterinário digitava o resultado da conta que
    fez de cabeça.

    Três decisões que não são de engenharia:

    1. **A regra é do par (apresentação, espécie).** Cão e gato metabolizam
       diferente: o gato não tem várias vias de glicuronidação hepática que o
       cão usa, e fármacos seguros no cão se acumulam a nível tóxico nele
       (carprofeno é fatal em gato; enrofloxacina precisa de teto por causa de
       degeneração retiniana irreversível). Uma posologia única por fármaco
       seria uma posologia errada para metade dos pacientes.

    2. **Nem tudo é mg/kg.** Alguns fármacos são dose fixa por animal:
       clorfeniramina 1–2 mg/gato, atenolol 6,25–12,5 mg/gato. Multiplicar isso
       pelo peso É o erro de dose. Por isso `fixed_dose_mg` existe e vence.

    3. **Regra sem revisão não preenche nada.** `reviewed_at` nulo significa que
       nenhum veterinário conferiu aquele número, e a interface diz isso em vez
       de sugerir. O sistema não pode afirmar uma dose que ninguém assinou. É a
       mesma postura que a spec §8.1 já assume sobre o resto do domínio clínico.

    Raça entra como AVISO, não como cálculo: a variante ABCB1-1∆ (MDR1) em
    Collie, Pastor Australiano, Border Collie, Shetland, Bobtail e Whippet de
    pelo longo compromete a glicoproteína-P na barreira hematoencefálica e exige
    reduzir dose ou trocar de fármaco (ivermectina, loperamida, alguns
    quimioterápicos, butorfanol); galgos metabolizam propofol mais devagar. Raça
    é texto livre no cadastro, então quem curou a lista foi a clínica. O
    sistema casa o texto e avisa, nunca decide sozinho.
    """

    __tablename__ = "dose_rules"
    __table_args__ = (
        sa.UniqueConstraint(
            "price_list_item_id", "species", "route", name="uq_dose_rules_item_species_route"
        ),
        sa.Index("ix_dose_rules_clinic_item", "clinic_id", "price_list_item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"))
    price_list_item_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("price_list_items.id"))
    #: None = vale para qualquer espécie. A regra da espécie vence a genérica.
    species: Mapped[str | None] = mapped_column(sa.Text, default=None)
    route: Mapped[str | None] = mapped_column(sa.Text, default=None)

    dose_min_per_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4), default=None)
    dose_max_per_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4), default=None)
    dose_default_per_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4), default=None)
    fixed_dose_mg: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4), default=None)
    max_total_mg: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 4), default=None)
    frequency_minutes: Mapped[int | None] = mapped_column(sa.Integer, default=None)

    is_contraindicated: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    warning: Mapped[str | None] = mapped_column(sa.Text, default=None)
    breed_warning: Mapped[str | None] = mapped_column(sa.Text, default=None)
    #: Raças que disparam o aviso, separadas por vírgula.
    breeds: Mapped[str | None] = mapped_column(sa.Text, default=None)
    source: Mapped[str | None] = mapped_column(sa.Text, default=None)
    notes: Mapped[str | None] = mapped_column(sa.Text, default=None)

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=None
    )
    #: Nome denormalizado: o prontuário precisa continuar legível se o vínculo
    #: mudar, como em toda autoria clínica deste sistema.
    reviewed_by_name: Mapped[str | None] = mapped_column(sa.Text, default=None)

    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
