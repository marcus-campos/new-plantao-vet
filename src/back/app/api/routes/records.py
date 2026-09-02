import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Annotated, Any

import anyio
import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_session,
    get_tenant_obj,
    require_read,
)
from app.core.errors import AppError
from app.models import Clinic, Hospitalization, Membership, Owner, Patient, Prescription, Task, User
from app.models.progress_note import ProgressNote
from app.permissions import CHARGES_READ, RECORD_READ, can
from app.schemas.hospitalization import HospitalizationOut, PatientSummary
from app.schemas.prescription import PrescriptionOut
from app.schemas.progress_note import ProgressNoteOut
from app.schemas.record import RecordAuthor, RecordExecution, RecordOut
from app.services.audit import ActorInfo, AuditService
from app.services.record_pdf import Letterhead, record_filename, render_record_pdf

router = APIRouter(prefix="/api/v1/hospitalizations", tags=["records"])

# Cobrança não entra na cópia do tutor por default: o prontuário é documento
# clínico. Quem quer o extrato pede `include=...,charges` explicitamente.
DEFAULT_SECTIONS = ("progress_notes", "tasks", "prescriptions")
ALLOWED_SECTIONS = frozenset(DEFAULT_SECTIONS) | {"charges"}


def _parse_include(include: str | None) -> set[str]:
    if include is None:
        return set(DEFAULT_SECTIONS)
    sections = {part.strip() for part in include.split(",") if part.strip()}
    unknown = sections - ALLOWED_SECTIONS
    if unknown:
        raise AppError("validation_error", 422, field="include", unknown=sorted(unknown))
    return sections


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID | datetime | Decimal):
        return str(value)
    return value


async def _authors(
    session: AsyncSession, *, clinic_id: uuid.UUID, membership_ids: set[uuid.UUID]
) -> dict[uuid.UUID, RecordAuthor]:
    """Nome + registro de quem executou, em uma consulta só."""
    if not membership_ids:
        return {}
    rows = await session.execute(
        sa.select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.clinic_id == clinic_id, Membership.id.in_(membership_ids))
    )
    return {
        membership.id: RecordAuthor(
            name=user.name,
            license_number=membership.license_number,
            license_authority=membership.license_authority,
        )
        for membership, user in rows.all()
    }


async def _charges(
    session: AsyncSession, *, clinic_id: uuid.UUID, hospitalization_id: uuid.UUID
) -> list[dict[str, Any]]:
    # `charge_items` chega por outra trilha da semana 2; enquanto não existir, a
    # seção vem vazia em vez de quebrar a exportação do prontuário.
    try:
        from app.models.charge_item import ChargeItem
    except ImportError:
        return []
    rows = (
        await session.execute(
            sa.select(ChargeItem).where(
                ChargeItem.clinic_id == clinic_id,
                ChargeItem.hospitalization_id == hospitalization_id,
            )
        )
    ).scalars()
    return [
        {
            column.key: _jsonable(getattr(row, column.key))
            for column in sa.inspect(row).mapper.columns
        }
        for row in rows
    ]


@dataclass
class _Bundle:
    """O prontuário e a clínica que o timbra.

    A rota JSON e a do PDF montam o MESMO documento: duas consultas separadas
    fariam o papel e a tela divergirem no dia em que uma das duas mudasse.
    """

    record: RecordOut
    clinic: Clinic


