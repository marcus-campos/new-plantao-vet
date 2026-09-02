import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.owner_contact import ContactChannel, ContactDirection, ContactStatus


class OwnerContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hospitalization_id: uuid.UUID
    owner_id: uuid.UUID
    channel: ContactChannel
    direction: ContactDirection
    summary: str
    #: Estado real do contato. Sem isto a interface não tem como distinguir um
    #: boletim entregue de uma tentativa que nunca saiu – e mostrava as duas
    #: como a mesma linha de histórico.
    status: ContactStatus
    #: Nulo até o provedor confirmar. A tela mostra "não enviado", não uma hora.
    sent_at: datetime | None
    delivered_at: datetime | None
    read_at: datetime | None
    #: Diagnóstico do provedor, para a tela de histórico mostrar por que a
    #: mensagem não chegou. Não é mensagem de interface (ADR-0004): o que a UI
    #: traduz é o código de erro da resposta.
    failure_reason: str | None
    external_id: str | None
    membership_id: uuid.UUID | None
    author_name: str


class OwnerContactCreate(BaseModel):
    channel: ContactChannel
    direction: ContactDirection = ContactDirection.outbound
    summary: str = Field(min_length=1)
    # Contato registrado depois do fato (retroativo): sem isto, agora.
    sent_at: datetime | None = None


class WhatsAppBulletinRequest(BaseModel):
    # O texto do boletim vem pronto do cliente: conteúdo escrito pela clínica
    # não é traduzido pelo servidor (ADR-0004, regra 6).
    body: str = Field(min_length=1)
    # Resumo curto para o prontuário; sem isto, o próprio corpo.
    summary: str | None = Field(default=None, min_length=1)
