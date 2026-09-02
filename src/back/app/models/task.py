import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.prescription import Criticality, PrescriptionCategory, _enum


class TaskStatus(StrEnum):
    pending = "pending"
    done = "done"
    partial = "partial"
    not_done = "not_done"
    cancelled = "cancelled"


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        sa.Index("ix_tasks_clinic_status_scheduled", "clinic_id", "status", "scheduled_for"),
        # Idempotência do aprazamento (regra 8 do contrato): a corrida entre a
        # criação da prescrição e o job de 48h é resolvida no banco, não em código.
        sa.Index(
            "uq_tasks_prescription_scheduled",
            "prescription_id",
            "scheduled_for",
            unique=True,
            postgresql_where=sa.text("prescription_id IS NOT NULL"),
        ),
        sa.ForeignKeyConstraint(
            ["prescription_id", "clinic_id"],
            ["prescriptions.id", "prescriptions.clinic_id"],
            name="fk_tasks_prescription_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    hospitalization_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("hospitalizations.id"), index=True
    )
    prescription_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, default=None)
    title: Mapped[str] = mapped_column(sa.Text)
    category: Mapped[PrescriptionCategory] = mapped_column(
        _enum(PrescriptionCategory, "prescription_category")
    )
    scheduled_for: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True))
    criticality: Mapped[Criticality] = mapped_column(_enum(Criticality, "criticality"))
    tolerance_minutes: Mapped[int] = mapped_column(sa.Integer)
    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus, "task_status"), default=TaskStatus.pending
    )
    executed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    executed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    retroactive: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    early: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    outcome_reason: Mapped[str | None] = mapped_column(sa.Text, default=None)
    values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    price_minor: Mapped[int | None] = mapped_column(sa.Integer, default=None)
