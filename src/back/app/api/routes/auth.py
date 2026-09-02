import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_operator,
    get_session,
    get_station_claims,
)
from app.core.errors import AppError
from app.core.security import create_jwt, verify_password
from app.models.clinic import Clinic
from app.models.membership import Membership
from app.models.station_device import StationDevice
from app.models.user import User
from app.permissions import capabilities_of
from app.schemas.auth import (
    ChangeMyPinRequest,
    DeviceEnrolledResponse,
    DeviceEnrollRequest,
    LoginRequest,
    MeResponse,
    OperatorResponse,
    OperatorTokenResponse,
    PinRequest,
    StationLoginRequest,
    TokenResponse,
)
from app.services.audit import ActorInfo, AuditService
from app.services.pin import PinService, pin_throttle
from app.services.station_device import StationDeviceService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login")
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    user = (
        await session.execute(
            select(User).where(User.email == body.email, User.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise AppError("invalid_credentials", 401)

    # v1: um usuário tem um vínculo ativo; múltiplas clínicas viram seletor na v2
    membership = (
        await session.execute(
            select(Membership)
            .where(Membership.user_id == user.id, Membership.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if membership is None:
        raise AppError("invalid_credentials", 401)
    await _ensure_clinic_open(session, membership.clinic_id)

    token = create_jwt(
        {
            "kind": "personal",
            "sub": str(user.id),
            "clinic_id": str(membership.clinic_id),
            "membership_id": str(membership.id),
        },
        expires_in=timedelta(hours=12),
    )
    return TokenResponse(access_token=token)


@router.get("/me")
async def me(auth: AuthContext = Depends(get_current_auth)) -> MeResponse:
    role = auth.membership.role if auth.membership else None
    return MeResponse(
        kind=auth.kind,
        clinic_id=auth.clinic_id,
        membership_id=auth.membership.id if auth.membership else None,
        role=role,
        # A estação não tem papel próprio: quem age é o dono do PIN, e a
        # capacidade é conferida no ato. Aqui vai vazio de propósito.
        capabilities=sorted(capabilities_of(role)),
        has_pin=auth.membership is not None and auth.membership.pin_hash is not None,
    )


@router.get("/operator")
async def operator(actor: ActorInfo = Depends(get_operator)) -> OperatorResponse:
    """O que pode quem está com o dedo no aparelho agora.

    No modo pessoal responde o próprio vínculo. Na estação, o dono do PIN, e
    sem PIN devolve `operator_required`, que é o mesmo código que a interface já
    traduz em "identifique-se". É o que permite ao cliente parar de oferecer o
    impossível num dispositivo compartilhado.
    """
    return OperatorResponse(
        membership_id=actor.membership_id,
        name=actor.name,
        role=actor.role,
        license_number=actor.license_number,
        license_authority=actor.license_authority,
        capabilities=sorted(capabilities_of(actor.role)),
    )


@router.post("/station")
async def station_login(
    body: StationLoginRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    """Entrada do aparelho compartilhado.

    Dois caminhos: o aparelho liberado (`device_id` + `device_secret`) e a
    chave única da clínica, que continua valendo enquanto houver aparelho em
    campo que só conhece ela. O `station_id` do token passa a ser o ID DO
    APARELHO quando ele existe: era um UUID sorteado a cada login, e o
    bloqueio por erro de PIN, chaveado nele, sumia quando a pessoa relogava."""
    clinic = (
        await session.execute(select(Clinic).where(Clinic.slug == body.clinic_slug))
    ).scalar_one_or_none()
    if clinic is None:
        raise AppError("invalid_credentials", 401)
    _ensure_open(clinic)

    if body.device_id is not None and body.device_secret:
        device = await StationDeviceService.authenticate(
            session,
            clinic_id=clinic.id,
            device_id=body.device_id,
            secret=body.device_secret,
        )
        await session.commit()
        station_id = str(device.id)
    elif body.station_key:
        if clinic.station_key_hash is None or not verify_password(
            body.station_key, clinic.station_key_hash
        ):
            raise AppError("invalid_credentials", 401)
        station_id = str(uuid.uuid4())
    else:
        raise AppError("invalid_credentials", 401)

    token = create_jwt(
        {
            "kind": "station",
            "clinic_id": str(clinic.id),
            "station_key_version": clinic.station_key_version,
            # station_id é o aparelho quando ele existe, e uma sessão avulsa
            # no caminho antigo. É a chave do bloqueio por erro de PIN.
            "station_id": station_id,
        },
        expires_in=timedelta(hours=12),
    )
    return TokenResponse(access_token=token)


@router.post("/station/enroll")
async def enroll_device(
    body: DeviceEnrollRequest, session: AsyncSession = Depends(get_session)
) -> DeviceEnrolledResponse:
    """O aparelho troca o código de seis dígitos pelo próprio segredo.

    Aberta de propósito: quem chega aqui ainda não tem credencial nenhuma, e é
    exatamente isso que o código do administrador está resolvendo. O código
    vale cinco minutos, morre no uso e não diz se errou ou expirou."""
    clinic = (
        await session.execute(select(Clinic).where(Clinic.slug == body.clinic_slug))
    ).scalar_one_or_none()
    if clinic is None:
        raise AppError("invalid_credentials", 401)
    device, secret = await StationDeviceService.claim(
        session, clinic_id=clinic.id, code=body.code, name=body.device_name
    )
    await session.commit()
    return DeviceEnrolledResponse(
        device_id=device.id, device_secret=secret, device_name=device.name
    )


@router.post("/pin")
async def exchange_pin(
    body: PinRequest,
    claims: dict = Depends(get_station_claims),
    session: AsyncSession = Depends(get_session),
) -> OperatorTokenResponse:
    station_id: str = claims["station_id"]
    clinic_id = uuid.UUID(claims["clinic_id"])

    # O aparelho liberado tem identidade no banco, e o bloqueio dele DURA:
    # relogar não zera mais a contagem, e sair do bloqueio é ato de um
    # administrador. O caminho antigo (chave da clínica) segue com o limite em
    # memória, que é o que dá para fazer sem aparelho identificado.
    device = await _station_device(session, station_id=station_id, clinic_id=clinic_id)
    StationDeviceService.ensure_unlocked(device)
    if device is None:
        pin_throttle.check(station_id)  # 429 pin_locked_out durante o lockout

    memberships = (
        (
            await session.execute(
                select(Membership).where(
                    Membership.clinic_id == clinic_id,
                    Membership.is_active.is_(True),
                    Membership.pin_hash.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    match = next((m for m in memberships if verify_password(body.pin, m.pin_hash)), None)

    if match is None:
        bloqueou = await StationDeviceService.register_pin_failure(session, device=device)
        if device is None:
            pin_throttle.register_failure(station_id)
        await AuditService.record(
            session,
            clinic_id=clinic_id,
            actor=None,
            action="device_locked" if bloqueou else "pin_failed",
            entity_type="station_device" if device is not None else "station",
            entity_id=device.id if device is not None else None,
            extra={"station_id": station_id},
        )
        # commit ANTES do raise: o rastro da falha sobrevive ao rollback do erro
        await session.commit()
        # O quinto erro responde o bloqueio, não "credencial inválida": quem
        # está no aparelho precisa saber que tentar de novo não adianta e que
        # falta chamar um administrador.
        if bloqueou:
            raise AppError("device_locked", 423, device_name=device.name)
        raise AppError("invalid_credentials", 401)

    await StationDeviceService.register_pin_success(session, device=device)
    await session.commit()
    pin_throttle.reset(station_id)
    token = create_jwt(
        {
            "kind": "operator",
            "clinic_id": str(clinic_id),
            "membership_id": str(match.id),
        },
        expires_in=timedelta(minutes=5),
    )
    return OperatorTokenResponse(operator_token=token)


async def _station_device(
    session: AsyncSession, *, station_id: str, clinic_id: uuid.UUID
) -> StationDevice | None:
    """O aparelho por trás desta sessão de estação, quando existe.

    No caminho antigo `station_id` é um UUID sorteado no login e não aponta
    para aparelho nenhum: devolve None e o limite em memória continua valendo."""
    try:
        device_id = uuid.UUID(station_id)
    except ValueError:
        return None
    device = await session.get(StationDevice, device_id)
    if device is None or device.clinic_id != clinic_id:
        return None
    return device


@router.put("/me/pin", status_code=204)
async def change_my_pin(
    body: ChangeMyPinRequest,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Trocar o próprio PIN.

    Existia só o caminho do administrador definir o PIN de alguém, e ele passa
    pelo administrador: para trocar um PIN que a pessoa desconfia que alguém
    viu, era preciso pedir a outra pessoa. Quem tem o PIN atual troca sozinho.

    Sem `current_pin` só quando ainda não há PIN nenhum: um vínculo com PIN
    definido e uma sessão esquecida aberta num aparelho seria uma troca de PIN
    a um clique de distância para quem passasse por ali."""
    # Só o vínculo pessoal troca o próprio PIN: na estação quem responde é o
    # operador do momento, e a sessão do aparelho não é de ninguém.
    membership = auth.membership
    if membership is None or not membership.is_active:
        raise AppError("forbidden", 403)

    if membership.pin_hash is not None:
        if not body.current_pin or not verify_password(body.current_pin, membership.pin_hash):
            raise AppError("invalid_credentials", 401)
        if verify_password(body.new_pin, membership.pin_hash):
            # Trocar por ele mesmo não é trocar, e sair daqui com "salvo" faria
            # a pessoa acreditar que o PIN antigo deixou de valer.
            raise AppError("pin_same_as_current", 400)

    user = await session.get(User, membership.user_id)
    await PinService.set_pin(
        session,
        membership=membership,
        pin=body.new_pin,
        actor=ActorInfo(
            membership_id=membership.id,
            name=user.name if user is not None else "",
            role=membership.role,
            license_number=membership.license_number,
            license_authority=membership.license_authority,
        ),
    )
    await session.commit()


def _ensure_open(clinic: Clinic) -> None:
    """Assinatura suspensa ou cancelada fecha a porta NO LOGIN.

    Só no login, de propósito: uma sessão aberta no meio do plantão não cai por
    causa de boleto. Quem já está dentro termina o turno com prescrição e
    grade; quem chega depois vê o motivo, em vez de "credencial inválida"."""
    if clinic.subscription_status in ("suspended", "cancelled"):
        raise AppError("clinic_suspended", 403, status=clinic.subscription_status)


async def _ensure_clinic_open(session: AsyncSession, clinic_id: uuid.UUID) -> None:
    clinic = await session.get(Clinic, clinic_id)
    if clinic is not None:
        _ensure_open(clinic)
