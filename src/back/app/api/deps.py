import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory
from app.core.errors import AppError
from app.core.security import decode_jwt
from app.models.clinic import Clinic
from app.models.membership import Membership
from app.models.user import User
from app.permissions import READ_ONLY_CAPABILITIES, can
from app.services.audit import ActorInfo


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


@dataclass
class AuthContext:
    kind: Literal["personal", "station"]
    clinic_id: uuid.UUID
    membership: Membership | None  # None quando kind == "station"


def _decode_bearer(authorization: str | None) -> dict[str, Any]:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AppError("invalid_credentials", 401)
    return decode_jwt(authorization.removeprefix("Bearer "))


async def _validate_station(session: AsyncSession, claims: dict[str, Any]) -> Clinic:
    clinic = await session.get(Clinic, uuid.UUID(claims["clinic_id"]))
    if clinic is None:
        raise AppError("invalid_credentials", 401)
    if claims.get("station_key_version") != clinic.station_key_version:
        # rotacionar a chave da estação revoga todo token emitido antes
        raise AppError("station_key_rotated", 401)
    return clinic


async def get_station_claims(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    claims = _decode_bearer(authorization)
    if claims.get("kind") != "station":
        raise AppError("forbidden", 403)
    await _validate_station(session, claims)
    return claims


async def get_current_auth(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> AuthContext:
    claims = _decode_bearer(authorization)
    if claims.get("kind") == "personal":
        membership = (
            await session.execute(
                select(Membership).where(
                    Membership.id == uuid.UUID(claims["membership_id"]),
                    Membership.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise AppError("invalid_credentials", 401)
        return AuthContext(kind="personal", clinic_id=membership.clinic_id, membership=membership)
    if claims.get("kind") == "station":
        clinic = await _validate_station(session, claims)
        return AuthContext(kind="station", clinic_id=clinic.id, membership=None)
    raise AppError("invalid_credentials", 401)


async def get_platform_operator(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> User:
    """Quem opera a plataforma: vende, faz onboarding e dá suporte.

    Só aceita `kind="platform"`. Um token pessoal ou de estação, por mais
    poderoso que seja dentro da clínica, não entra aqui; e o token da
    plataforma não entra em nenhuma rota de clínica (`get_current_auth` só
    conhece `personal` e `station`). As duas portas são disjuntas por
    construção, não por filtro."""
    claims = _decode_bearer(authorization)
    if claims.get("kind") != "platform":
        raise AppError("forbidden", 403)
    user = await session.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active or not user.is_platform_operator:
        raise AppError("invalid_credentials", 401)
    return user


async def get_operator(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
) -> ActorInfo:
    if auth.kind == "personal":
        membership = auth.membership
        user = await session.get(User, membership.user_id)
        return ActorInfo(
            membership_id=membership.id,
            name=user.name,
            license_number=membership.license_number,
            license_authority=membership.license_authority,
            role=membership.role,
        )
    # Modo estação: cada ação exige operator token de 5 min obtido via PIN.
    if x_operator_token is None:
        raise AppError("operator_required", 403)
    try:
        claims = decode_jwt(x_operator_token)
    except AppError as exc:
        raise AppError("operator_required", 403) from exc
    if claims.get("kind") != "operator" or claims.get("clinic_id") != str(auth.clinic_id):
        raise AppError("operator_required", 403)
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.id == uuid.UUID(claims["membership_id"]),
                Membership.clinic_id == auth.clinic_id,
                Membership.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise AppError("operator_required", 403)
    user = await session.get(User, membership.user_id)
    return ActorInfo(
        membership_id=membership.id,
        name=user.name,
        license_number=membership.license_number,
        license_authority=membership.license_authority,
        role=membership.role,
    )


async def get_optional_operator(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
) -> ActorInfo | None:
    """Quem está aqui, se der para saber, sem exigir PIN.

    `get_operator` recusa quando falta o PIN. Para uma LEITURA isso é fricção no
    lugar errado, e é a razão de nenhuma leitura do sistema ter capacidade
    nenhuma: expressá-la com a primitiva existente obrigaria a digitar PIN para
    *olhar* uma tela."""
    if auth.kind == "personal":
        return await get_operator(auth=auth, session=session, x_operator_token=None)
    if x_operator_token is None:
        return None
    try:
        return await get_operator(auth=auth, session=session, x_operator_token=x_operator_token)
    except AppError:
        return None


async def _ensure_writable(session: AsyncSession, clinic_id: uuid.UUID, capability: str) -> None:
    """A escrita para quando o teste vence. A leitura, não.

    Recebe a capacidade que AUTORIZOU o ator, nunca a lista da rota: em
    `require_any` as duas coisas são diferentes, e passar a lista deixaria
    uma rota que misture `hospitalization.discharge` com uma capacidade de
    escrita liberar a escrita para quem só tem a escrita — o gate nem
    chegaria a olhar a clínica.

    Fica aqui, e não em cada rota, porque TODA mutação clínica passa por
    `require` ou `require_any` — as únicas escritas de fora são login, troca do
    próprio PIN e registro de token de push, e as três devem mesmo continuar
    funcionando com o teste vencido.

    O que sobrevive está em `READ_ONLY_CAPABILITIES` (permissions.py), a mesma
    lista que encolhe a resposta de `/auth/me`: uma fonte, dois usos."""
    if capability in READ_ONLY_CAPABILITIES:
        return
    clinic = await session.get(Clinic, clinic_id)
    if clinic is None or not clinic.is_read_only:
        return
    raise AppError(
        "trial_expired",
        403,
        capability=capability,
        trial_ends_at=clinic.trial_ends_at.isoformat() if clinic.trial_ends_at else None,
    )


def require_read(capability: str) -> Any:
    """Autoriza uma LEITURA sensível.

    Nenhuma leitura do sistema tinha capacidade: um tablet logado na clínica,
    sem ninguém identificado, lia CPF e telefone de todo tutor, o extrato
    inteiro, o prontuário completo e a lista de e-mails da equipe.

    A regra: no modo pessoal responde o vínculo. Na estação responde o dono do
    PIN, e sem PIN a leitura sensível não acontece (`operator_required`, o
    mesmo código que a interface já sabe transformar em pedido de PIN). As
    leituras da operação (painel, fila, ficha, boxes, escala) seguem abertas:
    é exatamente para isso que o modo estação existe.
    """

    async def dependency(
        actor: ActorInfo | None = Depends(get_optional_operator),
    ) -> ActorInfo | None:
        if actor is None:
            raise AppError("operator_required", 403, capability=capability)
        if not can(actor.role, capability):
            raise AppError("forbidden", 403, capability=capability, role=actor.role)
        return actor

    return dependency


def require_any(*capabilities: str) -> Any:
    """Basta UMA das capacidades.

    Existe para a posologia: o administrador curadoria a tabela de preços
    (`price_list.manage`), mas quem confere se 0,15 mg/kg está certo é quem tem
    registro no conselho (`prescription.create`). Exigir só a primeira deixaria
    o veterinário sem poder corrigir uma dose errada sem chamar o administrador,
    fricção no caminho onde ela custa mais caro."""

    async def dependency(
        actor: ActorInfo = Depends(get_operator),
        auth: AuthContext = Depends(get_current_auth),
        session: AsyncSession = Depends(get_session),
    ) -> ActorInfo:
        # QUAL capacidade autorizou, não apenas SE alguma autorizou: é ela que
        # o gate do teste vencido precisa julgar.
        autorizada = next((c for c in capabilities if can(actor.role, c)), None)
        if autorizada is None:
            raise AppError("forbidden", 403, capability=capabilities[0], role=actor.role)
        await _ensure_writable(session, auth.clinic_id, autorizada)
        return actor

    return dependency


def require(capability: str) -> Any:
    """Exige a capacidade de QUEM AGE e devolve o ator, para a rota auditar.

    O ponto de checagem é `get_operator` de propósito: é ele que identifica a
    pessoa nos dois modos. No celular compartilhado quem responde pelo ato é o
    dono do PIN: checar o token do aparelho deixaria o técnico prescrever
    usando a estação."""

    async def dependency(
        actor: ActorInfo = Depends(get_operator),
        auth: AuthContext = Depends(get_current_auth),
        session: AsyncSession = Depends(get_session),
    ) -> ActorInfo:
        if not can(actor.role, capability):
            raise AppError("forbidden", 403, capability=capability, role=actor.role)
        await _ensure_writable(session, auth.clinic_id, capability)
        return actor

    return dependency


async def get_tenant_obj(
    session: AsyncSession, model: type, obj_id: uuid.UUID, clinic_id: uuid.UUID
) -> Any:
    obj = (
        await session.execute(select(model).where(model.id == obj_id, model.clinic_id == clinic_id))
    ).scalar_one_or_none()
    if obj is None:
        raise AppError("not_found", 404)
    return obj
