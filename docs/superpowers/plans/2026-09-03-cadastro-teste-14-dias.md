# Cadastro pelo site e teste de 14 dias — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Uma clínica cria a própria conta pelo site, usa 14 dias, e no dia 15 o sistema vira somente-leitura em vez de sumir.

**Architecture:** O onboarding sai de `platform.create_clinic` para um `OnboardingService` que as duas portas chamam. O fim do teste é **estado derivado** (`Clinic.is_read_only`), não gravado, e o gate mora nas duas funções (`require`, `require_any`) por onde toda mutação clínica já passa. A lista do que sobrevive ao vencimento fica em `permissions.py`, e é a mesma fonte usada pelo gate e pela resposta de `/auth/me` — então o front não muda uma linha de `can()`.

**Tech Stack:** FastAPI · SQLAlchemy 2 async · Alembic · Pydantic v2 · pytest-asyncio · React 19 + Vite · react-router-dom · i18next

**Spec:** `docs/superpowers/specs/2026-09-03-cadastro-teste-14-dias-design.md`

## Global Constraints

- **Nunca renomear identificador existente.** `plan_tier`, `subscription_status`, `bed_limit`, `trial_ends_at` ficam com os nomes que estão — é o que toda trilha de auditoria já gravada usa.
- **Português nas telas, inglês no código.** Identificador em inglês é o nome canônico (ADR-0004); a UI pt-BR rotula em português. Ver `CONTEXT.md`.
- **Todo código de erro novo entra em `ERROR_CODES`** (`app/core/errors.py`) e ganha tradução em `src/front/src/i18n/pt-BR.json` e `en.json`.
- **Catálogo i18n é plano.** `"signup.hero.title"` É a chave inteira, não um caminho (`keySeparator: false`).
- **Dinheiro em unidade menor** (`price_minor`, centavos). Sempre.
- **Migração copia, não importa.** Uma migração precisa continuar rodando igual depois que o modelo mudar (padrão de `0020_plans.py`).
- **Rodar os testes:** `cd src/back && uv run pytest tests/<arquivo> -v`. Precisa do Postgres: `docker compose up -d postgres`.
- **Lint:** `cd src/back && uv run ruff check . && uv run ruff format .` · `cd src/front && npm run lint`
- Valores fixos: teste de **14 dias**, **10 leitos**, WhatsApp **5561983031823**.

---

### Task 1: O estado derivado e a lista do que sobrevive

O coração da regra, sem banco e sem HTTP: uma property e um conjunto. Tudo o mais depende disto.

**Files:**
- Modify: `src/back/app/models/clinic.py` (fim da classe `Clinic`)
- Modify: `src/back/app/permissions.py` (após `DEFAULT_ROLE_CAPABILITIES`)
- Test: `src/back/tests/test_read_only.py` (criar)

**Interfaces:**
- Consumes: nada.
- Produces:
  - `Clinic.is_read_only -> bool` (property)
  - `permissions.READ_ONLY_CAPABILITIES: frozenset[str]`
  - `permissions.capabilities_of(role: str | None, *, read_only: bool = False) -> frozenset[str]`

- [ ] **Step 1: Write the failing test**

Criar `src/back/tests/test_read_only.py`:

```python
"""O teste vence: o que a clínica ainda pode fazer.

Sem banco e sem HTTP de propósito — é a regra pura. O gate na API tem teste
próprio em test_trial_expiry.py.
"""

from datetime import UTC, datetime, timedelta

from app.models.clinic import Clinic
from app.permissions import (
    AUDIT_READ,
    CHARGES_READ,
    HOSPITALIZATION_DISCHARGE,
    OWNER_READ,
    PRESCRIPTION_CREATE,
    READ_ONLY_CAPABILITIES,
    RECORD_READ,
    TASK_EXECUTE,
    TEAM_READ,
    capabilities_of,
)

ONTEM = datetime.now(UTC) - timedelta(days=1)
AMANHA = datetime.now(UTC) + timedelta(days=1)


def test_trial_vencido_e_somente_leitura():
    clinic = Clinic(subscription_status="trial", trial_ends_at=ONTEM)
    assert clinic.is_read_only is True


def test_trial_vigente_nao_e_somente_leitura():
    clinic = Clinic(subscription_status="trial", trial_ends_at=AMANHA)
    assert clinic.is_read_only is False


def test_trial_sem_data_nao_e_somente_leitura():
    # Clínica de cortesia, sem data de fim: teste que não vence não vira nada.
    clinic = Clinic(subscription_status="trial", trial_ends_at=None)
    assert clinic.is_read_only is False


def test_assinatura_ativa_ignora_data_de_teste_no_passado():
    # Quem assinou carrega um trial_ends_at velho. Só `trial` vence.
    clinic = Clinic(subscription_status="active", trial_ends_at=ONTEM)
    assert clinic.is_read_only is False


def test_a_alta_sobrevive_ao_vencimento():
    # Congelar um sistema com paciente internado dentro seria prender o
    # animal num software vencido.
    assert HOSPITALIZATION_DISCHARGE in READ_ONLY_CAPABILITIES


def test_as_cinco_leituras_sensiveis_sobrevivem():
    for capability in (OWNER_READ, RECORD_READ, TEAM_READ, CHARGES_READ, AUDIT_READ):
        assert capability in READ_ONLY_CAPABILITIES


def test_escrita_clinica_nao_sobrevive():
    assert PRESCRIPTION_CREATE not in READ_ONLY_CAPABILITIES
    assert TASK_EXECUTE not in READ_ONLY_CAPABILITIES


def test_capabilities_of_filtra_quando_somente_leitura():
    vet_normal = capabilities_of("vet")
    vet_vencido = capabilities_of("vet", read_only=True)
    assert PRESCRIPTION_CREATE in vet_normal
    assert PRESCRIPTION_CREATE not in vet_vencido
    assert RECORD_READ in vet_vencido
    assert HOSPITALIZATION_DISCHARGE in vet_vencido
    assert vet_vencido < vet_normal


def test_capabilities_of_do_admin_perde_configurar():
    # É a armadilha do banner: `clinic.configure` é escrita e some da lista.
    # Por isso o banner de vencido não pode depender dela para aparecer.
    from app.permissions import CLINIC_CONFIGURE

    assert CLINIC_CONFIGURE not in capabilities_of("admin", read_only=True)


def test_papel_nulo_continua_sem_nada():
    assert capabilities_of(None, read_only=True) == frozenset()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/back && uv run pytest tests/test_read_only.py -v`
Expected: FAIL com `ImportError: cannot import name 'READ_ONLY_CAPABILITIES'`

- [ ] **Step 3: Write minimal implementation**

Em `src/back/app/permissions.py`, logo após `DEFAULT_ROLE_CAPABILITIES`:

```python
#: O que continua valendo quando o teste vence.
#:
#: As cinco leituras sensíveis, porque ler não é agir — e a ALTA, porque
#: congelar um sistema com paciente internado dentro seria prender o animal
#: num software vencido. A clínica precisa poder dar alta e levar o prontuário
#: embora. Um teste que termina sequestrando dado clínico não é um teste.
#:
#: Mora aqui, e não em `deps.py`, pelo mesmo motivo que o resto: "espalhar
#: `if role == ...` pelas rotas é como o sistema deixa passar um técnico
#: prescrevendo — a regra fica onde ninguém procura".
READ_ONLY_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        OWNER_READ,
        RECORD_READ,
        TEAM_READ,
        CHARGES_READ,
        AUDIT_READ,
        HOSPITALIZATION_DISCHARGE,
    }
)
```

E substituir `capabilities_of` (mantendo `can` como está):

```python
def capabilities_of(role: str | None, *, read_only: bool = False) -> frozenset[str]:
    """O que este papel pode. A interface usa para não oferecer o proibido.

    `read_only` é o teste vencido: a lista encolhe para o que sobrevive, e o
    front esconde o resto sozinho, sem saber que existe uma assinatura. Uma
    fonte da verdade, usada por este filtro e pelo gate em `deps.py`."""
    if role is None:
        return frozenset()
    capabilities = DEFAULT_ROLE_CAPABILITIES.get(role, frozenset())
    return capabilities & READ_ONLY_CAPABILITIES if read_only else capabilities
```

