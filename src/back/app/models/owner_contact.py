import uuid
from datetime import UTC, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ContactChannel(StrEnum):
    phone = "phone"
    whatsapp = "whatsapp"
    in_person = "in_person"


class ContactDirection(StrEnum):
    outbound = "outbound"
    inbound = "inbound"


class ContactStatus(StrEnum):
    """Estado real do contato: o que impede o prontuário de mentir.

    Antes só existia `sent_at`, gravado sempre: um envio que nunca saiu ficava
    no prontuário como entrega auditada. Agora `sent_at` só é preenchido
    quando o provedor confirmou, e a tentativa que falhou fica registrada
    como tentativa (o log de tentativas de contato é história clínica: mostra
    que a clínica tentou avisar o tutor e não conseguiu).

    `queued` existe para envio assíncrono futuro; hoje ninguém o grava.
    """

    queued = "queued"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"


#: Ordem monotônica dos estados. O webhook da Meta reenvia e chega fora de
#: ordem: um callback `sent` que chega depois do `read` não pode desfazer a
#: leitura, e reprocessar o mesmo callback não pode mudar nada. A regra é uma
#: só. Só avança quem tem posto maior. `failed` fica acima de `sent` (uma
#: mensagem enviada pode ser declarada indeliverável) e abaixo de
#: `delivered`/`read` (o que já chegou não volta a falhar).
STATUS_ORDER: dict[str, int] = {
    ContactStatus.queued: 0,
    ContactStatus.sent: 1,
    ContactStatus.failed: 2,
    ContactStatus.delivered: 3,
    ContactStatus.read: 4,
}


def _enum(enum_cls: type[StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda e: [m.value for m in e],
    )


class OwnerContact(Base):
    """Log estruturado de cada contato com o tutor (spec §2, "Tutor (Owner)").

    O registro é imutável na prática: a clínica acrescenta contatos, nunca
    reescreve o histórico. delivered_at/read_at só são preenchidos pelo canal
    WhatsApp (webhook de status da Meta Cloud API); nos canais phone e
    in_person eles permanecem nulos, e `status` fica em `sent` porque o
    telefonema e a conversa presencial são registrados depois de acontecerem.
    """

    __tablename__ = "owner_contacts"
    __table_args__ = (
        sa.Index("ix_owner_contacts_hospitalization_sent", "hospitalization_id", "sent_at"),
        sa.Index(
            "ix_owner_contacts_hospitalization_created", "hospitalization_id", "created_at"
        ),
        # Parcial: só o canal WhatsApp tem wamid, e é a única chave de busca
        # do webhook, que a Meta reenvia.
        sa.Index(
            "ix_owner_contacts_external_id",
            "external_id",
            postgresql_where=sa.text("external_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    hospitalization_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("hospitalizations.id")
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("owners.id"))
    channel: Mapped[ContactChannel] = mapped_column(_enum(ContactChannel, "contact_channel"))
    direction: Mapped[ContactDirection] = mapped_column(
        _enum(ContactDirection, "contact_direction"), default=ContactDirection.outbound
    )
    summary: Mapped[str] = mapped_column(sa.Text)
    #: `sent` é o default de propósito: telefonema e conversa presencial são
    #: registrados DEPOIS de acontecerem, e as linhas migradas antes do
    #: webhook também descrevem contatos que de fato ocorreram.
    status: Mapped[ContactStatus] = mapped_column(
        _enum(ContactStatus, "contact_status"), default=ContactStatus.sent
    )
    #: Diagnóstico cru do provedor (ex.: "meta_131047: Re-engagement message").
    #: Não é texto de interface: a UI traduz o CÓDIGO de erro da resposta e
    #: mostra isto como detalhe técnico do log.
    failure_reason: Mapped[str | None] = mapped_column(sa.Text, default=None)
    #: NULO enquanto o envio não foi confirmado pelo provedor. Preenchê-lo em
    #: qualquer outro momento é afirmar uma entrega que não houve.
    #:
    #: SEM default Python de propósito: o SQLAlchemy dispara o default quando o
    #: atributo vale None no flush, então `sent_at=None` num envio que falhou
    #: voltaria a gravar `now()`, exatamente a mentira que esta entrega
    #: remove. Quem registra um contato que aconteceu passa o instante.
    sent_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    delivered_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=None
    )
    read_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    # id da mensagem no provedor (wamid da Meta Cloud API); nulo fora do WhatsApp.
    external_id: Mapped[str | None] = mapped_column(sa.Text, default=None)
    # Quem registrou: nulo quando o contato é inbound e não passou por operador.
    membership_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    # Nome denormalizado: o prontuário precisa continuar legível se o vínculo mudar.
    author_name: Mapped[str] = mapped_column(sa.Text, default="")
    #: Quando a TENTATIVA aconteceu. `sent_at` só existe quando o provedor
    #: confirmou, então duas falhas ficavam sem ordem entre si, num log cuja
    #: razão de existir é a cronologia do contato com o tutor.
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
