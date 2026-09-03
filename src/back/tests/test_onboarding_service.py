"""O onboarding: um caminho, dois portões.

O back-office e o site chamam o MESMO método. Duas cópias divergiriam no dia
em que o onboarding ganhar um passo.
"""

import pytest
import sqlalchemy as sa

from app.core.errors import AppError
from app.core.security import verify_password
from app.models.audit import AuditEntry
from app.models.membership import Membership
from app.services.audit import ActorInfo
from app.services.onboarding import ClinicSpec, OnboardingService
from tests.factories import make_clinic, make_user

SITE = ActorInfo(
    membership_id=None,
    name="Cadastro pelo site",
    role=None,
    license_number=None,
    license_authority=None,
)


@pytest.mark.asyncio
async def test_slug_sai_do_nome(session):
    assert await OnboardingService.unique_slug(session, "Clínica Vida Animal") == (
        "clinica-vida-animal"
    )


@pytest.mark.asyncio
async def test_slug_repetido_ganha_sufixo(session):
    await make_clinic(session, slug="clinica-vida-animal")
    assert await OnboardingService.unique_slug(session, "Clínica Vida Animal") == (
        "clinica-vida-animal-2"
    )


@pytest.mark.asyncio
async def test_nome_que_nao_gera_slug_valido_nao_derruba_o_cadastro(session):
    # "Vet" tem 3 letras e o padrão exige no mínimo 3 + inicial/final
    # alfanumérica; "🐶" não deixa nada. Nenhum dos dois pode ser a razão de
    # uma clínica não conseguir se cadastrar.
    import re

    padrao = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
    for nome in ("Vet", "🐶", "  ", "A"):
        slug = await OnboardingService.unique_slug(session, nome)
        assert padrao.match(slug), f"{nome!r} gerou slug inválido: {slug!r}"


@pytest.mark.asyncio
async def test_cria_clinica_admin_e_vinculo(session):
    spec = ClinicSpec(
        name="Clínica Vida Animal",
        admin_name="Paula Martins",
        admin_email="  Paula@Vida.VET  ",
        admin_password="senha-boa-123",
        plan_code="trial",
    )
    clinic, admin, membership, senha = await OnboardingService.create_clinic(
        session, spec=spec, actor=SITE
    )
    assert membership.role == "admin"
    assert membership.clinic_id == clinic.id

    assert clinic.name == "Clínica Vida Animal"
    assert clinic.plan_tier == "trial"
    assert clinic.subscription_status == "trial"
    assert clinic.trial_ends_at is not None
    assert clinic.bed_limit == 10
    # E-mail normalizado: o login busca por minúsculas.
    assert admin.email == "paula@vida.vet"
    assert senha == "senha-boa-123"
    assert verify_password(senha, admin.password_hash)

    membership = await session.scalar(sa.select(Membership).where(Membership.user_id == admin.id))
    assert membership is not None
    assert membership.role == "admin"
    assert membership.clinic_id == clinic.id


@pytest.mark.asyncio
async def test_sem_senha_o_sistema_sorteia_uma(session):
    spec = ClinicSpec(name="Vida Animal", admin_name="Paula", admin_email="p@vida.vet")
    _, admin, _, senha = await OnboardingService.create_clinic(session, spec=spec, actor=SITE)
    assert len(senha) >= 12
    assert verify_password(senha, admin.password_hash)


@pytest.mark.asyncio
async def test_email_repetido_recusa(session):
    await make_user(session, email="paula@vida.vet")
    spec = ClinicSpec(name="Outra", admin_name="Paula", admin_email="Paula@Vida.vet")
    with pytest.raises(AppError) as exc:
        await OnboardingService.create_clinic(session, spec=spec, actor=SITE)
    assert exc.value.code == "email_taken"
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_a_trilha_diz_de_onde_a_clinica_veio(session):
    spec = ClinicSpec(name="Vida Animal", admin_name="Paula", admin_email="p@vida.vet")
    clinic, _, _, _ = await OnboardingService.create_clinic(session, spec=spec, actor=SITE)

    entry = await session.scalar(sa.select(AuditEntry).where(AuditEntry.clinic_id == clinic.id))
    assert entry is not None
    assert entry.action == "clinic_created"
    assert entry.actor_name == "Cadastro pelo site"
