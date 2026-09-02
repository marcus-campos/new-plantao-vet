import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.task import TaskOut


class BoardCounters(BaseModel):
    """Pendentes por estado, mais o que já foi feito HOJE (dia da clínica).

    `on_time`/`due`/`overdue` contam só pendentes: é a fila que resta. O
    painel exibia esses três somados sob o rótulo "feitas", então dizia
    "9 de 12 feitas" quando nada tinha sido feito. `done_today` e
    `planned_today` existem para que a frase possa ser verdadeira.
    """

    on_time: int = 0
    due: int = 0
    overdue: int = 0
    done_today: int = 0
    planned_today: int = 0


class BoardAttention(BaseModel):
    """Por que este paciente precisa de atenção. Ausente = está em dia.

    Um motivo só, o mais grave. `magnitude` são minutos de atraso, ou horas sem
    evolução quando `reason == "no_progress_note"`. O cliente escolhe a frase
    pelo código, porque a API nunca devolve prosa (ADR-0004).
    """

    model_config = ConfigDict(from_attributes=True)

    reason: str
    severity: int
    magnitude: int | None = None
    task_title: str | None = None


class BoardRow(BaseModel):
    hospitalization_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    species: str | None
    #: O id vem junto do nome porque o mapa de boxes cruzava paciente e box por
    #: igualdade de string: renomear um box ocupado mudava o paciente de lugar.
    kennel_id: uuid.UUID | None
    kennel_name: str | None
    admitted_at: datetime
    next_task: TaskOut | None
    counters: BoardCounters
    critical_overdue: bool
    attention: BoardAttention | None = None


class BoardShift(BaseModel):
    """O turno que está correndo agora, e se é o meu."""

    id: uuid.UUID
    name: str
    starts_at: datetime
    ends_at: datetime
    membership_id: uuid.UUID
    member_name: str | None
    is_vet_responsible: bool
    is_mine: bool


class BoardTotals(BaseModel):
    patients: int
    due: int
    overdue: int
    #: Quantos pacientes têm algum motivo de atenção. É o número que vale a
    #: manchete: "3 pacientes precisam de atenção" decide, "40 atrasadas" não.
    attention: int
    #: A fila em três baldes de tempo.
    now: int
    next_hour: int
    later: int


class BoardOut(BaseModel):
    #: O relógio do SERVIDOR e o fuso da CLÍNICA. Sem eles o cliente formatava
    #: horários calculados no fuso da clínica usando o relógio do aparelho:
    #: um quiosque em UTC mostrava a dose das 10h como 13h, sem aviso.
    now: datetime
    timezone: str
    shifts: list[BoardShift]
    totals: BoardTotals
    rows: list[BoardRow]
