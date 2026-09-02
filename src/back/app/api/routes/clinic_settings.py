import re
import secrets
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_session,
    require,
    require_read,
)
from app.compliance import ComplianceProfile, get_profile, list_profiles
from app.core.errors import AppError
from app.core.security import hash_password
from app.models.clinic import Clinic, UnitSystem
from app.models.hospitalization import Hospitalization, HospitalizationStatus
from app.models.patient import Patient
from app.models.patient_identifier import PatientIdentifier
from app.models.plan import Plan
from app.permissions import CLINIC_CONFIGURE
from app.schemas.clinic import (
    ClinicOut,
    ClinicProfileOut,
    ClinicUpdate,
    IdentifierKindOut,
    StationKeyRotated,
)
from app.services.audit import ActorInfo, AuditService

router = APIRouter(prefix="/api/v1/clinic", tags=["clinic"])

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

#: Timbre do prontuário. Em branco vira NULL: um endereço gravado como "   "
#: faz o documento entregue ao tutor imprimir um separador solto no lugar da
#: identificação da clínica.
_LETTERHEAD = ("address", "phone", "tax_id")


def _require_admin(auth: AuthContext) -> None:
    # Estação não configura a clínica: o token de estação é compartilhado.
    if auth.kind != "personal" or auth.membership.role != "admin":
        raise AppError("forbidden", 403)


def _validate_timezone(timezone: str) -> None:
    """Um fuso inválido não falha aqui; falha depois, em toda prescrição.

    `ZoneInfo(clinic.timezone)` é chamado dentro do aprazamento, do extrato e
    da diária: salvar "Sao Paulo" no lugar de "America/Sao_Paulo" transformava
    a próxima prescrição num 500."""
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise AppError("validation_error", 422, field="timezone") from exc


def _validate_anchors(anchors: Any) -> None:
    """anchors = {"<minutos>": ["HH:MM", ...]}.

    A chave é a frequência em MINUTOS como string ("480") e o valor é a lista
    de horários locais em que as doses daquela frequência caem.
    """
    if not isinstance(anchors, dict):
        raise AppError("validation_error", 422, field="anchors")
    for key, times in anchors.items():
        if not isinstance(key, str) or not key.isdigit() or int(key) <= 0:
            raise AppError("validation_error", 422, field="anchors")
        if not isinstance(times, list) or not times:
            raise AppError("validation_error", 422, field="anchors")
        for value in times:
            if not isinstance(value, str) or not _HHMM.match(value):
                raise AppError("validation_error", 422, field="anchors")


async def _to_out(session: AsyncSession, clinic: Clinic) -> ClinicOut:
    active = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(Hospitalization)
            .where(
                Hospitalization.clinic_id == clinic.id,
                Hospitalization.status == HospitalizationStatus.active,
            )
        )
    ).scalar_one()
    out = ClinicOut.model_validate(clinic)
    out.active_hospitalizations = int(active)
    if clinic.plan_tier:
        out.plan_name = await session.scalar(
            sa.select(Plan.name).where(Plan.code == clinic.plan_tier)
        )
    return out


