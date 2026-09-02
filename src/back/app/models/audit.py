import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditEntry(Base):
    __tablename__ = "audit_entries"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("clinics.id"))
    actor_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("memberships.id"), default=None
    )
    actor_name: Mapped[str] = mapped_column(sa.Text)
    actor_license: Mapped[str | None] = mapped_column(sa.Text, default=None)
    actor_license_authority: Mapped[str | None] = mapped_column(sa.Text, default=None)
    action: Mapped[str] = mapped_column(sa.Text)
    entity_type: Mapped[str] = mapped_column(sa.Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[str] = mapped_column(sa.Text)
    entry_hash: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True))
