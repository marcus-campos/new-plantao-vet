import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinic import Clinic
from app.models.handover_ack import HandoverAck
from app.models.handover_report import HandoverReport
from app.models.hospitalization import Hospitalization, HospitalizationStatus
from app.models.prescription import Prescription, PrescriptionKind
from app.models.shift import Shift
from app.models.shift_note import ShiftNote
from app.models.task import Task, TaskStatus
from app.services.audit import ActorInfo, AuditService
from app.services.tasks import TaskService

# Janela usada quando não há turno de origem: um plantão típico para trás.
FALLBACK_WINDOW = timedelta(hours=12)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class HandoverService:
    @staticmethod
    async def build_skeleton(
        session: AsyncSession,
        *,
        hospitalization_id: uuid.UUID,
        clinic_id: uuid.UUID,
        since: datetime,
        until: datetime,
    ) -> dict:
        """O esqueleto do boletim: DETERMINÍSTICO, contado do banco, sem IA.

        É a camada que sustenta a passagem sozinha: se a narrativa faltar, ficar
        errada ou o provedor cair, estes números continuam verdadeiros. Nada aqui
        depende de alguém ter escrito uma nota.

        `overdue` é SUBCONJUNTO de `pending`: a tarefa atrasada continua pendente
        (atraso é estado derivado, nunca persistido, spec §2). Somar os dois
        contaria a mesma tarefa duas vezes."""
        tasks = list(
            (
                await session.execute(
                    sa.select(Task)
                    .where(
                        Task.clinic_id == clinic_id,
                        Task.hospitalization_id == hospitalization_id,
                        Task.scheduled_for >= since,
                        Task.scheduled_for <= until,
                    )
                    .order_by(Task.scheduled_for.asc())
                )
            ).scalars()
        )
        counters = {status: 0 for status in ("done", "partial", "not_done", "pending", "overdue")}
        for task in tasks:
            if task.status in counters:
                counters[str(task.status)] += 1
            if task.status == TaskStatus.pending and TaskService.display_state(task, until) == (
                "overdue"
            ):
                counters["overdue"] += 1

        # Eventos = o que não veio do aprazamento: tarefa avulsa (sem prescrição)
        # e execução de PRN. É exatamente o que o próximo plantão não consegue
        # deduzir olhando a grade.
        prn_ids = set(
            (
                await session.execute(
                    sa.select(Prescription.id).where(
                        Prescription.clinic_id == clinic_id,
                        Prescription.hospitalization_id == hospitalization_id,
                        Prescription.kind == PrescriptionKind.prn,
                    )
                )
            )
            .scalars()
            .all()
        )
        events = [
            {
                "id": str(task.id),
                "title": task.title,
                "category": str(task.category),
                "status": str(task.status),
                "scheduled_for": _iso(task.scheduled_for),
                "executed_at": _iso(task.executed_at),
            }
            for task in tasks
            if task.prescription_id is None or task.prescription_id in prn_ids
        ]

        prescriptions = list(
            (
                await session.execute(
                    sa.select(Prescription).where(
                        Prescription.clinic_id == clinic_id,
                        Prescription.hospitalization_id == hospitalization_id,
                        sa.or_(
                            sa.and_(
                                Prescription.starts_at >= since,
                                Prescription.starts_at <= until,
                            ),
                            sa.and_(
                                Prescription.suspended_at >= since,
                                Prescription.suspended_at <= until,
                            ),
                        ),
                    )
                )
            ).scalars()
        )
        changes: dict[str, list[dict]] = {"created": [], "suspended": [], "adjusted": []}
        for prescription in prescriptions:
            entry = {"id": str(prescription.id), "name": prescription.name}
            started = since <= prescription.starts_at <= until
            if started and prescription.replaces_prescription_id is not None:
                # Titulação: nasce como versão de outra, não como prescrição nova.
                changes["adjusted"].append(
                    {**entry, "replaces": str(prescription.replaces_prescription_id)}
                )
            elif started:
                changes["created"].append(entry)
            suspended_at = prescription.suspended_at
            if suspended_at is not None and since <= suspended_at <= until:
                changes["suspended"].append(entry)

        notes = list(
            (
                await session.execute(
                    sa.select(ShiftNote)
                    .where(
                        ShiftNote.clinic_id == clinic_id,
                        ShiftNote.hospitalization_id == hospitalization_id,
                        ShiftNote.created_at >= since,
                        ShiftNote.created_at <= until,
                    )
                    .order_by(ShiftNote.created_at.asc())
                )
            ).scalars()
        )
        return {
            "period": {"since": _iso(since), "until": _iso(until)},
            "tasks": counters,
            "events": events,
            "prescription_changes": changes,
            "notes": [
                {
                    "id": str(note.id),
                    "author_name": note.author_name,
                    "text": note.text,
                    "source": str(note.source),
                    "created_at": _iso(note.created_at),
                }
                for note in notes
            ],
        }

    @staticmethod
    def window(from_shift: Shift | None, now: datetime) -> tuple[datetime, datetime]:
        if from_shift is None:
            return now - FALLBACK_WINDOW, now
        # O fim real do turno é quando ele fechou, não quando a escala previa:
        # plantão que varou meia hora entregou meia hora de tarefas.
        return from_shift.starts_at, from_shift.closed_at or from_shift.ends_at or now

    @staticmethod
    async def generate(
        session: AsyncSession,
        *,
        clinic: Clinic,
        from_shift: Shift | None,
        to_shift: Shift | None,
        actor: ActorInfo | None,
    ) -> list[HandoverReport]:
        """Um boletim por internação ATIVA. Idempotente por (from_shift,
        internação): gerar de novo devolve o boletim que já existe, para que uma
        segunda chamada não apague a narrativa nem a aprovação já dadas."""
        now = datetime.now(UTC)
        since, until = HandoverService.window(from_shift, now)

        hospitalizations = list(
            (
                await session.execute(
                    sa.select(Hospitalization)
                    .where(
                        Hospitalization.clinic_id == clinic.id,
                        Hospitalization.status == HospitalizationStatus.active,
                    )
                    .order_by(Hospitalization.admitted_at.asc())
                )
            ).scalars()
        )
        existing_stmt = sa.select(HandoverReport).where(HandoverReport.clinic_id == clinic.id)
        existing_stmt = existing_stmt.where(
            HandoverReport.from_shift_id == from_shift.id
            if from_shift is not None
            else HandoverReport.from_shift_id.is_(None)
        )
        existing = {
            report.hospitalization_id: report
            for report in (await session.execute(existing_stmt)).scalars()
        }

        reports: list[HandoverReport] = []
        for hospitalization in hospitalizations:
            if hospitalization.id in existing:
                report = existing[hospitalization.id]
                # Só completa o destinatário quando ele ainda não era conhecido;
                # nunca reescreve esqueleto, narrativa ou aprovação existentes.
                if to_shift is not None and report.to_shift_id is None:
                    report.to_shift_id = to_shift.id
                reports.append(report)
                continue
            skeleton = await HandoverService.build_skeleton(
                session,
                hospitalization_id=hospitalization.id,
                clinic_id=clinic.id,
                since=since,
                until=until,
            )
            report = HandoverReport(
                clinic_id=clinic.id,
                hospitalization_id=hospitalization.id,
                from_shift_id=from_shift.id if from_shift else None,
                to_shift_id=to_shift.id if to_shift else None,
                skeleton=skeleton,
                # A IA preenche depois, por endpoint próprio: o boletim existe e
                # é útil antes de qualquer chamada externa.
                narrative=None,
                created_at=now,
            )
            session.add(report)
            await session.flush()
            await AuditService.record(
                session,
                clinic_id=clinic.id,
                actor=actor,
                action="handover_report_created",
                entity_type="handover_report",
                entity_id=report.id,
                after=AuditService.snapshot(report),
                extra={"since": _iso(since), "until": _iso(until)},
            )
            reports.append(report)
        return reports

    @staticmethod
    async def set_narrative(
        session: AsyncSession,
        *,
        report: HandoverReport,
        narrative: str,
        actor: ActorInfo | None,
        authored: bool = False,
    ) -> HandoverReport:
        """`authored=True` quando o texto foi escrito por uma pessoa.

        A trilha precisa distinguir o que um modelo rascunhou do que alguém
        redigiu e assina: são responsabilidades diferentes, e o boletim é peça
        de prontuário."""
        before = AuditService.snapshot(report)
        report.narrative = narrative
        # Rascunho novo invalida a revisão anterior: aprovar um texto e exibir
        # outro seria assinar o que ninguém leu.
        report.reviewed_at = None
        report.reviewed_by = None
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=report.clinic_id,
            actor=actor,
            action="handover_narrative_written" if authored else "handover_narrative_drafted",
            entity_type="handover_report",
            entity_id=report.id,
            before=before,
            after=AuditService.snapshot(report),
        )
        return report

    @staticmethod
    async def approve(
        session: AsyncSession, *, report: HandoverReport, actor: ActorInfo
    ) -> HandoverReport:
        before = AuditService.snapshot(report)
        report.reviewed_at = datetime.now(UTC)
        report.reviewed_by = actor.membership_id
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=report.clinic_id,
            actor=actor,
            action="handover_approved",
            entity_type="handover_report",
            entity_id=report.id,
            before=before,
            after=AuditService.snapshot(report),
        )
        return report

    @staticmethod
    async def acknowledge(
        session: AsyncSession, *, report: HandoverReport, actor: ActorInfo, seconds: int | None
    ) -> HandoverAck:
        """Aceite do receptor. Aceita boletim NÃO revisado de propósito: a spec
        proíbe bloquear a passagem por falta de aprovação: o que faltou já está
        auditado, e travar o aceite só deixaria o próximo plantão sem registro."""
        ack = HandoverAck(
            clinic_id=report.clinic_id,
            handover_report_id=report.id,
            membership_id=actor.membership_id,
            acked_at=datetime.now(UTC),
            seconds_to_ack=seconds,
        )
        session.add(ack)
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=report.clinic_id,
            actor=actor,
            action="handover_acknowledged",
            entity_type="handover_report",
            entity_id=report.id,
            after=AuditService.snapshot(ack),
            extra={"seconds_to_ack": seconds, "reviewed": report.reviewed_at is not None},
        )
        return ack

    @staticmethod
    async def audit_missing_reviews(
        session: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        reports: list[HandoverReport],
        actor: ActorInfo | None,
        shift: Shift | None = None,
    ) -> list[HandoverReport]:
        """Turno fechado com boletim sem aprovação: registra a omissão e SEGUE.

        Nunca levanta erro. A regra da spec é literal: sem aprovação o plantão
        seguinte vê TUDO mesmo assim, com o selo "não revisado". Bloquear aqui
        empurraria a passagem para o WhatsApp, que é justamente o que o produto
        veio substituir."""
        missing = [report for report in reports if report.reviewed_at is None]
        for report in missing:
            await AuditService.record(
                session,
                clinic_id=clinic_id,
                actor=actor,
                action="handover_missing_review",
                entity_type="handover_report",
                entity_id=report.id,
                extra={
                    "shift_id": str(shift.id) if shift else None,
                    "hospitalization_id": str(report.hospitalization_id),
                },
            )
        return missing
