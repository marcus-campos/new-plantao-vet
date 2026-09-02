"""Rate limit de PIN por estação e definição de PIN único por clínica.

O relógio é injetável (now_fn) para o teste de liberação após 15 minutos não
depender de sleep. O singleton pin_throttle guarda estado em memória por
processo, o suficiente para a v1 (uma instância de API); o scheduler da GCP na
semana 4 já roda single-instance pelo mesmo motivo.
"""

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.models.membership import Membership
from app.services.audit import ActorInfo, AuditService


class PinThrottle:
    max_failures = 5
    lockout = timedelta(minutes=15)

    def __init__(self, now_fn: Callable[[], datetime] | None = None) -> None:
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._failures: dict[str, list[datetime]] = defaultdict(list)

    def check(self, station_id: str) -> None:
        now = self._now_fn()
        recent = [t for t in self._failures[station_id] if now - t < self.lockout]
        self._failures[station_id] = recent
        if len(recent) >= self.max_failures:
            newest = max(recent)
            retry_after = int((self.lockout - (now - newest)).total_seconds())
            raise AppError("pin_locked_out", 429, retry_after_seconds=retry_after)

    def register_failure(self, station_id: str) -> None:
        self._failures[station_id].append(self._now_fn())

    def reset(self, station_id: str) -> None:
        self._failures.pop(station_id, None)


pin_throttle = PinThrottle()


class PinService:
    @staticmethod
    async def set_pin(
        session: AsyncSession,
        *,
        membership: Membership,
        pin: str,
        actor: ActorInfo,
    ) -> None:
        # PIN é único por clínica: dois PINs iguais atribuiriam o ato clínico
        # à pessoa errada. bcrypt não permite busca por igualdade, então
        # verificamos contra cada membership ativo com PIN definido.
        others = (
            (
                await session.execute(
                    select(Membership).where(
                        Membership.clinic_id == membership.clinic_id,
                        Membership.id != membership.id,
                        Membership.is_active.is_(True),
                        Membership.pin_hash.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if any(verify_password(pin, other.pin_hash) for other in others):
            raise AppError("pin_duplicate", 409)

        membership.pin_hash = hash_password(pin)
        await session.flush()
        # pin_hash está em AuditService.REDACTED: o snapshot nunca o carrega.
        await AuditService.record(
            session,
            clinic_id=membership.clinic_id,
            actor=actor,
            action="pin_set",
            entity_type="membership",
            entity_id=membership.id,
        )
