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
from app.models import (
    Clinic,
    Hospitalization,
    Kennel,
    Membership,
    Patient,
    Prescription,
    Task,
    User,
)
from app.permissions import (
    HOSPITALIZATION_ADMIT,
    HOSPITALIZATION_DISCHARGE,
    PRESCRIPTION_CREATE,
)
from app.schemas.hospitalization import (
    DrugDoseOut,
    FastingStart,
    HospitalizationCreate,
    HospitalizationCreated,
    HospitalizationDetail,
    HospitalizationOut,
    OutcomeRequest,
    PatientSummary,
    VitalKindOut,
)
from app.schemas.prescription import PrescriptionOut
from app.schemas.task import TaskOut
from app.services.audit import ActorInfo, AuditService
from app.services.hospitalization import HospitalizationService
from app.services.tasks import TaskService
from app.vitals import list_vitals

router = APIRouter(prefix="/api/v1/hospitalizations", tags=["hospitalizations"])


@router.post("", response_model=HospitalizationCreated, status_code=201)
async def admit(
    payload: HospitalizationCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(HOSPITALIZATION_ADMIT))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalizationCreated:
    # Regra transversal 2: toda FK de body é validada contra o tenant.
    await get_tenant_obj(session, Patient, payload.patient_id, auth.clinic_id)
    await get_tenant_obj(session, Membership, payload.vet_membership_id, auth.clinic_id)
    if payload.kennel_id is not None:
        await get_tenant_obj(session, Kennel, payload.kennel_id, auth.clinic_id)

    clinic = await session.get(Clinic, auth.clinic_id)
    hospitalization, warning = await HospitalizationService.admit(
        session, clinic=clinic, payload=payload, actor=actor
    )
    await HospitalizationService.create_default_prescriptions(
        session, hospitalization=hospitalization, clinic=clinic, actor=actor
    )
    await session.commit()
    return HospitalizationCreated(
        hospitalization=HospitalizationOut.model_validate(hospitalization), warning=warning
    )