Em `src/back/app/models/clinic.py`, no fim da classe `Clinic` (depois de `created_at`):

```python
    @property
    def is_read_only(self) -> bool:
        """O teste venceu: lê tudo, escreve quase nada.

        DERIVADO, não gravado. Um status `expired` no banco dependeria de um
        job ter rodado; derivar da data está sempre certo, inclusive no
        segundo seguinte ao vencimento.

        Só `trial` vence. Quem assinou carrega um `trial_ends_at` antigo, e
        deixar a data mandar sozinha suspenderia cliente pagante."""
        return (
            self.subscription_status == "trial"
            and self.trial_ends_at is not None
            and self.trial_ends_at < datetime.now(UTC)
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/back && uv run pytest tests/test_read_only.py -v`
Expected: PASS (10 testes)

Depois: `cd src/back && uv run pytest tests/test_permissions.py -v` — a assinatura de `capabilities_of` mudou e nenhum chamador existente passa `read_only`, então tudo continua verde. Se falhar, o parâmetro não foi feito keyword-only.

- [ ] **Step 5: Commit**

```bash
git add src/back/app/permissions.py src/back/app/models/clinic.py src/back/tests/test_read_only.py
git commit -m "feat(billing): a regra do teste vencido, derivada da data

Clinic.is_read_only nasce da data, nao de um status gravado: um job que
nao rodou nao pode ser a diferenca entre cobrar e nao cobrar.

READ_ONLY_CAPABILITIES guarda o que sobrevive — as cinco leituras
sensiveis e a ALTA, para ninguem ficar com paciente internado dentro de
um sistema vencido."
```

---

### Task 2: O plano de teste no catálogo

**Files:**
- Create: `src/back/alembic/versions/0021_trial_plan.py`
- Test: `src/back/tests/test_trial_plan.py` (criar)

**Interfaces:**
- Consumes: a tabela `plans` de `0020`.
- Produces: um `Plan` com `code="trial"`, `trial_days=14`, `bed_limit=10`, `price_minor=0`, `sort_order=0`.

- [ ] **Step 1: Write the failing test**

Criar `src/back/tests/test_trial_plan.py`:

```python
"""O plano de teste existe e faz o que `Plan.trial_days` promete."""

from datetime import UTC, datetime

import sqlalchemy as sa
import pytest

from app.models.clinic import Clinic
from app.models.plan import Plan
from app.services.plans import PlanService
from tests.factories import make_clinic


@pytest.mark.asyncio
async def test_migracao_semeia_o_plano_trial(session):
    plan = await session.scalar(sa.select(Plan).where(Plan.code == "trial"))
    assert plan is not None
    assert plan.trial_days == 14
    assert plan.bed_limit == 10
    assert plan.price_minor == 0
    assert plan.is_active is True


@pytest.mark.asyncio
async def test_aplicar_o_plano_trial_marca_o_fim_do_teste(session):
    clinic = await make_clinic(session)
    plan = await PlanService.get(session, "trial")
    agora = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

    PlanService.apply(clinic, plan, now=agora)

    assert clinic.plan_tier == "trial"
    assert clinic.bed_limit == 10
    assert clinic.subscription_status == "trial"
    assert clinic.trial_ends_at == datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_o_plano_trial_e_atribuivel(session):
    # Aposentado nao receberia clinica nova, e e por ele que todo mundo entra.
    plan = await PlanService.assignable(session, "trial")
    assert plan.code == "trial"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/back && uv run pytest tests/test_trial_plan.py -v`
Expected: FAIL — `assert plan is not None` falha (não existe plano `trial`)

- [ ] **Step 3: Write minimal implementation**

Criar `src/back/alembic/versions/0021_trial_plan.py`:

```python
"""O plano de teste: 14 dias, 10 leitos, zero real.

`Plan.trial_days` já descrevia este caso ("um plano de teste é um plano com
trial_days > 0: quem entra nele começa em trial com a data de fim já
calculada") e nenhum plano do catálogo o exercia. É por ele que toda clínica
que se cadastra pelo site entra.

O limite de 10 leitos é suave, como todo bed_limit: nunca bloqueia uma
admissão, só avisa o administrador.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-03
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

TRIAL_CODE = "trial"


def upgrade() -> None:
    conn = op.get_bind()
    # Idempotente, como PlanService.ensure_defaults: um banco que já tem o
    # plano (criado à mão pelo back-office) não ganha um duplicado, e `code`
    # é unique — um INSERT cego derrubaria a migração.
    ja_existe = conn.execute(
        sa.text("SELECT 1 FROM plans WHERE code = :code"), {"code": TRIAL_CODE}
    ).first()
    if ja_existe:
        return
    conn.execute(
        sa.text(
            "INSERT INTO plans (id, code, name, bed_limit, price_minor, currency,"
            " trial_days, is_active, sort_order, notes, created_at)"
            " VALUES (:id, :code, 'Teste 14 dias', 10, 0, 'BRL', 14, true, 0, :notes, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "code": TRIAL_CODE,
            "notes": "Cadastro pelo site. 14 dias; depois a clínica vira somente-leitura.",
            "now": datetime.now(UTC),
        },
    )


def downgrade() -> None:
    # Só some se ninguém estiver nele: a chave estrangeira de clinics.plan_tier
    # recusaria, e apagar o plano de quem está testando seria perder o vínculo.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM plans WHERE code = :code"
            " AND NOT EXISTS (SELECT 1 FROM clinics WHERE plan_tier = :code)"
        ),
        {"code": TRIAL_CODE},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/back && uv run pytest tests/test_trial_plan.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Commit**

```bash
git add src/back/alembic/versions/0021_trial_plan.py src/back/tests/test_trial_plan.py
git commit -m "feat(billing): plano de teste de 14 dias no catalogo

Plan.trial_days ja descrevia este caso e nenhum plano do catalogo o
exercia. Migracao idempotente; o downgrade so apaga se ninguem estiver
no plano."
```

---

### Task 3: `OnboardingService` — um caminho, dois portões

**Files:**
- Create: `src/back/app/services/onboarding.py`
- Modify: `src/back/app/api/routes/platform.py:232-296` (`create_clinic` passa a delegar)
- Test: `src/back/tests/test_onboarding_service.py` (criar)

**Interfaces:**
- Consumes: `PlanService.assignable`, `AuditService.record`, `hash_password`.
- Produces:
  - `OnboardingService.unique_slug(session, name: str) -> str`
  - `OnboardingService.temporary_password() -> str`
  - `OnboardingService.create_clinic(session, *, spec: ClinicSpec, actor: ActorInfo) -> tuple[Clinic, User, Membership, str]` — clínica, admin, vínculo e a senha **em claro**. O vínculo sai daqui porque o cadastro pelo site precisa dele para montar o token, e reconsultá-lo seria uma query para buscar o que a função acabou de criar.
  - `ClinicSpec` (dataclass)

- [ ] **Step 1: Write the failing test**

Criar `src/back/tests/test_onboarding_service.py`:

```python
"""O onboarding: um caminho, dois portões.

O back-office e o site chamam o MESMO método. Duas cópias divergiriam no dia
em que o onboarding ganhar um passo.
"""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.core.errors import AppError
from app.core.security import verify_password
from app.models.audit import AuditEntry
from app.models.clinic import Clinic
from app.models.membership import Membership
from app.models.user import User
from app.services.audit import ActorInfo
from app.services.onboarding import ClinicSpec, OnboardingService
from tests.factories import make_clinic, make_user

