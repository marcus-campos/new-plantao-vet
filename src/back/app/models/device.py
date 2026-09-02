import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Device(Base):
    """O aparelho que recebe o alerta no bolso.

    O app pedia permissão de notificação, obtinha o token do Expo e o jogava
    fora: não havia rota para registrá-lo nem tabela para guardá-lo. O
    plantonista autorizava o alerta e o sistema não tinha como cumprir.

    `token` é único no sistema INTEIRO, não por clínica: o token é do aparelho,
    não do vínculo. O celular da enfermaria passa de mão em mão, e registrar de
    novo MOVE o token para quem está com ele agora: token que continua
    notificando o dono anterior é vazamento de dado clínico, não folga de
    cadastro.
    """

    __tablename__ = "devices"
    __table_args__ = (
        sa.UniqueConstraint("token", name="uq_devices_token"),
        sa.Index("ix_devices_membership", "membership_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"))
    membership_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("memberships.id"))
    token: Mapped[str] = mapped_column(sa.Text)
    platform: Mapped[str] = mapped_column(sa.Text, default="unknown")
    #: Desligado quando o provedor responde que o token morreu (app desinstalado,
    #: token rotacionado). Continuar tentando é gastar orçamento com ninguém.
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=None
    )
