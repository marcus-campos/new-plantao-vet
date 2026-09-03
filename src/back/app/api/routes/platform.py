"""O back-office: quem vende, faz onboarding e dá suporte.

Tudo aqui é gatilho de `get_platform_operator`. Nenhuma rota de clínica aceita
o token da plataforma, e o token de clínica não entra aqui: as duas portas são
disjuntas por construção.

Toda ação que toca uma clínica fica na TRILHA DA CLÍNICA, com o nome de quem
fez e o prefixo "Suporte". O cliente vê que o suporte mexeu, e vê o quê. Sem
isso, o back-office seria uma porta dos fundos numa trilha que se vende como
íntegra.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_platform_operator, get_session
from app.core.errors import AppError
from app.core.security import create_jwt, hash_password, verify_password
from app.models.audit import AuditEntry
from app.models.clinic import Clinic
from app.models.hospitalization import Hospitalization, HospitalizationStatus
from app.models.membership import Membership
from app.models.plan import Plan
from app.models.station_device import StationDevice
from app.models.user import User
from app.schemas.auth import TokenResponse
from app.schemas.platform import (
    PasswordReset,
    PlanCreate,
    PlanMigrate,
    PlanMigrated,
    PlanOut,
    PlanUpdate,
    PlatformAuditOut,
    PlatformClinicCreate,
    PlatformClinicCreated,
    PlatformClinicOut,
    PlatformClinicRow,
    PlatformClinicUpdate,
    PlatformDeviceOut,
    PlatformLoginRequest,
    PlatformMemberOut,
    PlatformMeOut,
)
from app.services.audit import ActorInfo, AuditService
from app.services.onboarding import ClinicSpec, OnboardingService
from app.services.plans import PlanService

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])


def _support_actor(operator: User) -> ActorInfo:
    """Como o suporte aparece na trilha da clínica.

    `membership_id=None` porque o operador não é membro; o nome vem com o
    prefixo para ninguém confundir com alguém da equipe."""
    return ActorInfo(
        membership_id=None,
        name=f"Suporte PlantãoVet · {operator.name}",
        license_number=None,
        license_authority=None,
        role=None,
    )


# --- Sessão ------------------------------------------------------------------


@router.post("/login")
async def platform_login(
    body: PlatformLoginRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    user = (
        await session.execute(
            sa.select(User).where(
                User.email == body.email.strip().lower(), User.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    if (
        user is None
        or not user.is_platform_operator
        or not verify_password(body.password, user.password_hash)
    ):
        raise AppError("invalid_credentials", 401)
    token = create_jwt({"kind": "platform", "sub": str(user.id)}, expires_in=timedelta(hours=12))
    return TokenResponse(access_token=token)


@router.get("/me", response_model=PlatformMeOut)
async def platform_me(operator: Annotated[User, Depends(get_platform_operator)]) -> PlatformMeOut:
    return PlatformMeOut(id=operator.id, name=operator.name, email=operator.email)


# --- Clínicas ----------------------------------------------------------------


async def _counts(session: AsyncSession, clinic_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Membros, internados e última atividade, em três consultas para a lista
    inteira. Uma por clínica seria N+1 na tela que abre o dia do suporte."""
    if not clinic_ids:
        return {}
    out: dict[uuid.UUID, dict] = {cid: {} for cid in clinic_ids}
    membros = await session.execute(
        sa.select(Membership.clinic_id, sa.func.count())
        .where(Membership.clinic_id.in_(clinic_ids), Membership.is_active.is_(True))
        .group_by(Membership.clinic_id)
    )
    for cid, n in membros.all():
        out[cid]["members"] = int(n)
    internados = await session.execute(
        sa.select(Hospitalization.clinic_id, sa.func.count())
        .where(
            Hospitalization.clinic_id.in_(clinic_ids),
            Hospitalization.status == HospitalizationStatus.active,
        )
        .group_by(Hospitalization.clinic_id)
    )
    for cid, n in internados.all():
        out[cid]["active_hospitalizations"] = int(n)
    ultimo = await session.execute(
        sa.select(AuditEntry.clinic_id, sa.func.max(AuditEntry.created_at))
        .where(AuditEntry.clinic_id.in_(clinic_ids))
        .group_by(AuditEntry.clinic_id)
    )
    for cid, when in ultimo.all():
        out[cid]["last_activity_at"] = when
    return out


