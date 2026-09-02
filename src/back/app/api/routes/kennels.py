import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_session,
    get_tenant_obj,
    require,
)
from app.models import Kennel
from app.permissions import KENNEL_MANAGE
from app.schemas.kennel import KennelCreate, KennelOut, KennelUpdate
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.services.audit import ActorInfo, AuditService

router = APIRouter(prefix="/api/v1/kennels", tags=["kennels"])


@router.get("", response_model=Page[KennelOut])
async def list_kennels(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
    include_inactive: bool = False,
) -> Page[KennelOut]:
    stmt = sa.select(Kennel).where(Kennel.clinic_id == auth.clinic_id)
    if not include_inactive:
        stmt = stmt.where(Kennel.is_active.is_(True))
    rows, next_cursor = await paginate(
        session, stmt, id_column=Kennel.id, limit=limit, cursor=cursor
    )
    return Page[KennelOut](
        items=[KennelOut.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.get("/{kennel_id}", response_model=KennelOut)
async def get_kennel(
    kennel_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KennelOut:
    kennel = await get_tenant_obj(session, Kennel, kennel_id, auth.clinic_id)
    return KennelOut.model_validate(kennel)


@router.post("", response_model=KennelOut, status_code=201)
async def create_kennel(
    payload: KennelCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(KENNEL_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KennelOut:
    kennel = Kennel(clinic_id=auth.clinic_id, **payload.model_dump())
    session.add(kennel)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="kennel_created",
        entity_type="kennel",
        entity_id=kennel.id,
        after=AuditService.snapshot(kennel),
    )
    await session.commit()
    return KennelOut.model_validate(kennel)


@router.patch("/{kennel_id}", response_model=KennelOut)
async def update_kennel(
    kennel_id: uuid.UUID,
    payload: KennelUpdate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(KENNEL_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KennelOut:
    kennel = await get_tenant_obj(session, Kennel, kennel_id, auth.clinic_id)
    before = AuditService.snapshot(kennel)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(kennel, field, value)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="kennel_updated",
        entity_type="kennel",
        entity_id=kennel.id,
        before=before,
        after=AuditService.snapshot(kennel),
    )
    await session.commit()
    return KennelOut.model_validate(kennel)
