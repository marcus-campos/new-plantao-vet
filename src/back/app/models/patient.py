import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("owners.id"))
    name: Mapped[str] = mapped_column(sa.Text)
    species: Mapped[str] = mapped_column(sa.Text)
    breed: Mapped[str | None] = mapped_column(sa.Text, default=None)
    # SEMPRE em kg (unidade SI). clinics.unit_system decide a exibição — ADR-0004.
    weight_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 3), default=None)
    notes: Mapped[str | None] = mapped_column(sa.Text, default=None)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
