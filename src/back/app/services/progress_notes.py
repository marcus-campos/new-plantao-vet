import uuid
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance import get_profile
from app.core.errors import AppError
from app.models import Clinic, Hospitalization, HospitalizationStatus
from app.models.progress_note import ProgressNote
from app.schemas.progress_note import ProgressNoteCreate
from app.services.audit import ActorInfo, AuditService

DEFAULT_THRESHOLD_HOURS = 24


class ProgressNoteService:
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        hospitalization: Hospitalization,
        clinic: Clinic,
        actor: ActorInfo,
        payload: ProgressNoteCreate,
    ) -> ProgressNote:
        """Assina a evolução. Nome e registro do autor vêm do ActorInfo e são
        gravados na linha: o perfil `br` exige autoria em cada procedimento."""
        if not any(payload.texts()):
            raise AppError("validation_error", 422, field="text")

        note = ProgressNote(
            clinic_id=clinic.id,
            hospitalization_id=hospitalization.id,
            membership_id=actor.membership_id,
            author_name=actor.name,
            author_license=actor.license_number,
            author_license_authority=actor.license_authority,
            subjective=payload.subjective,
            findings=payload.findings,
            assessment=payload.assessment,
            plan=payload.plan,
            amends_progress_note_id=payload.amends_progress_note_id,
        )
        session.add(note)
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=clinic.id,
            actor=actor,
            action="progress_note_signed",
            entity_type="progress_note",
            entity_id=note.id,
            after=AuditService.snapshot(note),
            extra=(
                {"amends": str(payload.amends_progress_note_id)}
                if payload.amends_progress_note_id
                else None
            ),
        )
        return note

    @staticmethod
    async def hours_since_last(
        session: AsyncSession, *, hospitalization_id: uuid.UUID, now: datetime
    ) -> float | None:
        last = await session.scalar(
            sa.select(sa.func.max(ProgressNote.signed_at)).where(
                ProgressNote.hospitalization_id == hospitalization_id
            )
        )
        if last is None:
            return None
        return (now - last).total_seconds() / 3600

    @staticmethod
    async def overdue_hospitalizations(
        session: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        now: datetime,
        threshold_hours: int = DEFAULT_THRESHOLD_HOURS,
    ) -> list[uuid.UUID]:
        """Internações ATIVAS sem evolução há mais de `threshold_hours`.

        Só vale onde o perfil de compliance exige evolução diária: a regra de
        país mora no perfil, nunca aqui. Quem nunca teve evolução conta a partir
        da admissão: quem entrou há duas horas ainda não está em falta.
        """
        clinic = await session.get(Clinic, clinic_id)
        if clinic is None:
            return []
        if not get_profile(clinic.compliance_profile).requires_daily_progress_note:
            return []

        last_note = (
            sa.select(
                ProgressNote.hospitalization_id.label("hospitalization_id"),
                sa.func.max(ProgressNote.signed_at).label("last_signed_at"),
            )
            .where(ProgressNote.clinic_id == clinic_id)
            .group_by(ProgressNote.hospitalization_id)
            .subquery()
        )
        reference = sa.func.coalesce(last_note.c.last_signed_at, Hospitalization.admitted_at)
        rows = await session.execute(
            sa.select(Hospitalization.id)
            .outerjoin(last_note, last_note.c.hospitalization_id == Hospitalization.id)
            .where(
                Hospitalization.clinic_id == clinic_id,
                Hospitalization.status == HospitalizationStatus.active,
                reference < now - timedelta(hours=threshold_hours),
            )
            .order_by(reference.asc())
        )
        return list(rows.scalars())