async def _assemble(
    session: AsyncSession,
    *,
    auth: AuthContext,
    actor: ActorInfo,
    hospitalization_id: uuid.UUID,
    include: str | None,
    fmt: str,
) -> _Bundle:
    """Monta o prontuário e deixa a leitura na trilha.

    Documento regulado (CFMV Res. 1321/2020): sai inteiro, então exige alguém
    identificado, e a leitura fica na trilha, em QUALQUER formato. A cadeia
    registrava quem MUDOU o prontuário e nunca quem o leu, num produto cuja
    tese é segurança jurídica.
    """
    sections = _parse_include(include)
    if "charges" in sections and not can(actor.role, CHARGES_READ):
        raise AppError("forbidden", 403, capability=CHARGES_READ, role=actor.role)
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    clinic = await session.get(Clinic, auth.clinic_id)
    if clinic is None:
        raise AppError("not_found", 404)
    patient = await session.get(Patient, hospitalization.patient_id)
    owner = await session.get(Owner, patient.owner_id) if patient else None
    vet_membership = await session.get(Membership, hospitalization.vet_membership_id)
    vet_user = await session.get(User, vet_membership.user_id) if vet_membership else None

    progress_notes = None
    if "progress_notes" in sections:
        rows = (
            await session.execute(
                sa.select(ProgressNote)
                .where(
                    ProgressNote.clinic_id == auth.clinic_id,
                    ProgressNote.hospitalization_id == hospitalization.id,
                )
                .order_by(ProgressNote.signed_at.asc())
            )
        ).scalars()
        progress_notes = [ProgressNoteOut.model_validate(row) for row in rows]

    tasks = None
    if "tasks" in sections:
        rows = list(
            (
                await session.execute(
                    sa.select(Task)
                    .where(
                        Task.clinic_id == auth.clinic_id,
                        Task.hospitalization_id == hospitalization.id,
                        Task.executed_at.is_not(None),
                    )
                    .order_by(Task.executed_at.asc())
                )
            ).scalars()
        )
        authors = await _authors(
            session,
            clinic_id=auth.clinic_id,
            membership_ids={row.executed_by for row in rows if row.executed_by},
        )
        tasks = [
            RecordExecution(
                **{
                    field: getattr(row, field)
                    for field in RecordExecution.model_fields
                    if field != "author"
                },
                author=authors.get(row.executed_by),
            )
            for row in rows
        ]

    prescriptions = None
    if "prescriptions" in sections:
        rows = (
            await session.execute(
                sa.select(Prescription)
                .where(
                    Prescription.clinic_id == auth.clinic_id,
                    Prescription.hospitalization_id == hospitalization.id,
                )
                .order_by(Prescription.starts_at.asc())
            )
        ).scalars()
        prescriptions = [PrescriptionOut.model_validate(row) for row in rows]

    charges = None
    if "charges" in sections:
        charges = await _charges(
            session, clinic_id=auth.clinic_id, hospitalization_id=hospitalization.id
        )

    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="record_read",
        entity_type="hospitalization",
        entity_id=hospitalization.id,
        # O formato entra na trilha: papel entregue ao tutor e tela consultada
        # na clínica são leituras diferentes do mesmo documento.
        extra={"sections": sorted(sections), "format": fmt},
    )
    await session.commit()
    return _Bundle(
        record=RecordOut(
            generated_at=datetime.now(UTC),
            clinic_name=clinic.name,
            patient=PatientSummary.model_validate(patient) if patient else None,
            # Só o NOME do tutor: telefone e documento não vão no prontuário (LGPD).
            owner_name=owner.name if owner else None,
            hospitalization=HospitalizationOut.model_validate(hospitalization),
            vet=(
                RecordAuthor(
                    name=vet_user.name,
                    license_number=vet_membership.license_number,
                    license_authority=vet_membership.license_authority,
                )
                if vet_membership and vet_user
                else None
            ),
            progress_notes=progress_notes,
            tasks=tasks,
            prescriptions=prescriptions,
            charges=charges,
        ),
        clinic=clinic,
    )


@router.get("/{hospitalization_id}/record", response_model=RecordOut)
async def record(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require_read(RECORD_READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
    include: Annotated[str | None, Query()] = None,
) -> RecordOut:
    """Prontuário completo em JSON: o que a TELA desenha. Cada evolução e cada
    execução carrega autor e registro."""
    bundle = await _assemble(
        session,
        auth=auth,
        actor=actor,
        hospitalization_id=hospitalization_id,
        include=include,
        fmt="json",
    )
    return bundle.record


@router.get(
    "/{hospitalization_id}/record.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def record_pdf(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require_read(RECORD_READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
    include: Annotated[str | None, Query()] = None,
) -> Response:
    """A cópia ao tutor em 5 dias úteis (spec §2), gerada no servidor.

    Mesma capacidade, mesmo `include`, mesma trava da conta e mesma trilha da
    rota JSON; muda só o formato. O cliente fazia `window.print()` sob o
    rótulo "Baixar PDF": um documento sem timbre, sem paginação e limitado ao
    que a tela tinha carregado.
    """
    bundle = await _assemble(
        session,
        auth=auth,
        actor=actor,
        hospitalization_id=hospitalization_id,
        include=include,
        fmt="pdf",
    )
    # reportlab é CPU síncrona: um prontuário de internação longa seguraria o
    # event loop, e com ele todo o plantão que está executando dose.
    content = await anyio.to_thread.run_sync(
        partial(render_record_pdf, bundle.record, clinic=Letterhead.of(bundle.clinic))
    )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{record_filename(hospitalization_id)}"'
        },
    )