def _row(clinic: Clinic, counts: dict) -> PlatformClinicRow:
    row = PlatformClinicRow.model_validate(clinic)
    row.members = counts.get("members", 0)
    row.active_hospitalizations = counts.get("active_hospitalizations", 0)
    row.last_activity_at = counts.get("last_activity_at")
    return row


@router.get("/clinics", response_model=list[PlatformClinicRow])
async def list_clinics(
    _: Annotated[User, Depends(get_platform_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[PlatformClinicRow]:
    clinics = list(
        (await session.execute(sa.select(Clinic).order_by(Clinic.created_at.desc()))).scalars()
    )
    counts = await _counts(session, [c.id for c in clinics])
    return [_row(c, counts.get(c.id, {})) for c in clinics]


async def _detail(session: AsyncSession, clinic: Clinic) -> PlatformClinicOut:
    counts = (await _counts(session, [clinic.id])).get(clinic.id, {})
    base = _row(clinic, counts)
    out = PlatformClinicOut(
        **base.model_dump(),
        locale=clinic.locale,
        currency=clinic.currency,
        timezone=clinic.timezone,
        compliance_profile=clinic.compliance_profile,
        contact_email=clinic.contact_email,
        contact_phone=clinic.contact_phone,
        support_notes=clinic.support_notes,
        suspended_at=clinic.suspended_at,
        station_key_version=clinic.station_key_version,
    )
    membros = await session.execute(
        sa.select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.clinic_id == clinic.id)
        .order_by(Membership.role, User.name)
    )
    out.members_list = [
        PlatformMemberOut(
            membership_id=m.id,
            user_id=u.id,
            name=u.name,
            email=u.email,
            role=str(m.role),
            license_number=m.license_number,
            license_authority=m.license_authority,
            has_pin=m.pin_hash is not None,
            is_active=m.is_active,
        )
        for m, u in membros.all()
    ]
    aparelhos = await session.execute(
        sa.select(StationDevice)
        .where(StationDevice.clinic_id == clinic.id)
        .order_by(StationDevice.created_at.desc())
    )
    out.devices = [PlatformDeviceOut.model_validate(d) for d in aparelhos.scalars()]
    trilha = await session.execute(
        sa.select(AuditEntry)
        .where(AuditEntry.clinic_id == clinic.id)
        .order_by(AuditEntry.id.desc())
        .limit(30)
    )
    out.recent_audit = [PlatformAuditOut.model_validate(e) for e in trilha.scalars()]
    return out


async def _clinic(session: AsyncSession, clinic_id: uuid.UUID) -> Clinic:
    clinic = await session.get(Clinic, clinic_id)
    if clinic is None:
        raise AppError("not_found", 404)
    return clinic


@router.get("/clinics/{clinic_id}", response_model=PlatformClinicOut)
async def get_clinic(
    clinic_id: uuid.UUID,
    _: Annotated[User, Depends(get_platform_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlatformClinicOut:
    return await _detail(session, await _clinic(session, clinic_id))


@router.post("/clinics", response_model=PlatformClinicCreated, status_code=201)
async def create_clinic(
    payload: PlatformClinicCreate,
    operator: Annotated[User, Depends(get_platform_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlatformClinicCreated:
    """Onboarding: a clínica e o primeiro administrador, num ato só.

    O miolo vive em `OnboardingService`, compartilhado com o cadastro pelo
    site. Aqui fica o que é DESTA porta: quem pode chamar, e o ator que a
    trilha registra."""
    clinic, admin, _, password = await OnboardingService.create_clinic(
        session,
        spec=ClinicSpec(
            name=payload.name,
            admin_name=payload.admin_name,
            admin_email=payload.admin_email,
            admin_password=payload.admin_password,
            slug=payload.slug,
            plan_code=payload.plan_tier,
            subscription_status=payload.subscription_status,
            trial_days=payload.trial_days,
            bed_limit=payload.bed_limit,
            locale=payload.locale,
            currency=payload.currency,
            timezone=payload.timezone,
            compliance_profile=payload.compliance_profile,
            contact_name=payload.contact_name,
            contact_email=payload.contact_email,
            contact_phone=payload.contact_phone,
        ),
        actor=_support_actor(operator),
    )
    await session.commit()
    return PlatformClinicCreated(
        clinic=await _detail(session, clinic), admin_email=admin.email, admin_password=password
    )


@router.patch("/clinics/{clinic_id}", response_model=PlatformClinicOut)
async def update_clinic(
    clinic_id: uuid.UUID,
    payload: PlatformClinicUpdate,
    operator: Annotated[User, Depends(get_platform_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlatformClinicOut:
    """Plano, limite, status da assinatura, contato e anotações.

    Trocar de plano preenche o limite do plano quando ele não vem no corpo:
    é o caso comum, e obrigar a digitar 25 ao escolher "pro" é convite a
    esquecer."""
    clinic = await _clinic(session, clinic_id)
    before = AuditService.snapshot(clinic)
    changes = payload.model_dump(exclude_unset=True)

    if "plan_tier" in changes and changes["plan_tier"] is not None:
        # Trocar de plano reaplica o plano (limite e, se for de teste, o
        # teste). Um limite explícito no mesmo corpo (negociação) vence: por
        # isso ele continua em `changes` e é gravado depois.
        plan = await PlanService.assignable(session, changes.pop("plan_tier"))
        PlanService.apply(clinic, plan)

    if "subscription_status" in changes:
        status = changes["subscription_status"]
        # Suspender marca QUANDO; reativar limpa. A porta fecha no login, e
        # a data é o que o suporte precisa para responder "desde quando".
        if status in ("suspended", "cancelled") and clinic.subscription_status not in (
            "suspended",
            "cancelled",
        ):
            clinic.suspended_at = datetime.now(UTC)
        elif status not in ("suspended", "cancelled"):
            clinic.suspended_at = None

    for field, value in changes.items():
        setattr(clinic, field, value)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=clinic.id,
        actor=_support_actor(operator),
        action="clinic_subscription_updated",
        entity_type="clinic",
        entity_id=clinic.id,
        before=before,
        after=AuditService.snapshot(clinic),
    )
    await session.commit()
    return await _detail(session, clinic)


# --- Suporte a pessoas -------------------------------------------------------


async def _member(session: AsyncSession, clinic_id: uuid.UUID, membership_id: uuid.UUID):
    row = (
        await session.execute(
            sa.select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.id == membership_id, Membership.clinic_id == clinic_id)
        )
    ).first()
    if row is None:
        raise AppError("not_found", 404)
    return row


@router.post(
    "/clinics/{clinic_id}/members/{membership_id}/reset-password", response_model=PasswordReset
)
async def reset_password(
    clinic_id: uuid.UUID,
    membership_id: uuid.UUID,
    operator: Annotated[User, Depends(get_platform_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PasswordReset:
    """A ligação mais comum do suporte: "esqueci a senha".

    Sorteia uma senha ditável ao telefone e a devolve UMA vez. Fica na
    trilha da clínica que foi o suporte, e para quem."""
    membership, user = await _member(session, clinic_id, membership_id)
    password = OnboardingService.temporary_password()
    user.password_hash = hash_password(password)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=clinic_id,
        actor=_support_actor(operator),
        action="password_reset_by_support",
        entity_type="membership",
        entity_id=membership.id,
    )
    await session.commit()
    return PasswordReset(temporary_password=password)


@router.post("/clinics/{clinic_id}/members/{membership_id}/reset-pin", status_code=204)
async def reset_pin(
    clinic_id: uuid.UUID,
    membership_id: uuid.UUID,
    operator: Annotated[User, Depends(get_platform_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Zera o PIN. A pessoa define o próprio na próxima entrada: o suporte
    nunca escolhe o PIN de ninguém, porque o PIN identifica quem executou o
    ato clínico."""
    membership, _ = await _member(session, clinic_id, membership_id)
    membership.pin_hash = None
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=clinic_id,
        actor=_support_actor(operator),
        action="pin_reset_by_support",
        entity_type="membership",
        entity_id=membership.id,
    )
    await session.commit()


# --- Planos ------------------------------------------------------------------
#
# Era um dicionário no código. Quem vende cria o plano de lançamento, aposenta
# quando acabou a promoção e migra todo mundo para o definitivo, sem deploy.


async def _plan_out(
    session: AsyncSession, plan: Plan, counts: dict[str, int] | None = None
) -> PlanOut:
    counts = counts if counts is not None else await PlanService.clinic_counts(session)
    out = PlanOut.model_validate(plan)
    out.clinics = counts.get(plan.code, 0)
    return out


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    _: Annotated[User, Depends(get_platform_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[PlanOut]:
    plans = list(
        (
            await session.execute(sa.select(Plan).order_by(Plan.sort_order, Plan.created_at))
        ).scalars()
    )
    counts = await PlanService.clinic_counts(session)
    return [await _plan_out(session, p, counts) for p in plans]


@router.post("/plans", response_model=PlanOut, status_code=201)
async def create_plan(
    payload: PlanCreate,
    _: Annotated[User, Depends(get_platform_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlanOut:
    if await session.scalar(sa.select(Plan.id).where(Plan.code == payload.code)):
        raise AppError("plan_code_taken", 409, plan=payload.code)
    plan = Plan(**payload.model_dump())
    plan.currency = plan.currency.upper()
    session.add(plan)
    await session.flush()
    await session.commit()
    return await _plan_out(session, plan)


@router.patch("/plans/{code}", response_model=PlanOut)
async def update_plan(
    code: str,
    payload: PlanUpdate,
    _: Annotated[User, Depends(get_platform_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlanOut:
    """Nome, limite, preço, dias de teste e ativo/aposentado.

    Mudar o limite do plano NÃO mexe nas clínicas que já estão nele: o que
    cada uma tem foi combinado na hora. Migrar é o ato que reaplica."""
    plan = await PlanService.get(session, code)
    changes = payload.model_dump(exclude_unset=True)
    if "is_active" in changes:
        plan.retired_at = None if changes["is_active"] else datetime.now(UTC)
    for field, value in changes.items():
        setattr(plan, field, value)
    await session.flush()
    await session.commit()
    return await _plan_out(session, plan)


@router.delete("/plans/{code}", status_code=204)
async def delete_plan(
    code: str,
    _: Annotated[User, Depends(get_platform_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Só apaga plano VAZIO. Com clínica dentro, o caminho é migrar: apagar
    deixaria a clínica apontando para um plano que não existe."""
    plan = await PlanService.get(session, code)
    counts = await PlanService.clinic_counts(session)
    if counts.get(code, 0) > 0:
        raise AppError("plan_in_use", 409, plan=code, clinics=counts[code])
    await session.delete(plan)
    await session.commit()


@router.post("/plans/{code}/migrate", response_model=PlanMigrated)
async def migrate_plan(
    code: str,
    payload: PlanMigrate,
    operator: Annotated[User, Depends(get_platform_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlanMigrated:
    """Move todas as clínicas deste plano para outro, e aposenta este.

    O fim do plano "fundador". Cada clínica movida ganha uma entrada na
    própria trilha, com o plano de origem e o de destino."""
    source = await PlanService.get(session, code)
    target = await PlanService.get(session, payload.to)
    moved = await PlanService.migrate(
        session,
        source=source,
        target=target,
        actor=_support_actor(operator),
        retire_source=payload.retire_source,
    )
    await session.commit()
    counts = await PlanService.clinic_counts(session)
    return PlanMigrated(
        moved=len(moved),
        source=await _plan_out(session, source, counts),
        target=await _plan_out(session, target, counts),
    )
