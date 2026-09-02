import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_session, require, require_read
from app.core.errors import AppError
from app.models.station_device import StationDevice
from app.permissions import CLINIC_CONFIGURE
from app.schemas.station_device import (
    StationDeviceCreate,
    StationDeviceOpened,
    StationDeviceOut,
    StationDeviceRename,
)
from app.services.audit import ActorInfo, AuditService
from app.services.station_device import StationDeviceService

router = APIRouter(prefix="/api/v1/station-devices", tags=["station-devices"])


async def _device(session: AsyncSession, device_id: uuid.UUID, clinic_id: uuid.UUID):
    device = await session.get(StationDevice, device_id)
    if device is None or device.clinic_id != clinic_id:
        raise AppError("not_found", 404)
    return device


@router.get("", response_model=list[StationDeviceOut])
async def list_devices(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    _: Annotated[ActorInfo, Depends(require_read(CLINIC_CONFIGURE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[StationDeviceOut]:
    """Quais aparelhos entram nesta clínica.

    A pergunta que a chave compartilhada não sabia responder. Sem esta lista,
    revogar um acesso significava trocar a chave e derrubar todo mundo."""
    rows = list(
        (
            await session.execute(
                sa.select(StationDevice)
                .where(StationDevice.clinic_id == auth.clinic_id)
                .order_by(StationDevice.created_at.desc())
            )
        ).scalars()
    )
    return [StationDeviceOut.model_validate(row) for row in rows]


@router.post("", response_model=StationDeviceOpened, status_code=201)
async def open_enrollment(
    payload: StationDeviceCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(CLINIC_CONFIGURE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StationDeviceOpened:
    """Abre a liberação de um aparelho e devolve o código de seis dígitos.

    O código sai em claro UMA vez, aqui: no banco fica só o hash, como toda
    senha do sistema. Vale cinco minutos, o tempo de digitá-lo no aparelho que
    está na mão de quem acabou de clicar."""
    device, code = await StationDeviceService.open_enrollment(
        session, clinic_id=auth.clinic_id, name=payload.name, actor=actor
    )
    await session.commit()
    return StationDeviceOpened(
        device=StationDeviceOut.model_validate(device),
        enrollment_code=code,
        expires_at=device.enrollment_expires_at,
    )


@router.patch("/{device_id}", response_model=StationDeviceOut)
async def rename_device(
    device_id: uuid.UUID,
    payload: StationDeviceRename,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(CLINIC_CONFIGURE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StationDeviceOut:
    device = await _device(session, device_id, auth.clinic_id)
    before = AuditService.snapshot(device)
    device.name = payload.name.strip()
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="station_device_renamed",
        entity_type="station_device",
        entity_id=device.id,
        before=before,
        after=AuditService.snapshot(device),
    )
    await session.commit()
    return StationDeviceOut.model_validate(device)


@router.post("/{device_id}/unlock", response_model=StationDeviceOut)
async def unlock_device(
    device_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(CLINIC_CONFIGURE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StationDeviceOut:
    """Libera o aparelho travado por erros de PIN.

    O bloqueio não expira sozinho de propósito: cinco PINs errados seguidos
    são alguém tentando adivinhar, e um cronômetro só faz essa pessoa esperar.
    Quem libera é um administrador, e fica na trilha quem foi."""
    device = await _device(session, device_id, auth.clinic_id)
    await StationDeviceService.unlock(session, device=device, actor=actor)
    await session.commit()
    return StationDeviceOut.model_validate(device)


@router.post("/{device_id}/revoke", response_model=StationDeviceOut)
async def revoke_device(
    device_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(CLINIC_CONFIGURE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StationDeviceOut:
    """Tira ESTE aparelho de circulação, sem tocar nos outros.

    É a diferença central para a chave compartilhada: perder um tablet deixava
    de ser motivo para trocar a chave da clínica no meio do plantão."""
    device = await _device(session, device_id, auth.clinic_id)
    await StationDeviceService.revoke(session, device=device, actor=actor)
    await session.commit()
    return StationDeviceOut.model_validate(device)
