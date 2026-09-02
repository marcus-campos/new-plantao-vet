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
    require_read,
)
from app.models import Owner
from app.permissions import OWNER_READ, PATIENT_REGISTER
from app.schemas.owner import OwnerCreate, OwnerOut, OwnerUpdate
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.services.audit import ActorInfo, AuditService
from app.services.patient_search import normalize_tax_id

router = APIRouter(prefix="/api/v1/owners", tags=["owners"])


@router.get("", response_model=Page[OwnerOut])
async def list_owners(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    # Telefone E.164 e CPF: exatamente os dois campos que a auditoria se recusa
    # a registrar (`AuditService.REDACTED`) e que a lista devolvia a qualquer
    # token, inclusive a uma estação sem ninguém identificado.
    _actor: Annotated[ActorInfo, Depends(require_read(OWNER_READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
    include_inactive: bool = False,
) -> Page[OwnerOut]:
    stmt = sa.select(Owner).where(Owner.clinic_id == auth.clinic_id)
    if not include_inactive:
        stmt = stmt.where(Owner.is_active.is_(True))
    rows, next_cursor = await paginate(
        session, stmt, id_column=Owner.id, limit=limit, cursor=cursor
    )
    return Page[OwnerOut](
        items=[OwnerOut.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.get("/{owner_id}", response_model=OwnerOut)
async def get_owner(
    owner_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    _actor: Annotated[ActorInfo, Depends(require_read(OWNER_READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OwnerOut:
    owner = await get_tenant_obj(session, Owner, owner_id, auth.clinic_id)
    return OwnerOut.model_validate(owner)


@router.post("", response_model=OwnerOut, status_code=201)
async def create_owner(
    payload: OwnerCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PATIENT_REGISTER))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OwnerOut:
    valores = payload.model_dump()
    valores["tax_id"] = normalize_tax_id(valores.get("tax_id"))
    owner = Owner(clinic_id=auth.clinic_id, **valores)
    session.add(owner)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="owner_created",
        entity_type="owner",
        entity_id=owner.id,
        after=AuditService.snapshot(owner),
    )
    await session.commit()
    return OwnerOut.model_validate(owner)


@router.patch("/{owner_id}", response_model=OwnerOut)
async def update_owner(
    owner_id: uuid.UUID,
    payload: OwnerUpdate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PATIENT_REGISTER))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OwnerOut:
    owner = await get_tenant_obj(session, Owner, owner_id, auth.clinic_id)
    before = AuditService.snapshot(owner)
    mudancas = payload.model_dump(exclude_unset=True)
    if "tax_id" in mudancas:
        # Mesma forma canônica da criação: um caminho de escrita que escapa
        # basta para o mesmo CPF existir em dois formatos no banco.
        mudancas["tax_id"] = normalize_tax_id(mudancas["tax_id"])
    for field, value in mudancas.items():
        setattr(owner, field, value)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="owner_updated",
        entity_type="owner",
        entity_id=owner.id,
        before=before,
        after=AuditService.snapshot(owner),
    )
    await session.commit()
    return OwnerOut.model_validate(owner)
