import secrets
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def new_enrollment_code() -> str:
    """Seis dígitos, sorteados com o gerador criptográfico.

    `random` não serve: o código libera um aparelho a entrar na clínica, e um
    gerador previsível transformaria a liberação num palpite."""
    return f"{secrets.randbelow(1_000_000):06d}"


def new_device_secret() -> str:
    return secrets.token_urlsafe(32)


class StationDevice(Base):
    """Um aparelho compartilhado da clínica: o tablet do corredor, o balcão.

    Substitui a chave de estação, que era UMA senha para a clínica inteira.
    Três coisas quebravam com ela e não quebram aqui:

    1. **Revogar era tudo ou nada.** Trocar a chave porque um tablet sumiu
       derrubava todos os outros aparelhos ao mesmo tempo, no meio do plantão.
       Aqui cada aparelho tem segredo próprio: revogar um não toca nos demais.

    2. **Ninguém sabia quais aparelhos existiam.** A chave era um texto que
       circulava; não havia lista, nome, nem "visto pela última vez". Aqui há.

    3. **Bloquear por erro de PIN não durava nada.** O bloqueio vivia na
       memória do processo, chaveado por um `station_id` sorteado a cada
       login: relogar zerava a contagem, e um restart da API também. Agora o
       aparelho é a identidade, o bloqueio fica no banco, e sair dele é ato de
       um administrador, não passagem do tempo.

    O segredo e o código de liberação saem da API em claro UMA vez cada, na
    resposta que os cria. Aqui fica só o hash, como em toda senha do sistema.
    """

    __tablename__ = "station_devices"
    __table_args__ = (sa.Index("ix_station_devices_clinic_status", "clinic_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"))
    #: "Tablet da UTI". Quem revoga precisa saber o que está revogando: uma
    #: lista de identificadores não é uma lista de aparelhos.
    name: Mapped[str] = mapped_column(sa.Text)
    #: pending enquanto o código não foi usado; active depois; revoked no fim.
    status: Mapped[str] = mapped_column(sa.Text, default="pending")

    enrollment_code_hash: Mapped[str | None] = mapped_column(sa.Text, default=None)
    #: Minutos, não dias: um código de liberação que vale para sempre é uma
    #: chave compartilhada com outro nome.
    enrollment_expires_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=None
    )
    secret_hash: Mapped[str | None] = mapped_column(sa.Text, default=None)

    #: Erros de PIN seguidos NESTE aparelho. Zera a cada acerto.
    pin_failed_attempts: Mapped[int] = mapped_column(sa.Integer, default=0)
    #: Preenchido no quinto erro. Enquanto estiver preenchido o aparelho não
    #: troca PIN por operador nenhum, e só um administrador o libera.
    pin_locked_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=None
    )

    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    approved_by_name: Mapped[str | None] = mapped_column(sa.Text, default=None)
    approved_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    #: Sem isto a lista não responde "este aparelho ainda está em uso?", que é
    #: a única pergunta que faz alguém revogar um.
    last_seen_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
