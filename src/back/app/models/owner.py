import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    name: Mapped[str] = mapped_column(sa.Text)
    # E.164 (+5511999990000): pré-requisito do WhatsApp internacional (spec §2).
    phone_e164: Mapped[str] = mapped_column(sa.Text)
    tax_id: Mapped[str | None] = mapped_column(sa.Text, default=None)
    whatsapp_opt_in_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=None
    )
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
