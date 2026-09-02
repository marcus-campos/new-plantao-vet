"""Registro do aparelho que recebe o alerta.

A metade que faltava do fluxo de notificação: o app pedia permissão, obtinha o
token e não tinha para onde mandá-lo. Pedir permissão de notificação e não
conseguir notificar é a pior combinação possível: a pessoa acha que está
coberta.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_operator, get_session
from app.models.device import Device
from app.services.audit import ActorInfo
from app.services.push import PushClient

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


class DeviceRegister(BaseModel):
    token: str = Field(min_length=8, max_length=512)
    platform: Literal["ios", "android", "web", "unknown"] = "unknown"


class DeviceOut(BaseModel):
    """O token NÃO volta na resposta.

    Quem registrou já o tem, e um token de push é credencial de entrega: ecoá-lo
    o espalha por log de proxy e histórico de rede sem nada em troca."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    platform: str
    is_active: bool
    created_at: datetime
    last_seen_at: datetime | None
    #: O provedor consegue entregar neste token? O app registra token do Expo,
    #: que o transporte FCM não conhece; dizer "registrado" sem dizer isto
    #: seria a mesma promessa vazia de antes, um andar abaixo.
    deliverable: bool


@router.post("", response_model=DeviceOut)
async def register_device(
    payload: DeviceRegister,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceOut:
    """Registra ou atualiza o token de quem está com o aparelho AGORA.

    Idempotente pelo token, e o conflito MOVE o registro em vez de recusar: o
    celular da enfermaria passa de mão a cada troca de turno, e um token que
    continua notificando o plantonista anterior manda dado clínico para quem
    saiu e, pior, deixa quem está de plantão sem o alerta.

    Sem capacidade própria de propósito: registrar o PRÓPRIO aparelho é direito
    de qualquer papel. O que se exige é identidade: na estação, o dono do PIN
    (`get_operator`), porque um tablet compartilhado sem ninguém identificado
    não tem a quem notificar.
    """
    now = datetime.now(UTC)
    # Upsert no banco, não read-modify-write: dois registros simultâneos do
    # mesmo token (app reabrindo em duas telas) bateriam na unique e virariam
    # 500 no meio do login.
    stmt = (
        insert(Device)
        .values(
            id=uuid.uuid4(),
            clinic_id=auth.clinic_id,
            membership_id=actor.membership_id,
            token=payload.token,
            platform=payload.platform,
            is_active=True,
            created_at=now,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            # O índice é por token no sistema inteiro: o mesmo aparelho pode
            # ser entregue a alguém de outra clínica, e o registro segue o
            # aparelho.
            index_elements=["token"],
            set_={
                "clinic_id": auth.clinic_id,
                "membership_id": actor.membership_id,
                "platform": payload.platform,
                "is_active": True,
                "last_seen_at": now,
            },
        )
        .returning(Device)
    )
    device = (await session.execute(stmt)).scalar_one()
    await session.commit()
    return DeviceOut(
        id=device.id,
        platform=device.platform,
        is_active=device.is_active,
        created_at=device.created_at,
        last_seen_at=device.last_seen_at,
        deliverable=not PushClient.is_expo_token(device.token),
    )


@router.delete("/{token:path}", status_code=204)
async def remove_device(
    token: str,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Logout ou desinstalação: o token sai do banco.

    Apaga em vez de desativar: o token identifica um aparelho de uma pessoa
    (LGPD), e linha inativa que ninguém limpa é o mesmo vazamento adiado.

    Idempotente: token que já não existe também devolve 204. Erro no logout
    prenderia a pessoa numa sessão que ela já encerrou, e o efeito pretendido
    (este token não notifica mais) está garantido de qualquer jeito.
    """
    await session.execute(
        sa.delete(Device).where(Device.clinic_id == auth.clinic_id, Device.token == token)
    )
    await session.commit()
