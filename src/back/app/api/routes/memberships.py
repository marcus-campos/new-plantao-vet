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
from app.core.errors import AppError
from app.core.security import hash_password
from app.models.membership import Membership
from app.models.user import User
from app.permissions import TEAM_MANAGE, TEAM_READ
from app.schemas.auth import SetPinRequest
from app.schemas.membership import (
    MembershipCreate,
    MembershipOut,
    MembershipRosterOut,
    MembershipUpdate,
)
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.services.audit import ActorInfo, AuditService
from app.services.pin import PinService

router = APIRouter(prefix="/api/v1/memberships", tags=["memberships"])


def _require_admin(auth: AuthContext) -> None:
    # Gestão de equipe é do admin logado; a estação nunca administra vínculos.
    if auth.kind != "personal" or auth.membership.role != "admin":
        raise AppError("forbidden", 403)


def _to_out(membership: Membership, user: User) -> MembershipOut:
    return MembershipOut(
        id=membership.id,
        user_id=user.id,
        name=user.name,
        email=user.email,
        role=membership.role,
        license_number=membership.license_number,
        license_authority=membership.license_authority,
        # has_pin, nunca pin_hash: o hash não sai da API em nenhuma rota.
        has_pin=membership.pin_hash is not None,
        is_active=membership.is_active,
    )


@router.get("/roster", response_model=list[MembershipRosterOut])
async def roster(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[MembershipRosterOut]:
    """Quem é quem na clínica: sem e-mail e sem PIN, liberado a todo membro."""
    rows = list(
        (
            await session.execute(
                sa.select(Membership, User.name)
                .join(User, User.id == Membership.user_id)
                .where(Membership.clinic_id == auth.clinic_id, Membership.is_active.is_(True))
                .order_by(User.name)
            )
        ).all()
    )
    return [
        MembershipRosterOut(
            id=membership.id,
            name=name,
            role=membership.role,
            license_number=membership.license_number,
            license_authority=membership.license_authority,
            is_active=membership.is_active,
        )
        for membership, name in rows
    ]


@router.post("/{membership_id}/pin", status_code=204)
async def set_pin(
    membership_id: uuid.UUID,
    body: SetPinRequest,
    auth: AuthContext = Depends(get_current_auth),
    actor: ActorInfo = Depends(require(TEAM_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    # v1: só o admin da clínica define/troca PINs (tela de gestão da semana 2)
    if auth.kind != "personal" or auth.membership.role != "admin":
        raise AppError("forbidden", 403)
    membership = await get_tenant_obj(session, Membership, membership_id, auth.clinic_id)
    await PinService.set_pin(session, membership=membership, pin=body.pin, actor=actor)
    await session.commit()


@router.get("", response_model=Page[MembershipOut])
async def list_memberships(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    # A lista traz e-mail e quem tem PIN, e não tinha gate nenhum: qualquer
    # técnico (e qualquer estação sem PIN) lia a equipe inteira, enquanto a
    # interface escondia o item de menu e dizia que só o admin via.
    # Quem só precisa de nomes usa `/memberships/roster`, que existe para isso.
    _actor: Annotated[ActorInfo, Depends(require_read(TEAM_READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
    include_inactive: bool = True,
) -> Page[MembershipOut]:
    # include_inactive default True: a tela de equipe precisa mostrar quem foi
    # desativado (não há DELETE) para poder reativar.
    stmt = sa.select(Membership).where(Membership.clinic_id == auth.clinic_id)
    if not include_inactive:
        stmt = stmt.where(Membership.is_active.is_(True))
    rows, next_cursor = await paginate(
        session, stmt, id_column=Membership.id, limit=limit, cursor=cursor
    )
    users = {
        user.id: user
        for user in (
            await session.execute(
                sa.select(User).where(User.id.in_([row.user_id for row in rows] or [None]))
            )
        ).scalars()
    }
    return Page[MembershipOut](
        items=[_to_out(row, users[row.user_id]) for row in rows], next_cursor=next_cursor
    )


@router.post("", response_model=MembershipOut, status_code=201)
async def create_membership(
    payload: MembershipCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(TEAM_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MembershipOut:
    _require_admin(auth)
    email = payload.email.strip().lower()
    # users.email é UNIQUE global: um e-mail já usado (nesta clínica ou em
    # outra) não vira usuário novo. Checamos antes para devolver o código
    # certo em vez de estourar IntegrityError.
    existing = (
        await session.execute(sa.select(User).where(sa.func.lower(User.email) == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError("validation_error", 422, field="email")

    user = User(name=payload.name, email=email, password_hash=hash_password(payload.password))
    session.add(user)
    await session.flush()
    membership = Membership(
        clinic_id=auth.clinic_id,
        user_id=user.id,
        role=payload.role,
        license_number=payload.license_number,
        license_authority=payload.license_authority,
    )
    session.add(membership)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="membership_created",
        entity_type="membership",
        entity_id=membership.id,
        after=AuditService.snapshot(membership),
        extra={"email": email, "name": payload.name},
    )
    await session.commit()
    return _to_out(membership, user)


@router.patch("/{membership_id}", response_model=MembershipOut)
async def update_membership(
    membership_id: uuid.UUID,
    payload: MembershipUpdate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(TEAM_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MembershipOut:
    _require_admin(auth)
    membership = await get_tenant_obj(session, Membership, membership_id, auth.clinic_id)
    before = AuditService.snapshot(membership)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(membership, field, value)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="membership_updated",
        entity_type="membership",
        entity_id=membership.id,
        before=before,
        after=AuditService.snapshot(membership),
    )
    await session.commit()
    user = await session.get(User, membership.user_id)
    return _to_out(membership, user)
