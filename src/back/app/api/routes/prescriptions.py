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
from app.models import Clinic, Hospitalization, Patient, Prescription
from app.models.price_list_item import PriceListItem
from app.permissions import PRESCRIPTION_ADJUST, PRESCRIPTION_CREATE, PRESCRIPTION_SUSPEND
from app.schemas.prescription import (
    PrescriptionAdjust,
    PrescriptionCreate,
    PrescriptionOut,
    SchedulePreviewOut,
)
from app.schemas.price_list_item import DosePreviewOut, DosePreviewRequest
from app.services.audit import ActorInfo, AuditService
from app.services.dosing import DosingService
from app.services.scheduling import SCHEDULING_HORIZON, SchedulingService
from app.services.tasks import TaskService

router = APIRouter(prefix="/api/v1", tags=["prescriptions"])


@router.post("/prescriptions/dose-preview", response_model=DosePreviewOut)
async def preview_dose(
    payload: DosePreviewRequest,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DosePreviewOut:
    """A dose deste fármaco para ESTE paciente, já calculada.

    O sistema tem as duas coisas que faltavam: a concentração da apresentação e
    o peso e a espécie do paciente. Perguntar de novo o que ele já sabe é pedir
    ao veterinário que faça de cabeça uma conta que a máquina faz certo, e
    **51% dos erros de medicação são de dose** (pesquisa §5.8).

    Devolve a conta inteira, não só o resultado: o que se confere é o caminho.
    E devolve avisos, nunca bloqueios: fora de faixa, contraindicado na
    espécie, raça sensível. Quem decide é quem tem registro no conselho.
    """
    hospitalization = await get_tenant_obj(
        session, Hospitalization, payload.hospitalization_id, auth.clinic_id
    )
    item = await get_tenant_obj(session, PriceListItem, payload.price_list_item_id, auth.clinic_id)
    patient = await session.get(Patient, hospitalization.patient_id)
    rule = await DosingService.rule_for(
        session,
        clinic_id=auth.clinic_id,
        price_list_item_id=item.id,
        species=patient.species if patient else None,
    )
    calc = DosingService.calculate(
        rule=rule,
        item=item,
        weight_kg=patient.weight_kg if patient else None,
        species=patient.species if patient else None,
        breed=patient.breed if patient else None,
        dose_per_kg_override=payload.dose_per_kg,
    )
    return DosePreviewOut(
        **vars(calc),
        species=patient.species if patient else None,
        unit_label=item.unit,
    )


@router.post("/prescriptions/preview", response_model=SchedulePreviewOut)
async def preview_schedule(
    payload: PrescriptionCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SchedulePreviewOut:
    """Que horários esta prescrição vai gerar, sem gravar nada.

    Existe para que o cliente pare de ter a segunda cópia do aprazamento. A
    dele usava âncoras cravadas no código e ignorava `clinics.anchors`, então
    prometia na tela horários que o servidor não ia criar.
    """
    clinic = await session.get(Clinic, auth.clinic_id)
    now = datetime.now(UTC)
    # Objeto em memória: o aprazamento é função PURA e não toca no banco.
    candidate = Prescription(
        clinic_id=auth.clinic_id,
        hospitalization_id=uuid.uuid4(),
        kind=payload.kind,
        category=payload.category,
        name=payload.name,
        details=payload.details,
        frequency_minutes=payload.frequency_minutes,
        duration_hours=payload.duration_hours,
        criticality=payload.criticality,
        tolerance_minutes=payload.resolved_tolerance(clinic),
        first_dose_now=payload.first_dose_now,
        starts_at=payload.resolved_starts_at(),
        ends_at=payload.resolved_ends_at(),
    )
    tasks = SchedulingService.generate(candidate, clinic, now + SCHEDULING_HORIZON)
    # Sem a flag, nada é suprimido; com ela, a diferença entre o que as âncoras
    # dariam e o que sobrou é exatamente o que a tela precisa explicar.
    sem_flag = 0
    if payload.first_dose_now:
        candidate.first_dose_now = False
        sem_flag = len(SchedulingService.generate(candidate, clinic, now + SCHEDULING_HORIZON))
        candidate.first_dose_now = True
    return SchedulePreviewOut(
        times=[task.scheduled_for for task in tasks],
        suppressed=max(0, sem_flag + 1 - len(tasks)) if payload.first_dose_now else 0,
        tolerance_minutes=candidate.tolerance_minutes,
        anchors=clinic.anchors.get(str(payload.frequency_minutes), []),
    )


@router.post(
    "/hospitalizations/{hospitalization_id}/prescriptions",
    response_model=PrescriptionOut,
    status_code=201,
)
async def create_prescription(
    hospitalization_id: uuid.UUID,
    payload: PrescriptionCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PRESCRIPTION_CREATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PrescriptionOut:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )

    # Escolheu um item do catálogo? O preço vem DELE: ninguém digita centavos.
    # O valor é copiado agora e congelado: reajuste posterior da tabela não
    # altera esta prescrição nem a conta que ela gerar.
    price_minor = payload.price_minor
    if payload.price_list_item_id is not None:
        item = await get_tenant_obj(
            session, PriceListItem, payload.price_list_item_id, auth.clinic_id
        )
        if price_minor is None:
            price_minor = item.price_minor

    # A clínica entra antes de montar a prescrição: a janela de tolerância é
    # dela (configurações), não uma constante do produto.
    clinic = await session.get(Clinic, auth.clinic_id)

    prescription = Prescription(
        clinic_id=auth.clinic_id,
        hospitalization_id=hospitalization.id,
        kind=payload.kind,
        category=payload.category,
        name=payload.name,
        details=payload.details,
        frequency_minutes=payload.frequency_minutes,
        duration_hours=payload.duration_hours,
        criticality=payload.criticality,
        tolerance_minutes=payload.resolved_tolerance(clinic),
        first_dose_now=payload.first_dose_now,
        is_controlled=payload.is_controlled,
        max_doses_24h=payload.max_doses_24h,
        min_interval_minutes=payload.min_interval_minutes,
        price_minor=price_minor,
        price_list_item_id=payload.price_list_item_id,
        starts_at=payload.resolved_starts_at(),
        ends_at=payload.resolved_ends_at(),
        created_by=actor.membership_id,
    )
    session.add(prescription)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="prescription_created",
        entity_type="prescription",
        entity_id=prescription.id,
        after=AuditService.snapshot(prescription),
    )
    await TaskService.materialize(
        session,
        prescription=prescription,
        clinic=clinic,
        until=datetime.now(UTC) + SCHEDULING_HORIZON,
    )
    await session.commit()
    return PrescriptionOut.model_validate(prescription)


@router.post("/prescriptions/{prescription_id}/suspend", response_model=PrescriptionOut)
async def suspend(
    prescription_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PRESCRIPTION_SUSPEND))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PrescriptionOut:
    await get_tenant_obj(session, Prescription, prescription_id, auth.clinic_id)
    # FOR UPDATE serializa com o job de 48h: sem isso o worker pode inserir
    # tarefas futuras logo depois do cancelamento.
    prescription = await session.scalar(
        sa.select(Prescription).where(Prescription.id == prescription_id).with_for_update()
    )
    before = AuditService.snapshot(prescription)
    now = datetime.now(UTC)
    prescription.suspended_at = now
    prescription.suspended_by = actor.membership_id
    cancelled = await TaskService.cancel_pending(
        session, clinic_id=auth.clinic_id, prescription_id=prescription.id
    )
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="prescription_suspended",
        entity_type="prescription",
        entity_id=prescription.id,
        before=before,
        after=AuditService.snapshot(prescription),
        extra={"cancelled_tasks": cancelled},
    )
    await session.commit()
    return PrescriptionOut.model_validate(prescription)


