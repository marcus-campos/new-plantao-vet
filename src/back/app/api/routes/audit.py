import uuid
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_session
from app.core.errors import AppError
from app.models import AuditEntry
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_name: str
    actor_license: str | None
    actor_license_authority: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    payload: dict[str, Any]
    entry_hash: str
    created_at: datetime


@router.get("", response_model=Page[AuditEntryOut])
async def list_audit(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
) -> Page[AuditEntryOut]:
    if auth.membership is None or auth.membership.role == "tech":
        raise AppError("forbidden", 403)

    stmt = sa.select(AuditEntry).where(AuditEntry.clinic_id == auth.clinic_id)
    if entity_type is not None:
        stmt = stmt.where(AuditEntry.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditEntry.entity_id == entity_id)
    if cursor is not None:
        stmt = stmt.where(AuditEntry.id < int(cursor))
    stmt = stmt.order_by(AuditEntry.id.desc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(rows[-1].id)
    return Page[AuditEntryOut](
        items=[AuditEntryOut.model_validate(row) for row in rows], next_cursor=next_cursor
    )