SITE = ActorInfo(
    membership_id=None, name="Cadastro pelo site", role=None,
    license_number=None, license_authority=None,
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

    membership = await session.scalar(
        sa.select(Membership).where(Membership.user_id == admin.id)
    )
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

    entry = await session.scalar(
        sa.select(AuditEntry).where(AuditEntry.clinic_id == clinic.id)
    )
    assert entry is not None
    assert entry.action == "clinic_created"
    assert entry.actor_name == "Cadastro pelo site"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/back && uv run pytest tests/test_onboarding_service.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.services.onboarding'`

- [ ] **Step 3: Write minimal implementation**

Criar `src/back/app/services/onboarding.py`:

```python
"""O nascimento de uma clínica: um caminho, dois portões.

Existiam duas portas possíveis — o back-office, onde você faz o onboarding à
mão, e o site, onde a clínica se cadastra sozinha — e o miolo é o mesmo:
clínica, plano, administrador, vínculo e uma entrada na trilha. Duas cópias
divergiriam no dia em que o onboarding ganhar um passo, e a que ficasse para
trás seria justamente a que ninguém olha.

O que separa as duas portas é o ATOR na trilha: "Suporte PlantãoVet · <nome>"
de um lado, "Cadastro pelo site" do outro. O cliente vê de onde a clínica veio.
"""

import secrets
import unicodedata
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import hash_password
from app.models.clinic import Clinic
from app.models.membership import Membership
from app.models.user import User
from app.services.audit import ActorInfo, AuditService
from app.services.plans import PlanService

#: O que o back-office já exige de um slug (PlatformClinicCreate).
#: Repetido como constante porque agora quem GERA slug é este serviço.
SLUG_MIN = 3
SLUG_MAX = 40


@dataclass(slots=True)
class ClinicSpec:
    """Uma clínica a nascer. Os defaults servem ao cadastro pelo site; o
    back-office preenche o resto quando a negociação pediu outra coisa."""

    name: str
    admin_name: str
    admin_email: str
    admin_password: str | None = None
    #: Vazio: gerado do nome. O back-office manda o dele.
    slug: str | None = None
    plan_code: str = "trial"
    subscription_status: str = "trial"
    #: Só vale quando o plano NÃO é de teste; um plano de teste traz a duração.
    trial_days: int | None = None
    bed_limit: int | None = None
    locale: str = "pt-BR"
    currency: str = "BRL"
    timezone: str = "America/Sao_Paulo"
    compliance_profile: str = "br"
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


class OnboardingService:
    @staticmethod
    def temporary_password() -> str:
        """Legível ao telefone: sem 0/O, 1/l, e em grupos.

        Uma senha ditada por voz precisa sobreviver à ligação ruim."""
        alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"

    @staticmethod
    async def unique_slug(session: AsyncSession, name: str) -> str:
        """O slug sai do nome. Nunca é a razão de um cadastro falhar.

        "Slug" é jargão e a porta pública não pergunta. O nome da clínica vira
        `clinica-vida-animal`; nome curto demais, vazio ou só de emoji ganha um
        sufixo aleatório em vez de 422 — o padrão do back-office exige de 3 a
        40 caracteres com as pontas alfanuméricas, e um cadastro perdido por
        causa disso seria um cliente perdido por causa de uma regra interna."""
        sem_acento = (
            unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        )
        base = "".join(c if c.isalnum() else "-" for c in sem_acento.lower())
        base = "-".join(p for p in base.split("-") if p)[:SLUG_MAX].strip("-")
        if len(base) < SLUG_MIN:
            base = f"clinica-{secrets.token_hex(3)}"

        candidato = base
        n = 1
        while await session.scalar(sa.select(Clinic.id).where(Clinic.slug == candidato)):
            n += 1
            sufixo = f"-{n}"
            candidato = f"{base[: SLUG_MAX - len(sufixo)]}{sufixo}"
        return candidato

    @staticmethod
    async def create_clinic(
        session: AsyncSession, *, spec: ClinicSpec, actor: ActorInfo
    ) -> tuple[Clinic, User, Membership, str]:
        """A clínica, o primeiro administrador e o vínculo, num ato.

        NÃO faz commit: quem chama decide a transação. A rota do back-office
        precisa montar a ficha de detalhe antes de fechar, e a do site precisa
        emitir o token.

        Devolve a senha EM CLARO, uma vez. Depois disto só existe o hash. O
        vínculo sai junto porque o cadastro pelo site precisa dele para montar
        o token, e reconsultá-lo seria buscar o que acabou de ser criado."""
        plan = await PlanService.assignable(session, spec.plan_code)
        slug = spec.slug or await OnboardingService.unique_slug(session, spec.name)
        if spec.slug and await session.scalar(
            sa.select(Clinic.id).where(Clinic.slug == slug)
        ):
            raise AppError("slug_taken", 409)

        email = spec.admin_email.strip().lower()
        if await session.scalar(sa.select(User.id).where(User.email == email)):
            raise AppError("email_taken", 409)

        clinic = Clinic(
            name=spec.name.strip(),
            slug=slug,
            subscription_status=spec.subscription_status,
            locale=spec.locale,
            currency=spec.currency.upper(),
            timezone=spec.timezone,
            compliance_profile=spec.compliance_profile,
            contact_name=spec.contact_name,
            contact_email=spec.contact_email,
            contact_phone=spec.contact_phone,
        )
        # O plano decide limite e, quando é de teste, o próprio teste. Um
        # limite informado por fora (negociação) vence o do plano.
        PlanService.apply(clinic, plan)
        if spec.bed_limit is not None:
            clinic.bed_limit = spec.bed_limit
        # Teste com prazo negociado num plano PAGO: o plano não traz duração,
        # então quem vende diz quantos dias.
        if spec.subscription_status == "trial" and clinic.trial_ends_at is None:
            from datetime import UTC, datetime, timedelta

            clinic.trial_ends_at = datetime.now(UTC) + timedelta(days=spec.trial_days or 30)
        session.add(clinic)
        await session.flush()

        password = spec.admin_password or OnboardingService.temporary_password()
        admin = User(
            name=spec.admin_name.strip(), email=email, password_hash=hash_password(password)
        )
        session.add(admin)
        await session.flush()
        membership = Membership(clinic_id=clinic.id, user_id=admin.id, role="admin")
        session.add(membership)
        await session.flush()

        await AuditService.record(
            session,
            clinic_id=clinic.id,
            actor=actor,
            action="clinic_created",
            entity_type="clinic",
            entity_id=clinic.id,
            after=AuditService.snapshot(clinic),
        )
        return clinic, admin, membership, password
```

Mover o `from datetime import ...` para o topo do arquivo (o import dentro da função acima é para deixar claro onde é usado; o ruff vai reclamar — suba-o).

Agora `src/back/app/api/routes/platform.py`: substituir o corpo de `create_clinic` (linhas 232–296) por uma delegação, mantendo a assinatura, o `response_model` e o `_support_actor`:

```python
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
```

Ajustar os imports de `platform.py`: acrescentar `from app.services.onboarding import ClinicSpec, OnboardingService`. **Não apague `_temporary_password`** sem antes rodar `grep -rn "_temporary_password" src/back` — ele também serve ao reset de senha do suporte.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/back && uv run pytest tests/test_onboarding_service.py tests/test_platform.py -v`
Expected: PASS. `test_platform.py` é a rede de segurança: o back-office tem que continuar idêntico depois da extração.

- [ ] **Step 5: Commit**

```bash
git add src/back/app/services/onboarding.py src/back/app/api/routes/platform.py src/back/tests/test_onboarding_service.py
git commit -m "refactor(onboarding): extrair o nascimento da clinica para um servico

O back-office e o cadastro pelo site precisam do mesmo miolo. Duas copias
divergiriam no dia em que o onboarding ganhar um passo. O que separa as
portas e o ator na trilha."
```

---

### Task 4: `POST /api/v1/signup` — a porta pública

**Files:**
- Create: `src/back/app/api/routes/signup.py`
- Create: `src/back/app/schemas/signup.py`
- Modify: `src/back/app/core/errors.py` (acrescentar `signup_rate_limited`, `trial_expired`)
- Modify: `src/back/app/main.py` (registrar o router)
- Test: `src/back/tests/test_signup.py` (criar)

**Interfaces:**
- Consumes: `OnboardingService.create_clinic`, `ClinicSpec`, `create_jwt`.
- Produces: `POST /api/v1/signup` → `TokenResponse`; `signup.signup_throttle` (instância de `SignupThrottle`, resetável no teste).

- [ ] **Step 1: Write the failing test**

Criar `src/back/tests/test_signup.py`:

```python
"""A porta pública: uma clínica nasce sem passar por ninguém."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.models.clinic import Clinic
from tests.factories import make_user
from tests.helpers import bearer

CORPO = {
    "clinic_name": "Clínica Vida Animal",
    "admin_name": "Paula Martins",
    "email": "paula@vida.vet",
    "password": "senha-boa-123",
    "phone": "61999998888",
}


@pytest.fixture(autouse=True)
def _throttle_limpo():
    # O limite é de PROCESSO: sem zerar, o sexto teste do arquivo levaria 429.
    from app.api.routes.signup import signup_throttle

    signup_throttle.reset_all()
    yield
    signup_throttle.reset_all()


@pytest.mark.asyncio
async def test_cadastro_cria_clinica_e_ja_entra(client, session):
    resposta = await client.post("/api/v1/signup", json=CORPO)
    assert resposta.status_code == 201, resposta.text
    token = resposta.json()["access_token"]

    # O token vale de verdade: entra em /auth/me como administrador.
    me = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["role"] == "admin"
    assert me.json()["read_only"] is False


@pytest.mark.asyncio
async def test_a_clinica_nasce_em_teste_de_14_dias(client, session):
    await client.post("/api/v1/signup", json=CORPO)
    clinic = await session.scalar(
        sa.select(Clinic).where(Clinic.name == "Clínica Vida Animal")
    )
    assert clinic.plan_tier == "trial"
    assert clinic.subscription_status == "trial"
    assert clinic.bed_limit == 10
    faltam = clinic.trial_ends_at - datetime.now(UTC)
    assert timedelta(days=13, hours=23) < faltam <= timedelta(days=14)


@pytest.mark.asyncio
async def test_slug_sai_do_nome_sem_perguntar(client, session):
    await client.post("/api/v1/signup", json=CORPO)
    clinic = await session.scalar(
        sa.select(Clinic).where(Clinic.name == "Clínica Vida Animal")
    )
    assert clinic.slug == "clinica-vida-animal"


@pytest.mark.asyncio
async def test_duas_clinicas_com_o_mesmo_nome_convivem(client, session):
    primeira = await client.post("/api/v1/signup", json=CORPO)
    segunda = await client.post(
        "/api/v1/signup", json={**CORPO, "email": "outra@vida.vet"}
    )
    assert primeira.status_code == 201
    assert segunda.status_code == 201
    slugs = list(
        (
            await session.execute(
                sa.select(Clinic.slug).where(Clinic.name == "Clínica Vida Animal")
            )
        ).scalars()
    )
    assert len(slugs) == 2
    assert len(set(slugs)) == 2


@pytest.mark.asyncio
async def test_email_ja_cadastrado_recusa_com_o_codigo_certo(client, session):
    await make_user(session, email="paula@vida.vet")
    resposta = await client.post("/api/v1/signup", json=CORPO)
    assert resposta.status_code == 409
    assert resposta.json()["error"]["code"] == "email_taken"


@pytest.mark.asyncio
async def test_senha_curta_e_recusada(client):
    resposta = await client.post("/api/v1/signup", json={**CORPO, "password": "1234"})
    assert resposta.status_code == 422


@pytest.mark.asyncio
async def test_o_sexto_cadastro_do_mesmo_ip_na_mesma_hora_e_barrado(client):
    for i in range(5):
        resposta = await client.post(
            "/api/v1/signup", json={**CORPO, "email": f"vet{i}@vida.vet"}
        )
        assert resposta.status_code == 201, resposta.text
    barrado = await client.post(
        "/api/v1/signup", json={**CORPO, "email": "sexto@vida.vet"}
    )
    assert barrado.status_code == 429
    assert barrado.json()["error"]["code"] == "signup_rate_limited"


@pytest.mark.asyncio
async def test_ips_diferentes_nao_compartilham_o_limite(client):
    for i in range(5):
        await client.post("/api/v1/signup", json={**CORPO, "email": f"vet{i}@vida.vet"})
    outro = await client.post(
        "/api/v1/signup",
        json={**CORPO, "email": "outro-ip@vida.vet"},
        headers={"X-Forwarded-For": "203.0.113.7"},
    )
    assert outro.status_code == 201, outro.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/back && uv run pytest tests/test_signup.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.api.routes.signup'`

- [ ] **Step 3: Write minimal implementation**

Em `src/back/app/core/errors.py`, dentro de `ERROR_CODES`, junto do bloco da plataforma:

```python
        # --- Cadastro pelo site e fim do teste ---------------------------
        # `trial_expired` é 403 e não 402: não há nada a pagar dentro do
        # produto ainda. Quem recebe precisa saber que a escrita parou e por
        # quê, e a leitura continua aberta.
        "signup_rate_limited",
        "trial_expired",
```

Criar `src/back/app/schemas/signup.py`:

```python
"""O contrato da porta pública: o mínimo para uma clínica existir."""

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    """Quatro campos obrigatórios, e nenhum deles é jargão.

    Sem `slug` (gerado do nome), sem `plan_tier` (a porta pública não escolhe
    plano) e sem `timezone` (o Brasil é o mercado da v1, e a clínica troca nas
    configurações). Cada campo a mais aqui é um cadastro a menos."""

    clinic_name: str = Field(min_length=2, max_length=120)
    admin_name: str = Field(min_length=2, max_length=120)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
```

Criar `src/back/app/api/routes/signup.py`:

```python
"""A porta pública: uma clínica se cadastra sozinha e entra na hora.

Aberta de propósito, e é a única rota do sistema que CRIA um tenant sem
credencial nenhuma. O que a protege é o limite por IP daqui e o fato de o
plano de entrada ser um teste que vence: uma clínica falsa não custa nada
além de uma linha, e some da lista em 14 dias.

Não fica em `auth.py` de propósito: aquilo é sessão, isto é o nascimento de
uma clínica.
"""

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import AppError
from app.core.security import create_jwt
from app.schemas.auth import TokenResponse
from app.schemas.signup import SignupRequest
from app.services.audit import ActorInfo
from app.services.onboarding import ClinicSpec, OnboardingService

router = APIRouter(prefix="/api/v1", tags=["signup"])

#: Como o cadastro pelo site aparece na trilha da clínica. `membership_id=None`
#: porque não há ninguém da equipe agindo: a clínica ainda não existia.
SITE_ACTOR = ActorInfo(
    membership_id=None,
    name="Cadastro pelo site",
    role=None,
    license_number=None,
    license_authority=None,
)


class SignupThrottle:
    """Cinco cadastros por hora por IP.

    Em memória, como o `PinThrottle` que já resolve o mesmo tipo de problema.
    Serve a uma VM só, que é o deploy de hoje; com duas instâncias isto vira
    Redis, e esta classe é o único ponto de troca.

    Sem captcha de propósito: no lançamento, o custo de um cadastro perdido é
    maior que o de uma clínica falsa que vence em 14 dias.
    """

    max_signups = 5
    window = timedelta(hours=1)

    def __init__(self, now_fn: Callable[[], datetime] | None = None) -> None:
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._hits: dict[str, list[datetime]] = defaultdict(list)

    def check(self, ip: str) -> None:
        now = self._now_fn()
        recent = [t for t in self._hits[ip] if now - t < self.window]
        self._hits[ip] = recent
        if len(recent) >= self.max_signups:
            retry_after = int((self.window - (now - min(recent))).total_seconds())
            raise AppError("signup_rate_limited", 429, retry_after_seconds=retry_after)

    def register(self, ip: str) -> None:
        self._hits[ip].append(self._now_fn())

    def reset_all(self) -> None:
        self._hits.clear()


signup_throttle = SignupThrottle()


def client_ip(request: Request) -> str:
    """Quem está do outro lado, atrás do proxy.

    Em produção o app roda atrás do Caddy da VM, e `request.client.host` seria
    sempre o do proxy: um único IP para o mundo inteiro, e o limite barraria
    todo mundo depois do quinto cadastro do dia."""
    encaminhado = request.headers.get("X-Forwarded-For")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(
    payload: SignupRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """Cria a clínica e devolve a sessão, num ato.

    Devolve o MESMO token do login de propósito: uma segunda tela pedindo o
    e-mail e a senha que a pessoa acabou de digitar é onde ela desiste."""
    ip = client_ip(request)
    signup_throttle.check(ip)

    clinic, admin, membership, _ = await OnboardingService.create_clinic(
        session,
        spec=ClinicSpec(
            name=payload.clinic_name,
            admin_name=payload.admin_name,
            admin_email=payload.email,
            admin_password=payload.password,
            plan_code="trial",
            contact_name=payload.admin_name.strip(),
            contact_email=payload.email.strip().lower(),
            contact_phone=payload.phone,
        ),
        actor=SITE_ACTOR,
    )
    await session.commit()
    # Só conta depois de dar certo: um e-mail repetido não gasta a cota de
    # quem digitou errado e vai tentar de novo.
    signup_throttle.register(ip)

    token = create_jwt(
        {
            "kind": "personal",
            "sub": str(admin.id),
            "clinic_id": str(clinic.id),
            "membership_id": str(membership.id),
        },
        expires_in=timedelta(hours=12),
    )
    return TokenResponse(access_token=token)
```

Em `src/back/app/main.py`: acrescentar `from app.api.routes import signup as signup_routes` junto dos outros imports (ordem alfabética, entre `shifts` e `station_devices`) e `app.include_router(signup_routes.router)` logo antes de `platform_routes`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/back && uv run pytest tests/test_signup.py -v`
Expected: PASS (8 testes). O teste `test_cadastro_cria_clinica_e_ja_entra` verifica `read_only` em `/auth/me`, que só existe depois da Task 5 — se falhar **só nessa asserção**, siga para a Task 5 e volte. Todo o resto tem que passar agora.

- [ ] **Step 5: Commit**

```bash
git add src/back/app/api/routes/signup.py src/back/app/schemas/signup.py src/back/app/core/errors.py src/back/app/main.py src/back/tests/test_signup.py
git commit -m "feat(signup): porta publica de cadastro com teste de 14 dias

A unica rota que cria um tenant sem credencial. Devolve o mesmo token do
login: uma segunda tela pedindo o e-mail que a pessoa acabou de digitar e
onde ela desiste. Limite de cinco por hora por IP, contado so no sucesso."
```

---

### Task 5: O gate — a escrita para, a leitura continua

**Files:**
- Modify: `src/back/app/api/deps.py` (`require`, `require_any`, e `_ensure_writable` novo)
- Modify: `src/back/app/api/routes/auth.py` (`me` passa a devolver `read_only`)
- Modify: `src/back/app/schemas/auth.py` (`MeResponse.read_only`)
- Test: `src/back/tests/test_trial_expiry.py` (criar)

**Interfaces:**
- Consumes: `Clinic.is_read_only`, `READ_ONLY_CAPABILITIES`, `capabilities_of(role, read_only=)` (Task 1).
- Produces: `AppError("trial_expired", 403)` em toda mutação bloqueada; `MeResponse.read_only: bool`.

- [ ] **Step 1: Write the failing test**

Criar `src/back/tests/test_trial_expiry.py`:

```python
"""O dia 15: a escrita para, a leitura continua, e a alta sobrevive."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_owner,
    make_patient,
)
from tests.helpers import bearer, personal_token

