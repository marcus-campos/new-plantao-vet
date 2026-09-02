import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEntry


@dataclass
class ActorInfo:
    membership_id: uuid.UUID | None
    name: str
    license_number: str | None
    license_authority: str | None
    #: Papel de QUEM AGIU: na estação é o dono do PIN, não o aparelho.
    role: str | None = None


class AuditService:
    REDACTED = {
        "phone_e164",
        "tax_id",
        "password_hash",
        "pin_hash",
        "station_key_hash",
        # Segredo do aparelho e código de liberação: a trilha registra QUE um
        # aparelho entrou, nunca com o quê.
        "secret_hash",
        "enrollment_code_hash",
    }

    @staticmethod
    def snapshot(entity: Any) -> dict:
        # dict das colunas do model, excluindo REDACTED;
        # uuid/datetime/Decimal como str (payload precisa ser JSON puro).
        snap: dict[str, Any] = {}
        for column in sa.inspect(entity).mapper.columns:
            if column.key in AuditService.REDACTED:
                continue
            value = getattr(entity, column.key)
            if isinstance(value, uuid.UUID | datetime | Decimal):
                value = str(value)
            snap[column.key] = value
        return snap

    @staticmethod
    async def record(
        session: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        actor: ActorInfo | None,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None,
        before: dict | None = None,
        after: dict | None = None,
        extra: dict | None = None,
    ) -> None:
        payload = {"before": before, "after": after, "extra": extra}
        created_at = datetime.now(UTC)
        # prev_hash = entry_hash da última entrada da MESMA clínica ("" na 1ª).
        prev_hash = (
            await session.execute(
                sa.select(AuditEntry.entry_hash)
                .where(AuditEntry.clinic_id == clinic_id)
                .order_by(AuditEntry.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none() or ""
        canonical_payload = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        entry_hash = hashlib.sha256(
            f"{prev_hash}|{clinic_id}|{action}|{entity_type}|{entity_id}"
            f"|{canonical_payload}|{created_at.isoformat()}".encode()
        ).hexdigest()
        session.add(
            AuditEntry(
                clinic_id=clinic_id,
                actor_membership_id=actor.membership_id if actor else None,
                actor_name=actor.name if actor else "system",
                actor_license=actor.license_number if actor else None,
                actor_license_authority=actor.license_authority if actor else None,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
                created_at=created_at,
            )
        )
        await session.flush()
