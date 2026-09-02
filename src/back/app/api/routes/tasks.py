import uuid
from datetime import UTC, datetime, timedelta
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
from app.models import Clinic, Hospitalization, Prescription, Task
from app.permissions import TASK_AD_HOC, TASK_EXECUTE
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.schemas.task import TaskAdHoc, TaskExecute, TaskNotDone, TaskOut
from app.services.audit import ActorInfo, AuditService
from app.services.charges import ChargeService
from app.services.push import PushService
from app.services.tasks import TaskService
from app.vitals import declared_kinds, validate_values

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


def _encode_cursor(task: Task) -> str:
    return f"{task.scheduled_for.isoformat()}|{task.id}"


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    marco, _, raw_id = cursor.partition("|")
    try:
        return datetime.fromisoformat(marco), uuid.UUID(raw_id)
    except ValueError:
        return None


@router.get("", response_model=Page[TaskOut])
async def list_tasks(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> Page[TaskOut]:
    now = datetime.now(UTC)
    clinic = await session.get(Clinic, auth.clinic_id)
    default_from, default_to = TaskService.default_window(clinic, now)
    window_start = from_ or default_from
    window_end = to or default_to

    if from_ is None and to is None:
        criterio = TaskService.queue_criteria(now, window_start, window_end)
    else:
        # Janela pedida de propósito é respeitada: quem informa um intervalo
        # está montando relatório de um período, não tocando o plantão.
        criterio = sa.and_(
            Task.scheduled_for >= window_start, Task.scheduled_for <= window_end
        )

    stmt = sa.select(Task).where(
        Task.clinic_id == auth.clinic_id,
        Task.status != "cancelled",
        criterio,
    )
    if cursor is not None:
        # Cursor pela CHAVE DE ORDENAÇÃO (horário + id), não pelo id sozinho:
        # a fila é ordenada por horário, e paginar por id embaralharia as doses.
        marco = _decode_cursor(cursor)
        if marco is None:
            raise AppError("validation_error", 422, field="cursor")
        stmt = stmt.where(sa.tuple_(Task.scheduled_for, Task.id) > sa.tuple_(*marco))

    rows = list(
        (
            await session.execute(
                stmt.order_by(Task.scheduled_for.asc(), Task.id.asc()).limit(limit + 1)
            )
        ).scalars()
    )
    # Devolver next_cursor=None com a lista truncada faria o app acreditar que
    # viu o plantão inteiro. Numa clínica de 25 leitos, some metade da fila.
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1])
    return Page[TaskOut](
        items=[TaskOut.from_task(row, now) for row in rows], next_cursor=next_cursor
    )


