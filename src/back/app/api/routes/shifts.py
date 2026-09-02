import uuid
from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_session,
    get_tenant_obj,
    require,
)
from app.core.config import settings
from app.models.clinic import Clinic
from app.models.hospitalization import Hospitalization
from app.models.membership import Membership
from app.models.shift import Shift
from app.models.shift_note import ShiftNote, ShiftNoteSource
from app.permissions import SHIFT_OPERATE, SHIFT_SCHEDULE
from app.schemas.handover import HandoverReportOut
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.schemas.shift import (
    ShiftClose,
    ShiftClosed,
    ShiftCreate,
    ShiftNoteCreate,
    ShiftNoteOut,
    ShiftOut,
)
from app.services.audit import ActorInfo, AuditService
from app.services.handover import HandoverService
from app.services.transcription import TranscriptionService

router = APIRouter(prefix="/api/v1/shifts", tags=["shifts"])

# A nota de plantão pertence ao PACIENTE, não ao turno: quem escreve está na
# ficha da internação. Daí o router separado, montado sob /hospitalizations.
notes_router = APIRouter(prefix="/api/v1/hospitalizations", tags=["shifts"])


@router.get("", response_model=Page[ShiftOut])
async def list_shifts(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> Page[ShiftOut]:
    stmt = sa.select(Shift).where(Shift.clinic_id == auth.clinic_id)
    if from_ is not None:
        stmt = stmt.where(Shift.ends_at >= from_)
    if to is not None:
        stmt = stmt.where(Shift.starts_at <= to)
    rows = list(
        (await session.execute(stmt.order_by(Shift.starts_at.asc()).limit(limit))).scalars()
    )
    return Page[ShiftOut](items=[ShiftOut.model_validate(row) for row in rows], next_cursor=None)


@router.post("", response_model=ShiftOut, status_code=201)
async def create_shift(
    payload: ShiftCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    # Montar a escala é planejamento de pessoas, não trabalho de plantão.
    actor: Annotated[ActorInfo, Depends(require(SHIFT_SCHEDULE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ShiftOut:
    await get_tenant_obj(session, Membership, payload.membership_id, auth.clinic_id)
    shift = Shift(clinic_id=auth.clinic_id, **payload.model_dump())
    session.add(shift)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="shift_created",
        entity_type="shift",
        entity_id=shift.id,
        after=AuditService.snapshot(shift),
    )
    await session.commit()
    return ShiftOut.model_validate(shift)


@router.post("/{shift_id}/close", response_model=ShiftClosed)
async def close_shift(
    shift_id: uuid.UUID,
    payload: ShiftClose,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(SHIFT_OPERATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ShiftClosed:
    """Fecha o turno e GERA os boletins: um por internação ativa.

    Fechar é idempotente: reenviar não move `closed_at` nem regenera boletim já
    existente. Boletim sem aprovação NÃO impede o fechamento; a omissão é
    auditada e devolvida em `missing_review` para o cliente carimbar o selo."""
    shift = await get_tenant_obj(session, Shift, shift_id, auth.clinic_id)
    to_shift = None
    if payload.to_shift_id is not None:
        to_shift = await get_tenant_obj(session, Shift, payload.to_shift_id, auth.clinic_id)
    else:
        # Sem destinatário explícito, procura quem está ENTRANDO.
        #
        # O boletim nascia com remetente e sem destinatário sempre que o cliente
        # não passava `to_shift_id`, e nenhum passa. Quem chega nunca via a
        # passagem endereçada a ele: metade do I-PASS, justamente a metade que a
        # pesquisa nomeia como a mais negligenciada (a síntese do receptor).
        #
        # Quem entra é o turno aberto que já começou e ainda não terminou, mais
        # novo primeiro: a troca é a fronteira entre dois turnos, e é o mais
        # recente que assume.
        agora = datetime.now(UTC)
        to_shift = (
            await session.execute(
                sa.select(Shift)
                .where(
                    Shift.clinic_id == auth.clinic_id,
                    Shift.id != shift.id,
                    Shift.closed_at.is_(None),
                    Shift.starts_at <= agora,
                    Shift.ends_at > agora,
                )
                # O veterinário responsável primeiro: entram duas pessoas no
                # mesmo turno (vet e técnica) e o boletim só aponta para uma.
                # Quem responde pela internação perante o conselho é ele, e é o
                # nome que a fiscalização procura no turno.
                .order_by(Shift.is_vet_responsible.desc(), Shift.starts_at.desc())
            )
        ).scalars().first()

    if shift.closed_at is None:
        before = AuditService.snapshot(shift)
        shift.closed_at = datetime.now(UTC)
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=auth.clinic_id,
            actor=actor,
            action="shift_closed",
            entity_type="shift",
            entity_id=shift.id,
            before=before,
            after=AuditService.snapshot(shift),
        )

    clinic = await session.get(Clinic, auth.clinic_id)
    reports = await HandoverService.generate(
        session, clinic=clinic, from_shift=shift, to_shift=to_shift, actor=actor
    )
    missing = await HandoverService.audit_missing_reviews(
        session, clinic_id=auth.clinic_id, reports=reports, actor=actor, shift=shift
    )
    await session.commit()
    return ShiftClosed(
        shift=ShiftOut.model_validate(shift),
        reports=[HandoverReportOut.model_validate(report) for report in reports],
        missing_review=[report.id for report in missing],
    )


@notes_router.get("/{hospitalization_id}/shift-notes", response_model=Page[ShiftNoteOut])
async def list_shift_notes(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    shift_id: uuid.UUID | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> Page[ShiftNoteOut]:
    await get_tenant_obj(session, Hospitalization, hospitalization_id, auth.clinic_id)
    stmt = sa.select(ShiftNote).where(
        ShiftNote.clinic_id == auth.clinic_id,
        ShiftNote.hospitalization_id == hospitalization_id,
    )
    if shift_id is not None:
        stmt = stmt.where(ShiftNote.shift_id == shift_id)
    rows = list(
        (await session.execute(stmt.order_by(ShiftNote.created_at.asc()).limit(limit))).scalars()
    )
    return Page[ShiftNoteOut](
        items=[ShiftNoteOut.model_validate(row) for row in rows], next_cursor=None
    )


@notes_router.post(
    "/{hospitalization_id}/shift-notes", response_model=ShiftNoteOut, status_code=201
)
async def create_shift_note(
    hospitalization_id: uuid.UUID,
    payload: ShiftNoteCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(SHIFT_OPERATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ShiftNoteOut:
    await get_tenant_obj(session, Hospitalization, hospitalization_id, auth.clinic_id)
    if payload.shift_id is not None:
        await get_tenant_obj(session, Shift, payload.shift_id, auth.clinic_id)
    note = ShiftNote(
        clinic_id=auth.clinic_id,
        hospitalization_id=hospitalization_id,
        shift_id=payload.shift_id,
        membership_id=actor.membership_id,
        # Copiado no ato: o autor do registro clínico não muda depois.
        author_name=actor.name,
        # `source=audio` marca a PROCEDÊNCIA do texto. O áudio não chega aqui e
        # não é gravado em lugar nenhum (LGPD, spec §2).
        text=payload.text,
        source=payload.source,
    )
    session.add(note)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="shift_note_created",
        entity_type="shift_note",
        entity_id=note.id,
        after=AuditService.snapshot(note),
        extra={"source": payload.source},
    )
    await session.commit()
    return ShiftNoteOut.model_validate(note)


@notes_router.post("/{hospitalization_id}/shift-notes/transcribe")
async def transcribe_shift_note(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    _actor: Annotated[ActorInfo, Depends(require(SHIFT_OPERATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    audio: Annotated[UploadFile, File()],
) -> dict[str, str]:
    """Transcreve e devolve o texto, SEM gravar nada.

    A revisão antes de salvar não é enfeite: a transcrição erra, e o que ela
    escreve vai para o prontuário, que é append-only. A spec é literal: o áudio
    bruto é apagado depois da transcrição **confirmada**, e confirmar é o ato de
    uma pessoa ler o que saiu. Criar a nota direto do áudio empurraria a
    correção para adendo, que é registro de erro, não prevenção.

    O áudio morre com a requisição, como na rota que cria a nota: não há coluna,
    bucket, fila nem log que o guarde.
    """
    await get_tenant_obj(session, Hospitalization, hospitalization_id, auth.clinic_id)
    clinic = await session.get(Clinic, auth.clinic_id)
    filename = TranscriptionService.upload_name(audio.content_type)
    try:
        audio_bytes = await TranscriptionService.read_capped(audio)
        try:
            text = await TranscriptionService.transcribe(
                audio_bytes, filename=filename, locale=clinic.locale
            )
        finally:
            del audio_bytes
    finally:
        await audio.close()
    return {"text": text}


@notes_router.post(
    "/{hospitalization_id}/shift-notes/audio", response_model=ShiftNoteOut, status_code=201
)
async def create_shift_note_from_audio(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(SHIFT_OPERATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    audio: Annotated[UploadFile, File()],
    shift_id: Annotated[uuid.UUID | None, Form()] = None,
) -> ShiftNoteOut:
    """Grava a nota falada: transcreve o áudio e cria a `ShiftNote`.

    O plantonista com as mãos ocupadas fala; o texto entra no prontuário. Sem
    isto o app gravava, mostrava "Transcrevendo…" e devolvia caixa vazia.

    **O áudio não é armazenado.** Ele existe nos bytes desta requisição, vai para
    o provedor e morre com ela: não há coluna, bucket, fila nem log que o
    guarde (LGPD, spec §2: voz de funcionário, e o prontuário é append-only, o
    que tornaria o áudio inapagável para sempre).

    Provedor fora do ar levanta ANTES de escrever qualquer coisa: nota vazia ou
    com placeholder seria registrar que o plantão foi documentado quando não
    foi.
    """
    await get_tenant_obj(session, Hospitalization, hospitalization_id, auth.clinic_id)
    if shift_id is not None:
        await get_tenant_obj(session, Shift, shift_id, auth.clinic_id)
    clinic = await session.get(Clinic, auth.clinic_id)

    # Recusa o tipo antes de ler os bytes: não vale gastar memória com o que
    # nenhum provedor de transcrição aceitaria.
    filename = TranscriptionService.upload_name(audio.content_type)
    try:
        audio_bytes = await TranscriptionService.read_capped(audio)
        try:
            text = await TranscriptionService.transcribe(
                audio_bytes, filename=filename, locale=clinic.locale
            )
        finally:
            # Descarte explícito, não confiança no coletor: a partir daqui o
            # áudio não existe mais em lugar nenhum do processo.
            del audio_bytes
    finally:
        await audio.close()

    note = ShiftNote(
        clinic_id=auth.clinic_id,
        hospitalization_id=hospitalization_id,
        shift_id=shift_id,
        membership_id=actor.membership_id,
        # Copiado no ato: o autor do registro clínico não muda depois.
        author_name=actor.name,
        text=text,
        # `audio` é a PROCEDÊNCIA do texto: quem ler o prontuário depois precisa
        # saber que a frase saiu de uma máquina transcrevendo, não de um teclado.
        source=ShiftNoteSource.audio,
    )
    session.add(note)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="shift_note_created",
        entity_type="shift_note",
        entity_id=note.id,
        after=AuditService.snapshot(note),
        # `provider` e `chars` descrevem a transcrição; os bytes do áudio não
        # entram na trilha; auditoria de prontuário também é append-only.
        extra={
            "source": ShiftNoteSource.audio.value,
            "provider": settings.ai_speech_provider,
            "chars": len(text),
        },
    )
    await session.commit()
    return ShiftNoteOut.model_validate(note)