ONTEM = datetime.now(UTC) - timedelta(days=1)
DAQUI_UMA_SEMANA = datetime.now(UTC) + timedelta(days=7)


async def _clinica_vencida(session):
    return await make_clinic(
        session, subscription_status="trial", trial_ends_at=ONTEM, plan_tier=None
    )


@pytest.mark.asyncio
async def test_prescrever_com_teste_vencido_e_recusado(client, session):
    clinic = await _clinica_vencida(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    patient = await make_patient(session, clinic=clinic)
    hosp = await make_hospitalization(
        session, clinic=clinic, patient=patient, membership=vet
    )
    await session.flush()

    resposta = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        headers=bearer(personal_token(vet)),
        json={
            "name": "Dipirona",
            "category": "medication",
            "kind": "recurring",
            "frequency_minutes": 480,
            "criticality": "normal",
        },
    )
    assert resposta.status_code == 403
    assert resposta.json()["error"]["code"] == "trial_expired"


@pytest.mark.asyncio
async def test_ler_a_ficha_com_teste_vencido_continua_funcionando(client, session):
    clinic = await _clinica_vencida(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await session.flush()

    resposta = await client.get(
        "/api/v1/board", headers=bearer(personal_token(vet))
    )
    assert resposta.status_code == 200


@pytest.mark.asyncio
async def test_dar_alta_com_teste_vencido_continua_funcionando(client, session):
    # A exceção que importa: ninguém fica com paciente internado dentro de um
    # sistema congelado.
    clinic = await _clinica_vencida(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    patient = await make_patient(session, clinic=clinic)
    hosp = await make_hospitalization(
        session, clinic=clinic, patient=patient, membership=vet
    )
    await session.flush()

    resposta = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        headers=bearer(personal_token(vet)),
        json={"outcome": "discharged", "note": "alta a pedido"},
    )
    assert resposta.status_code == 200, resposta.text


@pytest.mark.asyncio
async def test_teste_vigente_nao_bloqueia_nada(client, session):
    clinic = await make_clinic(
        session, subscription_status="trial", trial_ends_at=DAQUI_UMA_SEMANA
    )
    vet = await make_membership(session, clinic=clinic, role="vet")
    owner = await make_owner(session, clinic=clinic)
    await session.flush()

    resposta = await client.post(
        "/api/v1/patients",
        headers=bearer(personal_token(vet)),
        json={"name": "Thor", "species": "dog", "owner_id": str(owner.id)},
    )
    assert resposta.status_code == 201, resposta.text


@pytest.mark.asyncio
async def test_me_anuncia_o_estado_e_encolhe_as_capacidades(client, session):
    clinic = await _clinica_vencida(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await session.flush()

    corpo = (
        await client.get("/api/v1/auth/me", headers=bearer(personal_token(vet)))
    ).json()
    assert corpo["read_only"] is True
    assert "prescription.create" not in corpo["capabilities"]
    assert "record.read" in corpo["capabilities"]
    assert "hospitalization.discharge" in corpo["capabilities"]


@pytest.mark.asyncio
async def test_me_com_teste_vigente_traz_tudo(client, session):
    clinic = await make_clinic(
        session, subscription_status="trial", trial_ends_at=DAQUI_UMA_SEMANA
    )
    vet = await make_membership(session, clinic=clinic, role="vet")
    await session.flush()

    corpo = (
        await client.get("/api/v1/auth/me", headers=bearer(personal_token(vet)))
    ).json()
    assert corpo["read_only"] is False
    assert "prescription.create" in corpo["capabilities"]
```

**Antes de rodar:** as factories acima já foram conferidas contra
`src/back/tests/factories.py` (`make_hospitalization` recebe `clinic`, `patient` e
`membership` — nunca `kennel`; a espécie padrão é `"dog"`). O que **não** foi
conferido é o corpo que `POST /hospitalizations/{id}/prescriptions` e `/outcome`
esperam: copie o formato de `src/back/tests/test_prescriptions.py` e
`test_hospitalizations.py`. O objetivo aqui é o **403**, não redescobrir o
contrato — se o corpo estiver errado, você recebe 422 e o teste mente.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/back && uv run pytest tests/test_trial_expiry.py -v`
Expected: FAIL — as mutações devolvem 200/201 em vez de 403, e `/auth/me` não tem a chave `read_only`.

- [ ] **Step 3: Write minimal implementation**

Em `src/back/app/api/deps.py`, acrescentar aos imports `from app.permissions import READ_ONLY_CAPABILITIES, can, capabilities_of` e, antes de `require_read`:

```python
async def _ensure_writable(
    session: AsyncSession, clinic_id: uuid.UUID, *capabilities: str
) -> None:
    """A escrita para quando o teste vence. A leitura, não.

    Fica aqui, e não em cada rota, porque TODA mutação clínica passa por
    `require` ou `require_any` — as únicas escritas de fora são login, troca do
    próprio PIN e registro de token de push, e as três devem mesmo continuar
    funcionando com o teste vencido.

    O que sobrevive está em `READ_ONLY_CAPABILITIES` (permissions.py), a mesma
    lista que encolhe a resposta de `/auth/me`: uma fonte, dois usos."""
    if any(capability in READ_ONLY_CAPABILITIES for capability in capabilities):
        return
    clinic = await session.get(Clinic, clinic_id)
    if clinic is None or not clinic.is_read_only:
        return
    raise AppError(
        "trial_expired",
        403,
        capability=capabilities[0],
        trial_ends_at=clinic.trial_ends_at.isoformat() if clinic.trial_ends_at else None,
    )
```

E as duas fábricas de dependência passam a receber `auth` e `session`:

```python
def require_any(*capabilities: str) -> Any:
    """Basta UMA das capacidades. (docstring existente, mantida)"""

    async def dependency(
        actor: ActorInfo = Depends(get_operator),
        auth: AuthContext = Depends(get_current_auth),
        session: AsyncSession = Depends(get_session),
    ) -> ActorInfo:
        if not any(can(actor.role, capability) for capability in capabilities):
            raise AppError("forbidden", 403, capability=capabilities[0], role=actor.role)
        await _ensure_writable(session, auth.clinic_id, *capabilities)
        return actor

    return dependency


def require(capability: str) -> Any:
    """Exige a capacidade de QUEM AGE e devolve o ator. (docstring existente)"""

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
```

A ordem importa: `forbidden` (não pode nunca) antes de `trial_expired` (não pode agora). Um técnico tentando prescrever com o teste vencido tem que ouvir que não é dele o ato, não que a assinatura acabou.

Em `src/back/app/schemas/auth.py`, dentro de `MeResponse`, depois de `has_pin`:

```python
    #: O teste venceu: a escrita parou. NÃO autoriza nada — quem autoriza é
    #: `capabilities`, que já vem encolhida. Existe para a interface conseguir
    #: EXPLICAR por que os botões sumiram, em vez de parecer quebrada.
    read_only: bool = False
```

Em `src/back/app/api/routes/auth.py`, `me` precisa da sessão:

```python
@router.get("/me")
async def me(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    clinic = await session.get(Clinic, auth.clinic_id)
    read_only = clinic is not None and clinic.is_read_only
    role = auth.membership.role if auth.membership else None
    return MeResponse(
        kind=auth.kind,
        clinic_id=auth.clinic_id,
        membership_id=auth.membership.id if auth.membership else None,
        role=role,
        # A estação não tem papel próprio: quem age é o dono do PIN, e a
        # capacidade é conferida no ato. Aqui vai vazio de propósito.
        capabilities=sorted(capabilities_of(role, read_only=read_only)),
        has_pin=auth.membership is not None and auth.membership.pin_hash is not None,
        read_only=read_only,
    )
```

Acrescentar `capabilities_of` ao import de `app.permissions` em `auth.py` (já importa `capabilities_of`; confira).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/back && uv run pytest tests/test_trial_expiry.py -v`
Expected: PASS (6 testes)

Depois, a suíte inteira — este é o passo que pode quebrar qualquer rota:

Run: `cd src/back && uv run pytest -q`
Expected: tudo verde. Se algum teste antigo quebrar com 403 `trial_expired`, é uma factory criando clínica com `trial_ends_at` no passado; conserte o teste, não o gate.

- [ ] **Step 5: Commit**

```bash
git add src/back/app/api/deps.py src/back/app/api/routes/auth.py src/back/app/schemas/auth.py src/back/tests/test_trial_expiry.py
git commit -m "feat(billing): teste vencido vira somente-leitura

O gate mora em require/require_any, por onde toda mutacao clinica ja
passa: nenhuma rota muda. /auth/me devolve a lista de capacidades ja
encolhida, entao o front esconde o resto sem saber que existe assinatura.

A alta continua liberada de proposito."
```

---

### Task 6: A landing com o formulário

**Files:**
- Create: `src/front/src/pages/Signup.tsx`
- Create: `src/front/src/styles/signup.css`
- Create: `src/front/src/i18n/extra/signup.pt-BR.json`
- Create: `src/front/src/i18n/extra/signup.en.json`
- Modify: `src/front/src/api/client.ts` (função `signup`)
- Modify: `src/front/src/hooks/useSession.tsx` (`signupClinic`)
- Modify: `src/front/src/App.tsx:39-46` (rotas públicas)
- Modify: `src/front/src/pages/Login.tsx` (link "Criar conta grátis")
- Modify: `src/front/src/i18n/pt-BR.json` e `en.json` (erros novos)

**Interfaces:**
- Consumes: `POST /api/v1/signup` → `{ access_token, token_type }`.
- Produces: `useSession().signupClinic(payload) => Promise<void>` (salva a sessão, igual a `loginPersonal`).

- [ ] **Step 1: Ler os padrões antes de escrever**

Não há suíte de front; a verificação é visual e manual. Antes de escrever, ler:

```bash
sed -n '1,80p' src/front/src/api/client.ts        # como loginPersonal chama e salva
grep -n "loginPersonal" -A 20 src/front/src/hooks/useSession.tsx
sed -n '1,60p' src/front/src/i18n/extra/team.pt-BR.json   # formato do catálogo
grep -n "login-layout" -A 30 src/front/src/styles/app.css # o layout irmão
```

- [ ] **Step 2: Erros novos no catálogo pt-BR e en**

Em `src/front/src/i18n/pt-BR.json`, junto dos outros `error.*`:

```json
  "error.email_taken": "Esse e-mail já tem conta. Entre por aqui.",
  "error.signup_rate_limited": "Muitos cadastros deste computador. Tente daqui a pouco.",
  "error.trial_expired": "Seu teste terminou. Dá para ler tudo e dar alta; para voltar a prescrever, fale com a gente.",
```

E o equivalente em `en.json`:

```json
  "error.email_taken": "That email already has an account. Sign in instead.",
  "error.signup_rate_limited": "Too many sign-ups from this computer. Try again shortly.",
  "error.trial_expired": "Your trial ended. You can still read everything and discharge patients; to prescribe again, talk to us.",
```

- [ ] **Step 3: O catálogo da landing**

Criar `src/front/src/i18n/extra/signup.pt-BR.json`. Chaves planas (`keySeparator: false`), tom do `_TOKENS.md`: português direto, sem jargão de software, falando como a clínica fala.

```json
{
  "signup.brand": "PlantãoVet",
  "signup.hero.title": "A ficha de internação que faz o plantão passar direito",
  "signup.hero.subtitle": "Prescrição, aprazamento e passagem de plantão num lugar só. Teste 14 dias, sem cartão.",
  "signup.hero.trustline": "Sem cartão · Sem instalação · Cancele quando quiser",

  "signup.form.title": "Criar conta grátis",
  "signup.form.clinicName": "Nome da clínica",
  "signup.form.adminName": "Seu nome",
  "signup.form.email": "E-mail",
  "signup.form.password": "Senha",
  "signup.form.passwordHint": "Ao menos 8 caracteres.",
  "signup.form.phone": "WhatsApp (opcional)",
  "signup.form.submit": "Começar os 14 dias",
  "signup.form.busy": "Criando sua clínica…",
  "signup.form.hasAccount": "Já tem conta?",
  "signup.form.signIn": "Entrar",

  "signup.problem.title": "O plantão vira pelo WhatsApp e pela memória",
  "signup.problem.body": "A prancheta fica presa no box, a dose atrasada só aparece quando alguém olha, e quem entra às 19h descobre o que aconteceu perguntando. O que se perde na troca de turno é o que mais custa caro.",

  "signup.promises.title": "O que muda",
  "signup.promise.onTime.title": "A dose sai na hora",
  "signup.promise.onTime.body": "A prescrição vira horários concretos pelos horários-âncora da sua clínica. q8h vira 10h, 18h e 02h, e a grade mostra o que venceu.",
  "signup.promise.overdue.title": "O atraso aparece sozinho",
  "signup.promise.overdue.body": "Cada tarefa tem janela de tolerância por criticidade. Passou, sobe no painel — ninguém precisa lembrar de olhar.",
  "signup.promise.handover.title": "A passagem fica registrada",
  "signup.promise.handover.body": "O boletim do turno sai pronto: feitas, não feitas, pendentes e o que mudou de prescrição. Quem recebe aceita paciente a paciente.",

  "signup.how.title": "Como começa",
  "signup.how.step1.title": "Você cria a conta",
  "signup.how.step1.body": "Quatro campos. A clínica existe no minuto seguinte, com os horários-âncora e as janelas de tolerância já configurados.",
  "signup.how.step2.title": "Cadastra a equipe e os boxes",
  "signup.how.step2.body": "Cada pessoa entra com o papel dela e um PIN. No tablet do corredor, o PIN é quem assina cada ato.",
  "signup.how.step3.title": "Interna o primeiro paciente",
  "signup.how.step3.body": "Prescreve, e a grade de tarefas nasce sozinha. É a partir daí que a passagem de plantão muda.",

  "signup.faq.title": "Perguntas que todo mundo faz",
  "signup.faq.card.q": "Precisa de cartão para testar?",
  "signup.faq.card.a": "Não. Você cria a conta e usa 14 dias. Não pedimos cartão em momento nenhum do teste.",
  "signup.faq.after.q": "O que acontece no dia 15?",
  "signup.faq.after.a": "A clínica passa a ser somente-leitura: você continua vendo tudo — ficha, prontuário, conta, trilha de auditoria — e continua podendo dar alta e exportar o prontuário dos seus pacientes. O que para é a escrita nova: prescrever, dar baixa em tarefa, internar. Nada é apagado.",
  "signup.faq.data.q": "Meus dados ficam comigo?",
  "signup.faq.data.a": "Ficam. O prontuário sai em PDF a qualquer momento, inclusive depois do teste, como o CFMV exige que a clínica consiga entregar ao tutor.",
  "signup.faq.size.q": "Serve para uma clínica pequena?",
  "signup.faq.size.a": "O teste vem com 10 leitos, que é um paciente internado por vez em cada um. O limite avisa, nunca bloqueia uma internação.",

  "signup.footer.talk": "Falar com a gente no WhatsApp",
  "signup.footer.signIn": "Já sou cliente"
}
```

Criar `src/front/src/i18n/extra/signup.en.json` com as **mesmas chaves**, traduzidas. Chave faltando cai no literal na tela.

- [ ] **Step 4: O cliente da API e o hook**

Em `src/front/src/api/client.ts`, junto de `login` (linha ~180), no mesmo formato:

```ts
export interface SignupPayload {
  clinic_name: string;
  admin_name: string;
  email: string;
  password: string;
  phone?: string;
}
```

e, dentro do objeto `api`, logo depois de `login`:

```ts
  /** A porta pública: cria a clínica e já devolve a sessão. */
  signup: (payload: SignupPayload) =>
    request<TokenResponse>("/api/v1/signup", { method: "POST", body: payload }),
```

Em `src/front/src/hooks/useSession.tsx`, na interface `SessionContextValue`, depois de `loginPersonal`:

```tsx
  /** Cria a clínica e já entra. Pedir login logo depois de criar a conta é
   *  onde a pessoa desiste. */
  signupClinic: (payload: SignupPayload) => Promise<void>;
```

e no objeto do provider, ao lado de `loginPersonal` (linha ~167), na mesma forma:

```tsx
      signupClinic: async (payload) => {
        const token = await api.signup(payload);
        persist({ kind: "personal", accessToken: token.access_token });
      },
```

Importar `SignupPayload` do `../api/client` no hook.

- [ ] **Step 5: As rotas públicas**

Em `src/front/src/App.tsx`, trocar `if (!session) return <Login />;` por:

```tsx
  // Sem sessão, a raiz é a landing: é o link que se divulga, e quem chega por
  // ele nunca ouviu falar do produto. Quem já é cliente vai direto para
  // /entrar — e quem tem sessão nunca vê nenhuma das duas, porque o teste de
  // sessão continua vindo antes.
  if (!session)
    return (
      <Routes>
        <Route path="/" element={<Signup />} />
        <Route path="/cadastro" element={<Signup />} />
        <Route path="*" element={<Login />} />
      </Routes>
    );
```

Importar `Signup` no topo. Em `Login.tsx`, acrescentar abaixo do botão de entrar:

```tsx
<p style={{ margin: 0, fontSize: 14, color: "var(--ink-3)", textAlign: "center" }}>
  {t("login.noAccount")} <Link to="/">{t("login.createAccount")}</Link>
</p>
```

com `"login.noAccount": "Ainda não tem conta?"` e `"login.createAccount": "Criar conta grátis"` nos dois catálogos base, e `Link` importado de `react-router-dom`.

- [ ] **Step 6: A página**

Criar `src/front/src/pages/Signup.tsx`. O formulário — a única parte com lógica — vai inteiro abaixo; as seções de conteúdo são layout sobre o catálogo do Step 3.

```tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { Button, ErrorBanner, Field, inputStyle, useApiErrorMessage } from "../components/ui";
import { useSession } from "../hooks/useSession";
import "../styles/signup.css";

export function Signup() {
  const { t } = useTranslation();
  const { signupClinic } = useSession();
  const describeError = useApiErrorMessage();

  const [clinicName, setClinicName] = useState("");
  const [adminName, setAdminName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");
  const [error, setError] = useState<string | null>(null);
  // O e-mail repetido é o erro mais provável e o único com saída óbvia: em vez
  // de só a mensagem, a pessoa ganha o caminho para entrar.
  const [emailTaken, setEmailTaken] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setEmailTaken(false);
    setBusy(true);
    try {
      await signupClinic({
        clinic_name: clinicName.trim(),
        admin_name: adminName.trim(),
        email: email.trim(),
        password,
        phone: phone.trim() || undefined,
      });
      // Nada a fazer no sucesso: a sessão foi salva, o App re-renderiza e o
      // RoleHome manda o administrador para /internados.
    } catch (err) {
      if (err instanceof ApiError && err.code === "email_taken") setEmailTaken(true);
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="signup-form" noValidate>
      <h2 className="signup-form-title">{t("signup.form.title")}</h2>

      <ErrorBanner message={error} />
      {emailTaken ? (
        <p style={{ margin: 0, fontSize: 14 }}>
          <Link to="/entrar">{t("signup.form.signIn")}</Link>
        </p>
      ) : null}

      <Field label={t("signup.form.clinicName")}>
        <input
          style={inputStyle}
          autoComplete="organization"
          value={clinicName}
          onChange={(e) => setClinicName(e.target.value)}
          required
          minLength={2}
        />
      </Field>
      <Field label={t("signup.form.adminName")}>
        <input
          style={inputStyle}
          autoComplete="name"
          value={adminName}
          onChange={(e) => setAdminName(e.target.value)}
          required
          minLength={2}
        />
      </Field>
      <Field label={t("signup.form.email")}>
        <input
          style={inputStyle}
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </Field>
      <Field label={t("signup.form.password")} hint={t("signup.form.passwordHint")}>
        <input
          style={inputStyle}
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={8}
        />
      </Field>
      <Field label={t("signup.form.phone")}>
        <input
          style={inputStyle}
          type="tel"
          autoComplete="tel"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
      </Field>

      <Button type="submit" disabled={busy}>
        {busy ? t("signup.form.busy") : t("signup.form.submit")}
      </Button>

      <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)", textAlign: "center" }}>
        {t("signup.form.hasAccount")} <Link to="/entrar">{t("signup.form.signIn")}</Link>
      </p>
    </form>
  );
}
```

**Conferir antes de colar:** `Field` aceita `hint`? `Button` aceita `type` e `disabled`? `ApiError` expõe `.code`? Ver `src/front/src/components/ui.tsx` e `src/front/src/api/client.ts`. Se `Field` não tiver `hint`, ponha a dica num `<p>` abaixo do input em vez de inventar a prop.

O resto da página envolve esse formulário, nesta ordem, com as chaves do Step 3:

| Seção | Conteúdo | Forma |
|---|---|---|
| Hero | `signup.hero.*` + o formulário | Duas colunas ≥900px (pitch à esquerda, card do formulário à direita, **acima da dobra**); empilhado abaixo disso, **formulário primeiro** |
| Problema | `signup.problem.*` | Um parágrafo largo, `max-width: 60ch` |
| Promessas | `signup.promise.onTime/overdue/handover.*` | Três cards, ícone de check SVG reusado do `Login.tsx` (traço 2px, `stroke-linecap="round"`) |
| Como começa | `signup.how.step1/2/3.*` | Três passos numerados, `font-variant-numeric: tabular-nums` |
| FAQ | `signup.faq.*` | `<details>`/`<summary>` nativos, sem JS |
| Rodapé | `signup.footer.*` | Link do WhatsApp + link para `/entrar` |

Regras do `design/telas/_TOKENS.md` que valem aqui: tokens do `index.css` (`var(--primary)`, `var(--ink)`, `var(--line)`…), display em Bricolage Grotesque 700/800, texto em Instrument Sans, cards `border: 1px solid var(--line); border-radius: 12px`, botão primário `background: var(--primary)`, **nunca emoji** — ícones em SVG inline.

WhatsApp do rodapé:
`https://wa.me/5561983031823?text=Ol%C3%A1%21%20Quero%20saber%20mais%20sobre%20o%20Plant%C3%A3oVet`

`signup.css` fica só com grid e media queries; o resto pode ser inline, como as outras páginas fazem.

- [ ] **Step 7: Rodar e olhar**

```bash
docker compose up -d postgres
cd src/back && uv run alembic upgrade head && uv run uvicorn app.main:app --reload &
cd src/front && npm run dev
```

Conferir, em `http://localhost:5173`:

1. `/` mostra a landing; `/entrar` mostra o login; o link de cada uma leva à outra.
2. Cadastrar uma clínica de verdade → cai em `/internados`, logado, com o banner do teste.
3. Repetir o mesmo e-mail → mensagem em português com o link para entrar.
4. Estreitar para 390px: o formulário vem primeiro e nada estoura na horizontal.
5. `npm run lint` limpo.

- [ ] **Step 8: Commit**

```bash
git add src/front/src/pages/Signup.tsx src/front/src/styles/signup.css src/front/src/i18n src/front/src/App.tsx src/front/src/api/client.ts src/front/src/hooks/useSession.tsx src/front/src/pages/Login.tsx
git commit -m "feat(signup): landing publica com o formulario na mesma tela

Quem chega pelo link nunca ouviu falar do produto: o pitch e o formulario
dividem a primeira tela. O FAQ responde o dia 15 na cara limpa — dizer o
que acontece converte melhor do que esconder."
```

---

### Task 7: O banner do teste vencido

**Files:**
- Modify: `src/front/src/App.tsx:276-290` (`SubscriptionBanner`)
- Modify: `src/front/src/styles/app.css` (variante vencida)
- Modify: `src/front/src/i18n/pt-BR.json` e `en.json`

**Interfaces:**
- Consumes: `useSession().me.read_only` (Task 5).
- Produces: nada.

- [ ] **Step 1: As mensagens**

Em `src/front/src/i18n/pt-BR.json`, junto de `subscription.trialEnding` e `subscription.pastDue`:

```json
  "subscription.expired": "Seu teste terminou. Você continua vendo tudo e pode dar alta; para voltar a prescrever, fale com a gente.",
  "subscription.expiredCta": "Falar no WhatsApp",
```

E em `en.json`:

```json
  "subscription.expired": "Your trial ended. You can still see everything and discharge patients; to prescribe again, talk to us.",
  "subscription.expiredCta": "Message us on WhatsApp",
```

- [ ] **Step 2: O banner**

Substituir `SubscriptionBanner` em `src/front/src/App.tsx`:

```tsx
function SubscriptionBanner() {
  const { t } = useTranslation();
  const { can, me } = useSession();
  const { profile } = useClinic();

  // O vencido vem ANTES da guarda de capacidade, e para todo mundo: quem
  // acabou de perder o botão de dar baixa precisa saber por quê, e
  // `clinic.configure` é escrita — some da lista justamente agora, o que
  // esconderia este aviso de quem mais precisa dele.
  if (me?.read_only) {
    return (
      <div className="subscription-banner subscription-banner-expired" role="status">
        {t("subscription.expired")}{" "}
        <a
          href="https://wa.me/5561983031823?text=Ol%C3%A1%21%20Meu%20teste%20do%20Plant%C3%A3oVet%20terminou"
          target="_blank"
          rel="noreferrer"
        >
          {t("subscription.expiredCta")}
        </a>
      </div>
    );
  }

  if (!profile || !can(CAN.clinicConfigure)) return null;
  if (profile.subscription_status === "past_due") {
    return <div className="subscription-banner subscription-banner-late">{t("subscription.pastDue")}</div>;
  }
  if (profile.subscription_status === "trial" && profile.trial_ends_at) {
    const days = Math.max(0, Math.ceil((Date.parse(profile.trial_ends_at) - Date.now()) / 86_400_000));
    if (days > 14) return null;
    return <div className="subscription-banner">{t("subscription.trialEnding", { count: days })}</div>;
  }
  return null;
}
```

Conferir que `me` é exportado por `useSession` (é — está na interface `SessionContextValue`) e que `Me` em `src/front/src/api/types.ts` ganhou `read_only: boolean`. Se não, acrescente.

- [ ] **Step 3: O estilo**

Em `src/front/src/styles/app.css`, ao lado de `.subscription-banner-late`:

```css
/* Vencido não é "atrasado": o atraso avisa, isto explica uma mudança que já
   aconteceu na tela. Mesma família de cor do atraso, com o link em destaque. */
.subscription-banner-expired {
  background: var(--late-bg);
  border-bottom: 1px solid var(--late-edge);
  color: var(--late);
  font-weight: 600;
}
.subscription-banner-expired a {
  color: var(--late);
  font-weight: 700;
}
```

- [ ] **Step 4: Verificar na tela**

Com a app rodando, vencer o teste da clínica de demonstração à mão:

```bash
docker compose exec postgres psql -U plantaovet -d plantaovet -c \
  "UPDATE clinics SET subscription_status='trial', trial_ends_at=now() - interval '1 day' WHERE slug='demo';"
```

Entrar como `paula@demo.vet` / `senha-123` e conferir:

1. O banner vermelho aparece, com o link do WhatsApp.
2. Os botões de prescrever e de dar baixa **sumiram** (não estão lá e devolvendo erro — sumiram).
3. A ficha, o prontuário e a trilha abrem normalmente.
4. Dar alta ainda funciona.
5. Sair e entrar de novo funciona (`_ensure_open` só barra `suspended`/`cancelled`).

Devolver a demo ao normal:

```bash
docker compose exec postgres psql -U plantaovet -d plantaovet -c \
  "UPDATE clinics SET trial_ends_at=now() + interval '14 days' WHERE slug='demo';"
```

- [ ] **Step 5: Commit**

```bash
git add src/front/src/App.tsx src/front/src/styles/app.css src/front/src/i18n src/front/src/api/types.ts
git commit -m "feat(billing): banner do teste vencido, visivel para todo mundo

O aviso vem antes da guarda de clinic.configure de proposito: essa
capacidade e escrita e some da lista quando o teste vence, o que
esconderia o aviso de quem acabou de perder os botoes."
```

---

## Verificação final

- [ ] `cd src/back && uv run pytest -q` — suíte inteira verde
- [ ] `cd src/back && uv run ruff check . && uv run ruff format --check .`
- [ ] `cd src/front && npm run lint && npx tsc --noEmit`
- [ ] `docker compose down -v && docker compose up -d --build` — sobe do zero, migrações rodam, `/` mostra a landing em `http://localhost:8080`
- [ ] Cadastro de ponta a ponta num banco limpo: criar conta → internar um paciente → prescrever → dar baixa