@router.post("/{task_id}/execute", response_model=TaskOut)
async def execute(
    task_id: uuid.UUID,
    payload: TaskExecute,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(TASK_EXECUTE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskOut:
    now = datetime.now(UTC)
    current = await get_tenant_obj(session, Task, task_id, auth.clinic_id)

    # A grade de monitoramento que a prescrição declarou é o contrato do que
    # esta tarefa mede: sem conferir, "temperatuar: 39,1" entrava no jsonb e a
    # medição sumia do prontuário sem ninguém perceber. Valor FORA DA FAIXA DE
    # REFERÊNCIA passa de propósito: é o achado clínico, e recusá-lo seria o
    # sistema se negando a registrar um animal doente.
    declared: tuple[str, ...] = ()
    if current.prescription_id is not None:
        prescription = await session.get(Prescription, current.prescription_id)
        declared = declared_kinds(prescription.details if prescription else None)
    invalid = validate_values(payload.values, declared)
    if invalid is not None:
        raise AppError("validation_error", 422, **invalid)

    # A janela ISMP vale nos DOIS lados: adiantar dose é erro como atrasar.
    early = now < current.scheduled_for - timedelta(minutes=current.tolerance_minutes)
    if early and not payload.confirm_early:
        raise AppError(
            "early_confirmation_required", 409, scheduled_for=current.scheduled_for.isoformat()
        )

    # Alimentar quem está em jejum: aviso auditado, nunca bloqueio duro (o
    # mesmo contrato dos guardrails de PRN).
    fasting = await TaskService.check_fasting_guardrail(
        session, task=current, clinic_id=auth.clinic_id
    )
    if fasting is not None and not payload.override:
        raise AppError("fasting_active", 409, **fasting)

    executed_at = payload.performed_at or now
    task = await TaskService.transition(
        session,
        task_id=task_id,
        clinic_id=auth.clinic_id,
        values={
            "status": "partial" if payload.partial else "done",
            "executed_at": executed_at,
            "executed_by": actor.membership_id,
            "retroactive": payload.retroactive,
            "early": early,
            "values": payload.values,
        },
    )
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="task_executed",
        entity_type="task",
        entity_id=task.id,
        after=AuditService.snapshot(task),
        extra={
            "performed_at": executed_at.isoformat(),
            "recorded_at": now.isoformat(),
            "early": early,
            # Quem alimentou apesar do jejum fica no prontuário com o motivo do
            # jejum que estava valendo: override sem rastro é override que não
            # aconteceu.
            "override": payload.override and fasting is not None,
            "fasting": fasting,
        },
    )
    # A conta segue a execução: partial gera item proporcional, e tarefa sem
    # preço (cerimônia, monitoramento) não gera nada.
    await ChargeService.record_execution(
        session,
        task=task,
        hospitalization_id=task.hospitalization_id,
        clinic_id=auth.clinic_id,
        actor=actor,
    )
    await session.commit()
    return TaskOut.from_task(task, now)


@router.post("/{task_id}/not-done", response_model=TaskOut)
async def not_done(
    task_id: uuid.UUID,
    payload: TaskNotDone,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(TASK_EXECUTE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskOut:
    now = datetime.now(UTC)
    await get_tenant_obj(session, Task, task_id, auth.clinic_id)
    task = await TaskService.transition(
        session,
        task_id=task_id,
        clinic_id=auth.clinic_id,
        values={
            "status": "not_done",
            "executed_at": now,
            "executed_by": actor.membership_id,
            "outcome_reason": payload.reason,
            "values": payload.values,
        },
    )
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="task_not_done",
        entity_type="task",
        entity_id=task.id,
        after=AuditService.snapshot(task),
        extra={"reason": payload.reason},
    )
    await session.commit()
    return TaskOut.from_task(task, now)


@router.post("/ad-hoc", response_model=TaskOut, status_code=201)
async def ad_hoc(
    payload: TaskAdHoc,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(TASK_AD_HOC))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskOut:
    now = datetime.now(UTC)
    guardrail = None
    if payload.prescription_id is not None:
        prescription = await get_tenant_obj(
            session, Prescription, payload.prescription_id, auth.clinic_id
        )
        if prescription.kind != "prn":
            raise AppError("validation_error", 422, field="prescription_id")
        guardrail = await TaskService.check_prn_guardrails(
            session, prescription=prescription, now=now
        )
        if guardrail is not None and not payload.override:
            raise AppError("prn_guardrail", 409, **guardrail)
        task = Task(
            clinic_id=auth.clinic_id,
            hospitalization_id=prescription.hospitalization_id,
            prescription_id=prescription.id,
            title=prescription.name,
            category=prescription.category,
            price_minor=prescription.price_minor,
            criticality=prescription.criticality,
            tolerance_minutes=prescription.tolerance_minutes,
        )
    else:
        await get_tenant_obj(session, Hospitalization, payload.hospitalization_id, auth.clinic_id)
        task = Task(
            clinic_id=auth.clinic_id,
            hospitalization_id=payload.hospitalization_id,
            title=payload.title,
            category=payload.category,
            criticality="normal",
            tolerance_minutes=60,
        )

    task.scheduled_for = now
    task.status = "done"
    task.executed_at = now
    task.executed_by = actor.membership_id
    task.values = payload.values
    session.add(task)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="task_executed",
        entity_type="task",
        entity_id=task.id,
        after=AuditService.snapshot(task),
        extra={
            "ad_hoc": True,
            "performed_at": now.isoformat(),
            "recorded_at": now.isoformat(),
            "override": payload.override and guardrail is not None,
            "guardrail": guardrail,
        },
    )
    await ChargeService.record_execution(
        session,
        task=task,
        hospitalization_id=task.hospitalization_id,
        clinic_id=auth.clinic_id,
        actor=actor,
    )
    await session.commit()
    # O switch "avisar o veterinário" gravava `values.notify_vet` e NADA no
    # backend lia esse campo: o técnico registrava a convulsão das 3h, marcava
    # o aviso, e ninguém era avisado. Mentira em caminho de segurança é pior do
    # que funcionalidade ausente.
    #
    # Depois do commit de propósito: avisar sobre um evento que não persistiu
    # seria a mesma classe de erro ao contrário. E `notify` nunca levanta: uma
    # notificação que falha não desfaz o registro clínico que acabou de entrar.
    if (payload.values or {}).get("notify_vet"):
        await PushService.notify_intercurrence(
            session, clinic_id=auth.clinic_id, task=task, actor=actor
        )
    return TaskOut.from_task(task, now)
