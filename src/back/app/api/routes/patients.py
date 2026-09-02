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
from app.core.errors import AppError
from app.models import Clinic, Owner, Patient
from app.models.patient_identifier import PatientIdentifier
from app.permissions import PATIENT_REGISTER
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.schemas.patient import (
    PatientCreate,
    PatientIdentifierOut,
    PatientOut,
    PatientRegister,
    PatientSearchHit,
    PatientUpdate,
)
from app.services.audit import ActorInfo, AuditService
from app.services.patient_search import PatientSearchService, normalize_tax_id

router = APIRouter(prefix="/api/v1/patients", tags=["patients"])


@router.get("", response_model=Page[PatientOut])
async def list_patients(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
    include_inactive: bool = False,
) -> Page[PatientOut]:
    stmt = sa.select(Patient).where(Patient.clinic_id == auth.clinic_id)
    if not include_inactive:
        stmt = stmt.where(Patient.is_active.is_(True))
    rows, next_cursor = await paginate(
        session, stmt, id_column=Patient.id, limit=limit, cursor=cursor
    )
    return Page[PatientOut](
        items=[PatientOut.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.get("/search", response_model=list[PatientSearchHit])
async def search_patients(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str = "",
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
) -> list[PatientSearchHit]:
    """Uma caixa só: nome, microchip, CPF, CNS ou nome/documento do responsável."""
    hits = await PatientSearchService.search(
        session, clinic_id=auth.clinic_id, query=q, limit=limit
    )
    return [
        PatientSearchHit(
            id=hit.patient.id,
            name=hit.patient.name,
            species=hit.patient.species,
            breed=hit.patient.breed,
            owner_id=hit.owner.id,
            owner_name=hit.owner.name,
            identifiers=[PatientIdentifierOut.model_validate(i) for i in hit.identifiers],
            active_hospitalization_id=hit.active_hospitalization_id,
        )
        for hit in hits
    ]


@router.post("/register", response_model=PatientOut, status_code=201)
async def register_patient(
    payload: PatientRegister,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PATIENT_REGISTER))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PatientOut:
    """Paciente + responsável + identificadores num passo só."""
    clinic = await session.get(Clinic, auth.clinic_id)

    if payload.owner_id is not None:
        owner = await get_tenant_obj(session, Owner, payload.owner_id, auth.clinic_id)
    else:
        owner = Owner(
            clinic_id=auth.clinic_id,
            name=payload.owner_name,
            phone_e164=payload.owner_phone_e164,
            tax_id=normalize_tax_id(payload.owner_tax_id),
        )
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

    patient = Patient(
        clinic_id=auth.clinic_id,
        owner_id=owner.id,
        name=payload.name,
        species=payload.species,
        breed=payload.breed,
        weight_kg=payload.weight_kg,
        notes=payload.notes,
    )
    session.add(patient)
    await session.flush()

    for entry in payload.identifiers:
        value = PatientSearchService.validate_identifier(clinic, entry.kind, entry.value)
        existing = await session.scalar(
            sa.select(PatientIdentifier).where(
                PatientIdentifier.clinic_id == auth.clinic_id,
                PatientIdentifier.kind == entry.kind,
                PatientIdentifier.value == value,
            )
        )
        if existing is not None:
            # O mesmo microchip/CPF não pode apontar para dois pacientes: é o que
            # garante que a busca devolva UM paciente.
            raise AppError("identifier_taken", 409, kind=entry.kind)
        session.add(
            PatientIdentifier(
                clinic_id=auth.clinic_id, patient_id=patient.id, kind=entry.kind, value=value
            )
        )
    await session.flush()

    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="patient_created",
        entity_type="patient",
        entity_id=patient.id,
        after=AuditService.snapshot(patient),
        extra={"identifiers": [entry.kind for entry in payload.identifiers]},
    )
    await session.commit()
    return PatientOut.model_validate(patient)


@router.get("/{patient_id}", response_model=PatientOut)
async def get_patient(
    patient_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PatientOut:
    patient = await get_tenant_obj(session, Patient, patient_id, auth.clinic_id)
    return PatientOut.model_validate(patient)


@router.post("", response_model=PatientOut, status_code=201)
async def create_patient(
    payload: PatientCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PATIENT_REGISTER))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PatientOut:
    # FK vinda do body é validada contra o tenant: 404, nunca 403, para não vazar existência.
    await get_tenant_obj(session, Owner, payload.owner_id, auth.clinic_id)
    patient = Patient(clinic_id=auth.clinic_id, **payload.model_dump())
    session.add(patient)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="patient_created",
        entity_type="patient",
        entity_id=patient.id,
        after=AuditService.snapshot(patient),
    )
    await session.commit()
    return PatientOut.model_validate(patient)


@router.patch("/{patient_id}", response_model=PatientOut)
async def update_patient(
    patient_id: uuid.UUID,
    payload: PatientUpdate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PATIENT_REGISTER))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PatientOut:
    patient = await get_tenant_obj(session, Patient, patient_id, auth.clinic_id)
    before = AuditService.snapshot(patient)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="patient_updated",
        entity_type="patient",
        entity_id=patient.id,
        before=before,
        after=AuditService.snapshot(patient),
    )
    await session.commit()
    return PatientOut.model_validate(patient)
