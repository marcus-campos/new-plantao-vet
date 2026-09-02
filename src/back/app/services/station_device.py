"""Liberação, uso e revogação dos aparelhos compartilhados da clínica.

A chave de estação era um segredo único da clínica inteira: quem o tivesse
entrava de qualquer aparelho, revogá-lo derrubava todo mundo junto, e não
havia lista de quem estava usando. Aqui cada aparelho é um registro com nome,
segredo próprio e último acesso, liberado por um administrador com um código
de seis dígitos que expira em minutos.
"""

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.models.station_device import (
    StationDevice,
    new_device_secret,
    new_enrollment_code,
)
from app.services.audit import ActorInfo, AuditService


class StationDeviceService:
    #: Tempo de vida do código de liberação. Curto de propósito: o código é
    #: lido em voz alta ou copiado da tela do administrador para o aparelho
    #: que está na mão dele, o que leva segundos, não horas.
    ENROLLMENT_TTL = timedelta(minutes=5)
    #: Erros de PIN seguidos antes do bloqueio. É o mesmo número que já valia
    #: no rate limit em memória; o que muda é que agora ele dura.
    MAX_PIN_FAILURES = 5

    @staticmethod
    async def open_enrollment(
        session: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        name: str,
        actor: ActorInfo,
        now: datetime | None = None,
    ) -> tuple[StationDevice, str]:
        """Cria o aparelho pendente e devolve o código EM CLARO, uma vez só."""
        now = now or datetime.now(UTC)
        code = new_enrollment_code()
        device = StationDevice(
            clinic_id=clinic_id,
            name=name.strip() or "Aparelho",
            status="pending",
            enrollment_code_hash=hash_password(code),
            enrollment_expires_at=now + StationDeviceService.ENROLLMENT_TTL,
            approved_by=actor.membership_id,
            approved_by_name=actor.name,
        )
        session.add(device)
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=clinic_id,
            actor=actor,
            action="station_device_enrollment_opened",
            entity_type="station_device",
            entity_id=device.id,
            after=AuditService.snapshot(device),
        )
        return device, code

    @staticmethod
    async def claim(
        session: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        code: str,
        name: str | None,
        now: datetime | None = None,
    ) -> tuple[StationDevice, str]:
        """O aparelho apresenta o código e recebe o próprio segredo.

        O código é conferido contra TODOS os pendentes não expirados da
        clínica: hash não permite busca por igualdade. A resposta é sempre a
        mesma quando falha (`invalid_credentials`), porque distinguir "código
        errado" de "código expirado" diria a quem tenta se vale insistir."""
        now = now or datetime.now(UTC)
        pendentes = list(
            (
                await session.execute(
                    sa.select(StationDevice).where(
                        StationDevice.clinic_id == clinic_id,
                        StationDevice.status == "pending",
                        StationDevice.enrollment_code_hash.is_not(None),
                        StationDevice.enrollment_expires_at > now,
                    )
                )
            ).scalars()
        )
        device = next((d for d in pendentes if verify_password(code, d.enrollment_code_hash)), None)
        if device is None:
            raise AppError("invalid_credentials", 401)

        secret = new_device_secret()
        device.secret_hash = hash_password(secret)
        device.status = "active"
        device.approved_at = now
        device.last_seen_at = now
        # O código morre no uso: um código que continua valendo é uma segunda
        # porta aberta para o mesmo aparelho.
        device.enrollment_code_hash = None
        device.enrollment_expires_at = None
        if name and name.strip():
            device.name = name.strip()
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=clinic_id,
            actor=None,
            action="station_device_claimed",
            entity_type="station_device",
            entity_id=device.id,
            after=AuditService.snapshot(device),
        )
        return device, secret

    @staticmethod
    async def authenticate(
        session: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        device_id: uuid.UUID,
        secret: str,
        now: datetime | None = None,
    ) -> StationDevice:
        now = now or datetime.now(UTC)
        device = await session.get(StationDevice, device_id)
        if (
            device is None
            or device.clinic_id != clinic_id
            or device.status != "active"
            or device.secret_hash is None
            or not verify_password(secret, device.secret_hash)
        ):
            raise AppError("invalid_credentials", 401)
        device.last_seen_at = now
        await session.flush()
        return device

    @staticmethod
    def ensure_unlocked(device: StationDevice | None) -> None:
        """Aparelho bloqueado não troca PIN por operador nenhum.

        Sem `retry_after_seconds`: esperar não resolve mais. O que resolve é um
        administrador liberar, e a interface precisa dizer isso em vez de
        sugerir que a pessoa tente de novo daqui a pouco."""
        if device is not None and device.pin_locked_at is not None:
            raise AppError("device_locked", 423, device_name=device.name)

    @staticmethod
    async def register_pin_failure(
        session: AsyncSession, *, device: StationDevice | None, now: datetime | None = None
    ) -> bool:
        """Conta o erro e devolve se este foi o que bloqueou o aparelho."""
        if device is None:
            return False
        device.pin_failed_attempts += 1
        if device.pin_failed_attempts >= StationDeviceService.MAX_PIN_FAILURES:
            device.pin_locked_at = now or datetime.now(UTC)
            await session.flush()
            return True
        await session.flush()
        return False

    @staticmethod
    async def register_pin_success(session: AsyncSession, *, device: StationDevice | None) -> None:
        if device is None or device.pin_failed_attempts == 0:
            return
        device.pin_failed_attempts = 0
        await session.flush()

    @staticmethod
    async def unlock(
        session: AsyncSession, *, device: StationDevice, actor: ActorInfo
    ) -> StationDevice:
        before = AuditService.snapshot(device)
        device.pin_failed_attempts = 0
        device.pin_locked_at = None
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=device.clinic_id,
            actor=actor,
            action="station_device_unlocked",
            entity_type="station_device",
            entity_id=device.id,
            before=before,
            after=AuditService.snapshot(device),
        )
        return device

    @staticmethod
    async def revoke(
        session: AsyncSession,
        *,
        device: StationDevice,
        actor: ActorInfo,
        now: datetime | None = None,
    ) -> StationDevice:
        before = AuditService.snapshot(device)
        device.status = "revoked"
        device.revoked_at = now or datetime.now(UTC)
        # O segredo vai junto: um aparelho revogado que guardasse o segredo
        # voltaria a funcionar se alguém o reativasse por engano.
        device.secret_hash = None
        device.enrollment_code_hash = None
        device.enrollment_expires_at = None
        await session.flush()
        await AuditService.record(
            session,
            clinic_id=device.clinic_id,
            actor=actor,
            action="station_device_revoked",
            entity_type="station_device",
            entity_id=device.id,
            before=before,
            after=AuditService.snapshot(device),
        )
        return device