@router.get("/{hospitalization_id}", response_model=HospitalizationDetail)
async def detail(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalizationDetail:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    prescriptions = list(
        (
            await session.execute(
                sa.select(Prescription)
                .where(
                    Prescription.hospitalization_id == hospitalization.id,
                    Prescription.suspended_at.is_(None),
                )
                .order_by(Prescription.starts_at)
            )
        ).scalars()
    )
    now = datetime.now(UTC)
    clinic = await session.get(Clinic, auth.clinic_id)
    window_start, window_end = TaskService.default_window(clinic, now)
    tasks = list(
        (
            await session.execute(
                sa.select(Task)
                .where(
                    Task.hospitalization_id == hospitalization.id,
                    Task.status != "cancelled",
                    TaskService.queue_criteria(now, window_start, window_end),
                )
                .order_by(Task.scheduled_for)
            )
        ).scalars()
    )
    patient = await session.get(Patient, hospitalization.patient_id)
    kennel = (
        await session.get(Kennel, hospitalization.kennel_id)
        if hospitalization.kennel_id
        else None
    )
    vet_membership = await session.get(Membership, hospitalization.vet_membership_id)
    vet_user = await session.get(User, vet_membership.user_id) if vet_membership else None
    # A faixa de referência vai junto da ficha, resolvida para a espécie deste
    # paciente: é ela que transforma "82" em achado. Sem isso a medição existia
    # no banco e não significava nada na tela.
    vitals = [
        VitalKindOut.from_kind(vital, patient.species if patient else None)
        for vital in list_vitals()
    ]
    doses = await TaskService.dose_counters(
        session, hospitalization_id=hospitalization.id, now=now
    )
    return HospitalizationDetail(
        hospitalization=HospitalizationOut.model_validate(hospitalization),
        patient=PatientSummary.model_validate(patient) if patient else None,
        kennel_name=kennel.name if kennel else None,
        vet_name=vet_user.name if vet_user else None,
        vet_license=vet_membership.license_number if vet_membership else None,
        prescriptions=[PrescriptionOut.model_validate(row) for row in prescriptions],
        tasks=[TaskOut.from_task(row, now) for row in tasks],
        vitals=vitals,
        drug_doses=[DrugDoseOut.model_validate(row) for row in doses],
    )


@router.get("", response_model=list[HospitalizationOut])
async def list_hospitalizations(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    patient_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[HospitalizationOut]:
    """As internações de um paciente, da mais recente para a mais antiga.

    Dar alta fazia o paciente sumir: o painel só lista internação ATIVA e a
    busca só devolvia `active_hospitalization_id`, então quem acabou de dar alta
    não tinha caminho nenhum de volta para a conta ou para o prontuário, que é
    justamente o que se faz depois da alta (cópia ao tutor em 5 dias úteis,
    perfil `br`). A única ação oferecida era internar de novo.
    """
    stmt = sa.select(Hospitalization).where(Hospitalization.clinic_id == auth.clinic_id)
    if patient_id is not None:
        stmt = stmt.where(Hospitalization.patient_id == patient_id)
    if status is not None:
        stmt = stmt.where(Hospitalization.status == status)
    rows = list(
        (
            await session.execute(
                stmt.order_by(Hospitalization.admitted_at.desc()).limit(min(limit, 200))
            )
        ).scalars()
    )
    return [HospitalizationOut.model_validate(row) for row in rows]


@router.post("/{hospitalization_id}/outcome", response_model=HospitalizationOut)
async def close(
    hospitalization_id: uuid.UUID,
    payload: OutcomeRequest,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(HOSPITALIZATION_DISCHARGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalizationOut:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    # Só o que JÁ era para ter sido feito. Dar alta cancela as doses futuras:
    # é o que a alta significa. Contá-las aqui faria a confirmação aparecer em
    # toda alta, e confirmação que aparece sempre é confirmação que ninguém lê.
    now = datetime.now(UTC)
    pending = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Task)
        .where(
            Task.hospitalization_id == hospitalization.id,
            Task.status == "pending",
            Task.scheduled_for <= now,
        )
    )
    if pending and not payload.confirm_pending_tasks:
        raise AppError("pending_tasks_confirmation_required", 409, pending=pending)
    await HospitalizationService.close(
        session,
        hospitalization=hospitalization,
        outcome=payload.outcome,
        note=payload.note,
        actor=actor,
    )
    cancelled = await TaskService.cancel_pending(
        session, clinic_id=auth.clinic_id, hospitalization_id=hospitalization.id
    )
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="tasks_cancelled_on_outcome",
        entity_type="hospitalization",
        entity_id=hospitalization.id,
        extra={"cancelled_tasks": cancelled},
    )
    await session.commit()
    return HospitalizationOut.model_validate(hospitalization)


@router.post("/{hospitalization_id}/fasting", response_model=HospitalizationOut)
async def start_fasting(
    hospitalization_id: uuid.UUID,
    payload: FastingStart,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PRESCRIPTION_CREATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalizationOut:
    """Coloca o paciente em jejum.

    Sob `prescription.create`, porque jejum é ORDEM CLÍNICA, não operação de
    plantão: suspende a nutrição prescrita, e suspender prescrição é privativo
    de quem tem registro no conselho. Nada disso trava quem está ao lado do
    box: a alimentação durante o jejum avisa e segue com override, e o técnico
    continua podendo registrar a não realizada com motivo `fasting`.
    """
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    now = datetime.now(UTC)
    since = payload.since or now
    if since > now:
        # Jejum começa no passado ou agora. Aceitar hora futura faria a ficha
        # exibir "em jejum" para um paciente que ainda está comendo.
        raise AppError("validation_error", 422, field="since")
    before = AuditService.snapshot(hospitalization)
    hospitalization.fasting_since = since
    hospitalization.fasting_reason = payload.reason
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="fasting_started",
        entity_type="hospitalization",
        entity_id=hospitalization.id,
        before=before,
        after=AuditService.snapshot(hospitalization),
        extra={"since": since.isoformat(), "recorded_at": now.isoformat()},
    )
    await session.commit()
    return HospitalizationOut.model_validate(hospitalization)


@router.delete("/{hospitalization_id}/fasting", response_model=HospitalizationOut)
async def end_fasting(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PRESCRIPTION_CREATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalizationOut:
    """Libera a alimentação."""
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    if hospitalization.fasting_since is None:
        # Não havia jejum: gravar "jejum encerrado" na trilha seria registrar um
        # ato clínico que não aconteceu. Devolve o estado e não escreve nada.
        return HospitalizationOut.model_validate(hospitalization)
    before = AuditService.snapshot(hospitalization)
    hospitalization.fasting_since = None
    hospitalization.fasting_reason = None
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="fasting_ended",
        entity_type="hospitalization",
        entity_id=hospitalization.id,
        before=before,
        after=AuditService.snapshot(hospitalization),
    )
    await session.commit()
    return HospitalizationOut.model_validate(hospitalization)
