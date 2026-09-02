import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StationDeviceOut(BaseModel):
    """O que a lista de aparelhos mostra.

    Nem o segredo nem o código de liberação aparecem aqui: os dois saem em
    claro uma única vez, na resposta que os cria."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: str
    #: Responde "este aparelho ainda está em uso?", que é a única pergunta que
    #: faz alguém decidir revogar um.
    last_seen_at: datetime | None
    created_at: datetime
    approved_at: datetime | None
    approved_by_name: str | None
    revoked_at: datetime | None
    #: Preenchido = travado por erros de PIN. Sair daí é ato de administrador.
    pin_locked_at: datetime | None
    pin_failed_attempts: int
    #: Enquanto isto está no futuro, o aparelho ainda pode usar o código.
    enrollment_expires_at: datetime | None


class StationDeviceCreate(BaseModel):
    #: "Tablet da UTI". Sem nome, revogar é escolher entre identificadores.
    name: str = Field(min_length=1, max_length=80)


class StationDeviceRename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class StationDeviceOpened(BaseModel):
    device: StationDeviceOut
    #: Em claro UMA vez. Depois disto, só o hash existe.
    enrollment_code: str
    expires_at: datetime | None
