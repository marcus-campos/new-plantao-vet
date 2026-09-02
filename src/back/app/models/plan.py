import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Plan(Base):
    """Um plano comercial: o pacote que a clínica assina.

    Era um dicionário no código (`PLAN_TIERS`), e quem vende não muda código
    para lançar um plano. O ciclo de vida que um plano tem na prática:

    1. **Nasce** com nome, limite de leitos e preço. Um plano de teste é um
       plano com `trial_days > 0`: quem entra nele começa em `trial` com a
       data de fim já calculada.
    2. **Vive** enquanto `is_active`: pode ser atribuído a clínica nova.
    3. **Aposenta** (`retired_at`): ninguém novo entra, quem está continua.
       É o caso do plano "fundador": preço de lançamento para as primeiras
       clínicas, que depois migram para o definitivo.
    4. **Migra**: todas as clínicas de um plano vão para outro num ato só,
       com o limite do plano novo e uma entrada na trilha de cada clínica.

    `code` é a chave que `clinics.plan_tier` referencia. Não muda depois de
    criado: é o que aparece em toda trilha de auditoria já gravada.
    """

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(sa.Text, unique=True)
    name: Mapped[str] = mapped_column(sa.Text)
    #: None = sem limite (enterprise). O limite é suave: nunca bloqueia uma
    #: admissão, só avisa o administrador.
    bed_limit: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    #: Em unidade menor (centavos), como todo dinheiro do sistema. Zero é
    #: legítimo: teste e cortesia custam zero.
    price_minor: Mapped[int] = mapped_column(sa.Integer, default=0)
    currency: Mapped[str] = mapped_column(sa.String(3), default="BRL")
    #: Zero = plano pago. Maior que zero = plano de teste com esta duração.
    trial_days: Mapped[int] = mapped_column(sa.Integer, default=0)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(sa.Integer, default=0)
    #: Para quem vende: "preço de lançamento, válido até dez/2026".
    notes: Mapped[str | None] = mapped_column(sa.Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
    retired_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
