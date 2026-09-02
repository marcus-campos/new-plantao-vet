import enum
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Role(enum.StrEnum):
    vet = "vet"
    tech = "tech"
    admin = "admin"


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        sa.UniqueConstraint("clinic_id", "user_id", name="uq_memberships_clinic_id_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("clinics.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    role: Mapped[Role] = mapped_column(
        sa.Enum(
            Role,
            name="role",
            native_enum=False,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    license_number: Mapped[str | None] = mapped_column(sa.Text, default=None)
    license_authority: Mapped[str | None] = mapped_column(sa.Text, default=None)
    pin_hash: Mapped[str | None] = mapped_column(sa.Text, default=None)
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