@router.post(
    "/prescriptions/{prescription_id}/adjust", response_model=PrescriptionOut, status_code=201
)
async def adjust(
    prescription_id: uuid.UUID,
    payload: PrescriptionAdjust,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PRESCRIPTION_ADJUST))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PrescriptionOut:
    await get_tenant_obj(session, Prescription, prescription_id, auth.clinic_id)
    previous = await session.scalar(
        sa.select(Prescription).where(Prescription.id == prescription_id).with_for_update()
    )
    now = datetime.now(UTC)
    changes = payload.model_dump(exclude_unset=True, exclude={"reason"})

    replacement = Prescription(
        clinic_id=previous.clinic_id,
        hospitalization_id=previous.hospitalization_id,
        kind=previous.kind,
        category=previous.category,
        name=changes.get("name", previous.name),
        details=changes.get("details", previous.details),
        frequency_minutes=changes.get("frequency_minutes", previous.frequency_minutes),
        duration_hours=previous.duration_hours,
        criticality=changes.get("criticality", previous.criticality),
        tolerance_minutes=changes.get("tolerance_minutes", previous.tolerance_minutes),
        first_dose_now=False,
        is_controlled=previous.is_controlled,
        max_doses_24h=previous.max_doses_24h,
        min_interval_minutes=previous.min_interval_minutes,
        price_minor=changes.get("price_minor", previous.price_minor),
        starts_at=now,
        ends_at=previous.ends_at,
        replaces_prescription_id=previous.id,
        created_by=actor.membership_id,
    )
    session.add(replacement)

    before = AuditService.snapshot(previous)
    previous.suspended_at = now
    previous.suspended_by = actor.membership_id
    cancelled = await TaskService.cancel_pending(
        session, clinic_id=auth.clinic_id, prescription_id=previous.id
    )
    await session.flush()

    clinic = await session.get(Clinic, auth.clinic_id)
    await TaskService.materialize(
        session, prescription=replacement, clinic=clinic, until=now + SCHEDULING_HORIZON
    )
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="prescription_adjusted",
        entity_type="prescription",
        entity_id=replacement.id,
        before=before,
        after=AuditService.snapshot(replacement),
        extra={
            "replaces": str(previous.id),
            "cancelled_tasks": cancelled,
            "reason": payload.reason,
        },
    )
    await session.commit()
    return PrescriptionOut.model_validate(replacement)
