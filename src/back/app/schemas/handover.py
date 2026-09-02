import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.task import TaskOut


class HandoverReportOut(BaseModel):
    """O boletim se identifica sozinho: quem lê não precisa cruzar com o painel
    (que só lista internações ativas; boletim de paciente que teve alta ficaria
    órfão). O aceite vem junto para a barra de progresso sobreviver ao reload."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hospitalization_id: uuid.UUID
    from_shift_id: uuid.UUID | None
    to_shift_id: uuid.UUID | None
    skeleton: dict[str, Any]
    narrative: str | None
    # reviewed_at None é informação, não erro: o cliente desenha o selo
    # "não revisado" e mostra o boletim inteiro assim mesmo.
    reviewed_at: datetime | None
    reviewed_by: uuid.UUID | None
    created_at: datetime
    # Identificação do paciente junto do boletim.
    patient_name: str | None = None
    kennel_name: str | None = None
    # Aceite (se já houve): a barra de progresso sobrevive ao reload.
    acked_at: datetime | None = None
    acked_by_name: str | None = None
    seconds_to_ack: int | None = None
    #: O que continua em aberto AGORA, não no fechamento do turno.
    #:
    #: O esqueleto congela contadores; quem recebe o plantão precisa da lista.
    #: "3 pendentes" é um número. "Glicemia das 16h, atrasada 3h" é a coisa que
    #: a pessoa vai fazer. A pesquisa nomeia a síntese do receptor como o
    #: elemento mais negligenciado do I-PASS, e a spec é literal: pendências e
    #: atrasadas visíveis NO PRÓPRIO ATO do aceite.
    #:
    #: Ao vivo de propósito: entre o fechamento do turno e o aceite alguém pode
    #: ter dado a dose, e aceitar uma pendência que já não existe é ruído.
    open_tasks: list[TaskOut] = []


class HandoverNarrativeRequest(BaseModel):
    """O texto do boletim, escrito ou revisado por quem entrega.

    A rota só sabia GERAR: quem entrega o plantão podia mandar o servidor
    rascunhar de novo e nada mais. Não havia como corrigir uma frase nem
    escrever o boletim do próprio punho, e o rascunho é rascunho justamente
    porque quem assina é a pessoa, não o modelo. Sem `text`, gera.
    """

    text: str | None = Field(default=None, max_length=8000)


class HandoverAckRequest(BaseModel):
    # Tempo entre abrir e aceitar. Termômetro de "carimbo em série": medido,
    # nunca usado para bloquear.
    seconds_to_ack: int | None = Field(default=None, ge=0)


class HandoverAckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    handover_report_id: uuid.UUID
    membership_id: uuid.UUID | None
    acked_at: datetime
    seconds_to_ack: int | None
