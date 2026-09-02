import uuid
from datetime import UTC, datetime
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
from app.models.clinic import Clinic
from app.models.handover_ack import HandoverAck
from app.models.handover_report import HandoverReport
from app.models.hospitalization import Hospitalization
from app.models.kennel import Kennel
from app.models.membership import Membership
from app.models.patient import Patient
from app.models.task import Task
from app.models.user import User
from app.permissions import SHIFT_OPERATE
from app.schemas.handover import (
    HandoverAckOut,
    HandoverAckRequest,
    HandoverNarrativeRequest,
    HandoverReportOut,
)
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.schemas.task import TaskOut
from app.services.audit import ActorInfo
from app.services.handover import HandoverService
from app.services.narrative import NarrativeService

router = APIRouter(prefix="/api/v1/handover", tags=["handover"])


async def _enrich(session: AsyncSession, rows: list[HandoverReport]) -> list[HandoverReportOut]:
    """Junta paciente, box e aceite ao boletim, em duas queries para a lista toda.

    Sem isso o cliente teria de cruzar com o painel, que só lista internações
    ATIVAS, deixando órfão o boletim de quem teve alta depois do turno."""
    if not rows:
        return []

    hospitalization_ids = {row.hospitalization_id for row in rows}
    context = {
        hosp_id: (patient_name, kennel_name)
        for hosp_id, patient_name, kennel_name in (
            await session.execute(
                sa.select(Hospitalization.id, Patient.name, Kennel.name)
                .join(Patient, Patient.id == Hospitalization.patient_id)
                .outerjoin(Kennel, Kennel.id == Hospitalization.kennel_id)
                .where(Hospitalization.id.in_(hospitalization_ids))
            )
        ).all()
    }

    acks = {
        report_id: (acked_at, author, seconds)
        for report_id, acked_at, author, seconds in (
            await session.execute(
                sa.select(
                    HandoverAck.handover_report_id,
                    HandoverAck.acked_at,
                    User.name,
                    HandoverAck.seconds_to_ack,
                )
                .join(Membership, Membership.id == HandoverAck.membership_id)
                .join(User, User.id == Membership.user_id)
                .where(HandoverAck.handover_report_id.in_({row.id for row in rows}))
                # DESC porque o dict abaixo sobrescreve: o aceite que vale é o
                # PRIMEIRO (quem assumiu o plantão), não um segundo clique.
                .order_by(HandoverAck.acked_at.desc())
            )
        ).all()
    }

    # O que FICOU do turno que sai, não o turno inteiro que entra.
    #
    # Quem recebe o plantão não precisa aceitar a dose de amanhã às 02h: aquilo
    # é o trabalho normal dele e vive no console do plantão. O que ele assume,
    # e o que a spec manda ver no próprio ato do aceite, é a dívida: o que era
    # para ter sido feito até o fim do turno anterior e continua pendente.
    #
    # O corte é o fim da janela do boletim (`skeleton.period.until` = quando o
    # turno de origem realmente fechou), por boletim: dois turnos diferentes
    # têm janelas diferentes. Ao vivo, não congelado: entre o fechamento e o
    # aceite alguém pode ter dado a dose, e aceitar pendência que já não existe
    # é ruído.
    now = datetime.now(UTC)
    limites: dict[uuid.UUID, datetime] = {}
    for row in rows:
        periodo = (row.skeleton or {}).get("period") or {}
        until = periodo.get("until")
        try:
            limites[row.id] = datetime.fromisoformat(until) if until else now
        except (TypeError, ValueError):
            limites[row.id] = now
    teto = max([*limites.values(), now])

    abertas: dict[uuid.UUID, list[Task]] = {}
    for task in (
        await session.execute(
            sa.select(Task)
            .where(
                Task.hospitalization_id.in_(hospitalization_ids),
                Task.status == "pending",
                Task.scheduled_for <= teto,
            )
            .order_by(Task.scheduled_for.asc())
        )
    ).scalars():
        abertas.setdefault(task.hospitalization_id, []).append(task)

    enriched: list[HandoverReportOut] = []
    for row in rows:
        out = HandoverReportOut.model_validate(row)
        patient_name, kennel_name = context.get(row.hospitalization_id, (None, None))
        out.patient_name = patient_name
        out.kennel_name = kennel_name
        ack = acks.get(row.id)
        if ack is not None:
            out.acked_at, out.acked_by_name, out.seconds_to_ack = ack
        limite = limites[row.id]
        out.open_tasks = [
            TaskOut.from_task(task, now)
            for task in abertas.get(row.hospitalization_id, [])
            if task.scheduled_for <= limite
        ]
        enriched.append(out)
    return enriched