@router.get("", response_model=ClinicOut)
async def get_clinic(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    # Plano, limite de leitos e versão da chave de estação: informação
    # comercial e de segurança que a interface escondia do menu e a API
    # devolvia a qualquer token. Fuso, moeda e locale (que toda tela precisa)
    # saem por `/clinic/profile`, aberto a todo membro.
    _actor: Annotated[ActorInfo, Depends(require_read(CLINIC_CONFIGURE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClinicOut:
    clinic = await session.get(Clinic, auth.clinic_id)
    if clinic is None:
        raise AppError("not_found", 404)
    return await _to_out(session, clinic)


def _profile_out(profile: ComplianceProfile, clinic: Clinic | None = None) -> ClinicProfileOut:
    return ClinicProfileOut(
        profile=profile.name,
        locale=clinic.locale if clinic else "pt-BR",
        currency=clinic.currency if clinic else "BRL",
        unit_system=clinic.unit_system if clinic else UnitSystem.metric,
        timezone=clinic.timezone if clinic else "UTC",
        name_key=profile.name_key,
        responsible_label_key=profile.responsible_label_key,
        patient_identifier_kinds=[
            IdentifierKindOut(kind=k.kind, label_key=k.label_key, pattern=k.pattern)
            for k in profile.patient_identifier_kinds
        ],
        retention_years=profile.retention_years,
        license_authority_label_key=profile.license_authority_label_key,
        subscription_status=clinic.subscription_status if clinic else "active",
        trial_ends_at=clinic.trial_ends_at if clinic else None,
    )


@router.get("/profiles", response_model=list[ClinicProfileOut])
async def list_clinic_profiles(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> list[ClinicProfileOut]:
    """As áreas que a clínica pode escolher. Um perfil novo aparece aqui sozinho."""
    return [_profile_out(profile) for profile in list_profiles()]


@router.get("/profile", response_model=ClinicProfileOut)
async def get_clinic_profile(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClinicProfileOut:
    """O perfil de compliance da clínica, como contrato para a interface."""
    clinic = await session.get(Clinic, auth.clinic_id)
    if clinic is None:
        raise AppError("not_found", 404)
    return _profile_out(get_profile(clinic.compliance_profile), clinic)


@router.patch("", response_model=ClinicOut)
async def update_clinic(
    payload: ClinicUpdate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(CLINIC_CONFIGURE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClinicOut:
    _require_admin(auth)
    clinic = await session.get(Clinic, auth.clinic_id)
    if clinic is None:
        raise AppError("not_found", 404)

    changes = payload.model_dump(exclude_unset=True)
    if "compliance_profile" in changes:
        await _check_profile_change(session, clinic, changes["compliance_profile"])
    if "timezone" in changes:
        _validate_timezone(changes["timezone"])
    for field in _LETTERHEAD:
        if isinstance(changes.get(field), str):
            changes[field] = changes[field].strip() or None
    if "anchors" in changes:
        _validate_anchors(changes["anchors"])
        # Mudar âncoras NÃO reescreve tarefas já criadas: o aprazamento
        # congela o horário no momento da prescrição, e mexer no passado
        # falsificaria o prontuário. A âncora nova vale da próxima
        # prescrição em diante.

    before = AuditService.snapshot(clinic)
    for field, value in changes.items():
        setattr(clinic, field, value)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=clinic.id,
        actor=actor,
        action="clinic_updated",
        entity_type="clinic",
        entity_id=clinic.id,
        before=before,
        after=AuditService.snapshot(clinic),
    )
    await session.commit()
    return await _to_out(session, clinic)


async def _check_profile_change(
    session: AsyncSession, clinic: Clinic, new_profile: str
) -> None:
    """Trocar de área muda quais identificadores existem.

    A escolha é de onboarding, não um interruptor: uma clínica que já cadastrou
    microchips não vira clínica de saúde humana sem migrar esses dados, e
    trocar em silêncio deixaria identificação órfã, sem tela que a edite."""
    try:
        profile = get_profile(new_profile)
    except KeyError:
        raise AppError("validation_error", 422, field="compliance_profile") from None
    if profile.name == clinic.compliance_profile:
        return

    allowed = {kind.kind for kind in profile.patient_identifier_kinds}
    orphans = sorted(
        (
            await session.execute(
                sa.select(sa.distinct(PatientIdentifier.kind))
                .join(Patient, Patient.id == PatientIdentifier.patient_id)
                .where(
                    PatientIdentifier.clinic_id == clinic.id,
                    PatientIdentifier.kind.notin_(allowed) if allowed else sa.true(),
                )
            )
        ).scalars()
    )
    if orphans:
        raise AppError("compliance_profile_in_use", 409, kinds=orphans, profile=profile.name)


@router.post("/rotate-station-key", response_model=StationKeyRotated)
async def rotate_station_key(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(CLINIC_CONFIGURE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StationKeyRotated:
    _require_admin(auth)
    clinic = await session.get(Clinic, auth.clinic_id)
    if clinic is None:
        raise AppError("not_found", 404)

    station_key = secrets.token_urlsafe(16)
    clinic.station_key_hash = hash_password(station_key)
    # Incrementar a versão revoga TODO token de estação emitido antes:
    # get_current_auth compara station_key_version e devolve
    # station_key_rotated (401). É o botão de pânico do tablet perdido.
    clinic.station_key_version += 1
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=clinic.id,
        actor=actor,
        action="station_key_rotated",
        entity_type="clinic",
        entity_id=clinic.id,
        # station_key_hash está em AuditService.REDACTED; só a versão é registrada.
        extra={"station_key_version": clinic.station_key_version},
    )
    await session.commit()
    # A chave em claro aparece aqui e nunca mais: o banco guarda só o hash.
    return StationKeyRotated(
        station_key=station_key, station_key_version=clinic.station_key_version
    )
