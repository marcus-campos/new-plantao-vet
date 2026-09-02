import uuid
from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_session,
    get_tenant_obj,
    require,
)
from app.core.errors import AppError
from app.models import Clinic, Hospitalization, Patient
from app.models.progress_note import ProgressNote
from app.permissions import PROGRESS_NOTE_SIGN
from app.schemas.progress_note import (
    ComplianceAlerts,
    MissingProgressNoteAlert,
    ProgressNoteCreate,
    ProgressNoteOut,
)
from app.services.audit import ActorInfo
from app.services.progress_notes import ProgressNoteService

router = APIRouter(prefix="/api/v1/hospitalizations", tags=["progress-notes"])
compliance_router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])


@router.post(
    "/{hospitalization_id}/progress-notes", response_model=ProgressNoteOut, status_code=201
)
async def sign(
    hospitalization_id: uuid.UUID,
    payload: ProgressNoteCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PROGRESS_NOTE_SIGN))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProgressNoteOut:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    if payload.amends_progress_note_id is not None:
        amended = await get_tenant_obj(
            session, ProgressNote, payload.amends_progress_note_id, auth.clinic_id
        )
        # Adendo corrige a evolução DESTA internação, não de outra.
        if amended.hospitalization_id != hospitalization.id:
            raise AppError("validation_error", 422, field="amends_progress_note_id")

    clinic = await session.get(Clinic, auth.clinic_id)
    note = await ProgressNoteService.create(
        session, hospitalization=hospitalization, clinic=clinic, actor=actor, payload=payload
    )
    await session.commit()
    return ProgressNoteOut.model_validate(note)


@router.get("/{hospitalization_id}/progress-notes", response_model=list[ProgressNoteOut])
async def list_notes(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProgressNoteOut]:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    rows = (
        await session.execute(
            sa.select(ProgressNote)
            .where(
                ProgressNote.clinic_id == auth.clinic_id,
                ProgressNote.hospitalization_id == hospitalization.id,
            )
            .order_by(ProgressNote.signed_at.desc())
        )
    ).scalars()
    return [ProgressNoteOut.model_validate(row) for row in rows]


@compliance_router.get("/alerts", response_model=ComplianceAlerts)
async def alerts(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ComplianceAlerts:
    """"Internado sem evolução há 24h", a obrigação do CFMV virada em alerta."""
    now = datetime.now(UTC)
    overdue = await ProgressNoteService.overdue_hospitalizations(
        session, clinic_id=auth.clinic_id, now=now
    )
    items: list[MissingProgressNoteAlert] = []
    for hospitalization_id in overdue:
        hospitalization = await session.get(Hospitalization, hospitalization_id)
        patient = await session.get(Patient, hospitalization.patient_id)
        items.append(
            MissingProgressNoteAlert(
                hospitalization_id=hospitalization_id,
                patient_name=patient.name if patient else None,
                hours_since=await ProgressNoteService.hours_since_last(
                    session, hospitalization_id=hospitalization_id, now=now
                ),
            )
        )
    return ComplianceAlerts(missing_progress_note=items)
