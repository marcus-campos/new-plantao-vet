from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.i18n.catalog import translate
from app.models import Clinic, Hospitalization, HospitalizationStatus, Prescription
from app.schemas.hospitalization import HospitalizationCreate
from app.schemas.prescription import default_tolerance
from app.services.audit import ActorInfo, AuditService
from app.services.scheduling import SCHEDULING_HORIZON
from app.services.tasks import TaskService

OUTCOMES_REQUIRING_NOTE = {"died", "left_ama"}


class HospitalizationService:
    @staticmethod
    async def admit(
        session: AsyncSession, *, clinic: Clinic, payload: HospitalizationCreate, actor: ActorInfo
    ) -> tuple[Hospitalization, str | None]:
        if payload.consent_status == "emergency_no_consent" and not payload.consent_reason:
            raise AppError("consent_reason_required", 422)

        hospitalization = Hospitalization(clinic_id=clinic.id, **payload.model_dump())
        session.add(hospitalization)
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=clinic.id,
            actor=actor,
            action="hospitalization_admitted",
            entity_type="hospitalization",
            entity_id=hospitalization.id,
            after=AuditService.snapshot(hospitalization),
        )

        warning = None
        if clinic.bed_limit is not None:
            active = await session.scalar(
                sa.select(sa.func.count())
                .select_from(Hospitalization)
                .where(
                    Hospitalization.clinic_id == clinic.id,
                    Hospitalization.status == HospitalizationStatus.active,
                )
            )
            if active > clinic.bed_limit:
                warning = "bed_limit_exceeded"
                await AuditService.record(
                    session,
                    clinic_id=clinic.id,
                    actor=actor,
                    action="bed_limit_exceeded",
                    entity_type="clinic",
                    entity_id=clinic.id,
                    extra={"active": active, "bed_limit": clinic.bed_limit},
                )
        return hospitalization, warning

    @staticmethod
    async def close(
        session: AsyncSession,
        *,
        hospitalization: Hospitalization,
        outcome: str,
        note: str | None,
        actor: ActorInfo,
    ) -> Hospitalization:
        if outcome in OUTCOMES_REQUIRING_NOTE and not note:
            raise AppError("outcome_note_required", 422)

        before = AuditService.snapshot(hospitalization)
        hospitalization.status = HospitalizationStatus(outcome)
        hospitalization.ended_at = datetime.now(UTC)
        hospitalization.outcome_note = note
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=hospitalization.clinic_id,
            actor=actor,
            action="hospitalization_closed",
            entity_type="hospitalization",
            entity_id=hospitalization.id,
            before=before,
            after=AuditService.snapshot(hospitalization),
        )
        return hospitalization

    @staticmethod
    async def create_default_prescriptions(
        session: AsyncSession, *, hospitalization: Hospitalization, clinic: Clinic, actor: ActorInfo
    ) -> list[Prescription]:
        """Cerimônias do dia (spec §2): nascem da admissão e reusam aprazamento,
        tolerância e auditoria, sem entidade nova. O `name_key` é conteúdo NOSSO,
        então é traduzido no locale da clínica; nome digitado pela clínica nunca é."""
        created: list[Prescription] = []
        for template in clinic.default_prescriptions:
            prescription = Prescription(
                clinic_id=clinic.id,
                hospitalization_id=hospitalization.id,
                kind=template.get("kind", "recurring"),
                category=template.get("category", "care"),
                name=translate(template["name_key"], clinic.locale),
                details={"anchor": template["anchor"]} if template.get("anchor") else {},
                frequency_minutes=template["frequency_minutes"],
                criticality=template.get("criticality", "normal"),
                tolerance_minutes=default_tolerance(
                    template.get("criticality", "normal"),
                    template["frequency_minutes"],
                    clinic,
                ),
                starts_at=hospitalization.admitted_at,
                created_by=actor.membership_id,
            )
            session.add(prescription)
            created.append(prescription)
        await session.flush()
        until = datetime.now(UTC) + SCHEDULING_HORIZON
        for prescription in created:
            await TaskService.materialize(
                session, prescription=prescription, clinic=clinic, until=until
            )
            await AuditService.record(
                session,
                clinic_id=clinic.id,
                actor=actor,
                action="prescription_created",
                entity_type="prescription",
                entity_id=prescription.id,
                after=AuditService.snapshot(prescription),
                extra={"source": "clinic_default"},
            )
        return created