@router.get("/reports", response_model=Page[HandoverReportOut])
async def list_reports(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    shift_id: uuid.UUID | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> Page[HandoverReportOut]:
    """Leitura sem qualquer filtro por aprovação.

    Regra dura da spec: boletim NÃO revisado aparece igual, com esqueleto e
    narrativa completos. Esconder o não revisado seria esconder justamente o
    plantão que correu mal."""
    stmt = sa.select(HandoverReport).where(HandoverReport.clinic_id == auth.clinic_id)
    if shift_id is not None:
        # Um shift vê o que ele entregou E o que ele recebe.
        stmt = stmt.where(
            sa.or_(
                HandoverReport.from_shift_id == shift_id,
                HandoverReport.to_shift_id == shift_id,
            )
        )
    rows = list(
        (
            await session.execute(stmt.order_by(HandoverReport.created_at.asc()).limit(limit))
        ).scalars()
    )
    return Page[HandoverReportOut](items=await _enrich(session, rows), next_cursor=None)


@router.get("/reports/{report_id}", response_model=HandoverReportOut)
async def get_report(
    report_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HandoverReportOut:
    report = await get_tenant_obj(session, HandoverReport, report_id, auth.clinic_id)
    return HandoverReportOut.model_validate(report)


@router.post("/reports/{report_id}/narrative", response_model=HandoverReportOut)
async def draft_narrative(
    report_id: uuid.UUID,
    payload: HandoverNarrativeRequest | None = None,
    auth: Annotated[AuthContext, Depends(get_current_auth)] = None,
    actor: Annotated[ActorInfo, Depends(require(SHIFT_OPERATE))] = None,
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> HandoverReportOut:
    """Escreve o boletim: com `text`, grava o que a pessoa escreveu; sem, gera
    o rascunho NO LOCALE DA CLÍNICA (ADR-0004).

    Quem entrega o plantão precisa poder corrigir uma frase e escrever o próprio
    texto; a rota só sabia mandar o servidor rascunhar de novo. Rascunho é
    rascunho porque quem assina é a pessoa; o esqueleto continua sendo a verdade
    e nunca é redigido por modelo nenhum.
    """
    report = await get_tenant_obj(session, HandoverReport, report_id, auth.clinic_id)
    clinic = await session.get(Clinic, auth.clinic_id)
    escrito = (payload.text or "").strip() if payload else ""
    narrative = escrito or await NarrativeService.draft(report.skeleton, clinic.locale)
    await HandoverService.set_narrative(
        session, report=report, narrative=narrative, actor=actor, authored=bool(escrito)
    )
    await session.commit()
    return HandoverReportOut.model_validate(report)


@router.post("/reports/{report_id}/approve", response_model=HandoverReportOut)
async def approve_report(
    report_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(SHIFT_OPERATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HandoverReportOut:
    report = await get_tenant_obj(session, HandoverReport, report_id, auth.clinic_id)
    await HandoverService.approve(session, report=report, actor=actor)
    await session.commit()
    return HandoverReportOut.model_validate(report)


@router.post("/reports/{report_id}/ack", response_model=HandoverAckOut, status_code=201)
async def acknowledge_report(
    report_id: uuid.UUID,
    payload: HandoverAckRequest,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(SHIFT_OPERATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HandoverAckOut:
    """Aceite do receptor. Nunca exige aprovação prévia: a spec proíbe bloquear
    a passagem por boletim não revisado."""
    report = await get_tenant_obj(session, HandoverReport, report_id, auth.clinic_id)
    ack = await HandoverService.acknowledge(
        session, report=report, actor=actor, seconds=payload.seconds_to_ack
    )
    await session.commit()
    return HandoverAckOut.model_validate(ack)
