# PlantãoVet Semana 1 — Fundação · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar a API núcleo do PlantãoVet — tenancy, i18n nativo, dois modos de identidade, pacientes/internações, prescrição → aprazamento → tarefas com execução atômica, board e trilha de auditoria encadeada — pronta para a UI da semana 2 consumir.

**Architecture:** Monolito async FastAPI multi-tenant (Postgres único, `clinic_id` por linha) com lógica de negócio em classes de serviço (`SchedulingService` puro e testável sem I/O, `TaskService`, `AuditService`). Um único worker APScheduler mantém uma janela rolante de 48h de tarefas aprazadas; "atrasada" nunca é persistida — é computada na leitura, de modo que board e ficha jamais divergem. Toda mutação clínica grava snapshots before/after numa trilha append-only imposta por trigger e encadeada por hash. A API é internacionalizada por construção: identificadores e enums em inglês, erros como códigos estáveis (nunca prosa), armazenamento canônico (UTC, SI, ISO 4217, E.164) e catálogos `pt-BR`/`en` com paridade garantida por teste.

**Tech Stack:** Python 3.13 · FastAPI · SQLAlchemy 2.0 async + asyncpg · Alembic · pydantic v2 · APScheduler · pytest/pytest-asyncio/httpx · uv · ruff · Postgres 16 (docker compose local).

**Spec:** `docs/2026-08-31-spec-plantaovet-v1.md` (produto, domínio, i18n §3, schema §4, API §5) · contratos exatos em `docs/superpowers/plans/_drafts/_drafting-brief-semana1.md` · glossário bilíngue em `CONTEXT.md` · decisões em `docs/adr/` (0001 stack, 0002 app companion, 0003 auditoria append-only, 0004 i18n nativo) · pesquisa de mercado em `docs/2026-08-31-pesquisa-internacao-veterinaria.md`.

## Global Constraints

- Python **3.13**; dependências via `uv`; lint `ruff` (line-length 100, target py313); testes `pytest` com `asyncio_mode = "auto"`.
- **Todo identificador de código, nome de tabela, rota e valor de enum em inglês** (ADR-0004). O português vive nos catálogos de tradução e na UI.
- Lógica de negócio **em classes de serviço**, nunca funções soltas de negócio.
- Frequência de prescrição em **minutos** (`frequency_minutes`). Âncoras da clínica chaveadas por minutos: `{"1440": ["10:00"], "720": ["10:00","22:00"], "480": ["10:00","18:00","02:00"], "360": ["10:00","16:00","22:00","04:00"]}`.
- Timestamps `timestamptz` em UTC no banco; aprazamento, "dia" e "janela" no timezone da clínica (`clinics.timezone`, default `America/Sao_Paulo`) via `zoneinfo`.
- Janelas de tolerância (ISMP): `critical` → 30 min · `normal` → 60 min · `normal` com `frequency_minutes >= 1440` → 120 min.
- **Erros são códigos, nunca prosa**: `{"error": {"code": "<snake_case>", "params": {...}}}` via `AppError`. Um teste valida que todo código casa `^[a-z][a-z0-9_]*$`.
- **Armazenamento canônico**: UTC, unidades SI (kg, °C), dinheiro em unidade menor (`price_minor`) + `clinics.currency` (ISO 4217), telefone em E.164, enums como códigos neutros. Formatação é do cliente.
- **Tenancy em FK de body**: toda FK recebida no body é carregada com filtro `clinic_id` e responde **404 `not_found`** se for de outro tenant (nunca 403). Reforço no banco: `UNIQUE (id, clinic_id)` nos pais + FK composta nos filhos.
- **Sem DELETE** em nenhuma tabela de domínio: desativação é `is_active` (kennels, patients, owners) ou `status` (hospitalizations, tasks).
- **Transição de estado de tarefa é atômica**: `UPDATE ... WHERE status='pending' ... RETURNING`; zero linhas → `409 task_already_processed`. Toda task que muda estado de tarefa tem teste de corrida.
- **Auditoria**: `AuditService.record` com `before`/`after`; `REDACTED = {"phone_e164", "tax_id", "password_hash", "pin_hash", "station_key_hash"}` nunca entra no payload (LGPD — a trilha é inapagável); `entry_hash` encadeia com `prev_hash` da última entrada **da mesma clínica**.
- **Compliance é perfil por país**: nenhuma regra do CFMV fora de `app/compliance/br.py`.
- **Teste de isolamento de tenant obrigatório em cada router**: leitura cruzada → 404 **e** injeção de FK cruzada no body → 404.
- Commits: conventional commits; **nunca** incluir linha `Co-Authored-By`.

## File Structure

```
backend/
  pyproject.toml              # uv, deps, ruff + pytest config
  alembic.ini · alembic/      # migrações (env.py async lendo settings.database_url)
  app/
    main.py                   # create_app(): routers + handler de AppError
    core/config.py            # Settings (DATABASE_URL, JWT_SECRET, ENV)
    core/db.py                # engine async, async_session_factory, Base
    core/security.py          # bcrypt hash/verify · JWT HS256 create/decode
    core/errors.py            # AppError + app_error_handler
    i18n/catalog.py           # translate(key, locale, **params) · catalog_keys(locale)
    i18n/pt-BR.json · en.json # catálogos com paridade garantida por teste
    compliance/__init__.py    # get_profile(name) -> ComplianceProfile
    compliance/br.py          # perfil brasileiro (CFMV)
    models/                   # clinic, user, membership, kennel, owner, patient,
                              #   hospitalization, prescription, task, audit
    schemas/                  # auth, kennel, owner, patient, hospitalization,
                              #   prescription, task, board, pagination
    services/                 # audit, scheduling, tasks, hospitalization
    api/deps.py               # get_session, get_current_auth, get_operator, get_tenant_obj
    api/routes/               # auth, kennels, owners, patients, hospitalizations,
                              #   prescriptions, tasks, board, audit
    workers/scheduler.py      # AsyncIOScheduler + job único de aprazamento (48h)
  scripts/seed_demo.py        # clínica demo completa para o piloto e a demo de venda
  tests/                      # conftest.py (harness), factories.py, test_*.py
docker-compose.yml            # postgres:16
```

Cada arquivo tem uma responsabilidade: models só mapeiam tabelas; schemas só validam entrada/saída; services carregam a regra clínica (e são testáveis sem HTTP); routes só orquestram (auth → serviço → auditoria → resposta).

## Ordem das tarefas

As 16 tarefas são sequenciais — cada uma termina com testes verdes e um commit. Blocos:

| Tarefas | Bloco |
|---|---|
| 1–4 | Fundação técnica: scaffold + erros como códigos + i18n, banco/migrações/harness, identidade e tenant + compliance profile, auditoria encadeada |
| 5–8 | Acesso e cadastro: auth pessoal, modo estação endurecido, owners/patients/kennels, hospitalizations |
| 9–12 | Coração clínico: prescriptions, aprazamento puro, persistência idempotente + worker, suspensão e titulação |
| 13–16 | Operação: fila por janela, execução de tarefas, board e auditoria, seeds + smoke E2E |

**Checkpoint externo (durante a semana, sem bloquear):** fotos das fichas de papel reais das clínicas parceiras + 30 min com um veterinário para confirmar quatro decisões clínicas — `category` da prescrição, `first_dose_now`, titulação de fluidoterapia por versionamento e as cerimônias default do dia (spec §8, risco 1). Migrações seguem normalmente; o que a conversa mudar entra como migração adicional.

---
### Task 1: Scaffold, erros como códigos e i18n

**Files:**
- Create: `src/back/pyproject.toml`
- Create: `src/back/.gitignore`
- Create: `src/back/app/__init__.py`
- Create: `src/back/app/main.py`
- Create: `src/back/app/core/__init__.py`
- Create: `src/back/app/core/config.py`
- Create: `src/back/app/core/errors.py`
- Create: `src/back/app/i18n/__init__.py`
- Create: `src/back/app/i18n/catalog.py`
- Create: `src/back/app/i18n/pt-BR.json`
- Create: `src/back/app/i18n/en.json`
- Create: `docker-compose.yml`
- Create: `src/back/tests/__init__.py`
- Test: `src/back/tests/test_health.py`
- Test: `src/back/tests/test_config.py`
- Test: `src/back/tests/test_errors.py`
- Test: `src/back/tests/test_i18n.py`

**Interfaces:**
- Consumes: nada (primeira task).
- Produces:
  - `app.main.create_app() -> FastAPI` — registra o handler de `AppError` e o de `RequestValidationError`; expõe `GET /health`.
  - `app.core.config.Settings` (pydantic-settings) com `database_url: str`, `jwt_secret: str`, `env: str`; instância global `settings`.
  - `app.core.errors.AppError(code: str, status_code: int = 400, **params: Any)` com atributos `.code`, `.status_code`, `.params`; `app_error_handler` devolve `JSONResponse(status_code, {"error": {"code": code, "params": params}})`; `validation_error_handler` devolve o mesmo envelope com code `validation_error` e status 422; `ERROR_CODES: frozenset[str]` com os 15 códigos da v1.
  - `app.i18n.catalog.translate(key: str, locale: str, **params: Any) -> str` (fallback pt-BR para locale desconhecido; `KeyError` para chave ausente) e `catalog_keys(locale: str) -> set[str]`.
  - Chaves de catálogo disponíveis (pt-BR e en): `task.check`, `ceremony.owner_contact`, `ceremony.daily_progress_note`, `compliance.br.license_authority_label`.
  - Serviço `postgres` (postgres:16) em `localhost:5432`, user/senha/db `plantaovet`.

> Regra transversal 1 do brief nasce aqui: **erros são códigos, nunca prosa** — toda resposta de erro passa por `AppError` + handler.

- [ ] **Step 1: Criar o esqueleto do backend com uv**

Na raiz do repositório:

```bash
mkdir -p backend
cd src/back
uv init --bare --python 3.13
uv python pin 3.13
mkdir -p app/core app/i18n tests
touch app/__init__.py app/core/__init__.py app/i18n/__init__.py tests/__init__.py
```

Substitua o conteúdo de `src/back/pyproject.toml` por:

```toml
[project]
name = "plantaovet-backend"
version = "0.1.0"
description = "PlantaoVet API"
requires-python = ">=3.13"
dependencies = [
    "fastapi",
    "uvicorn",
    "sqlalchemy[asyncio]",
    "asyncpg",
    "alembic",
    "pydantic-settings",
    "pyjwt",
    "bcrypt",
    "apscheduler",
]

[dependency-groups]
dev = [
    "pytest",
    "pytest-asyncio",
    "httpx",
    "ruff",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]

[tool.ruff.lint.per-file-ignores]
# conftest.py precisa fixar DATABASE_URL no ambiente ANTES de importar módulos do app
"tests/conftest.py" = ["E402"]
```

Crie `src/back/.gitignore`:

```gitignore
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
*.egg-info/
.env
```

Rode:

```bash
uv sync
```

Expected: cria `.venv`, resolve e instala as dependências e o pacote `plantaovet-backend` em modo editável, gera `uv.lock`. Exit 0.

- [ ] **Step 2: Subir o Postgres com docker compose**

Crie `docker-compose.yml` na **raiz do repositório** (mesmo nível de `src/back/`):

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: plantaovet
      POSTGRES_PASSWORD: plantaovet
      POSTGRES_DB: plantaovet
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

Run (na raiz do repositório):

```bash
docker compose up -d postgres && sleep 3 && docker compose exec postgres pg_isready -U plantaovet
```

Expected: `/var/run/postgresql:5432 - accepting connections`

- [ ] **Step 3: Escrever os testes falhando de health e config**

Crie `src/back/tests/test_health.py`:

```python
import httpx

from app.main import create_app


async def test_health_returns_ok():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

Crie `src/back/tests/test_config.py`:

```python
from app.core.config import Settings


def test_settings_have_dev_defaults():
    settings = Settings(_env_file=None)
    assert settings.env == "dev"
    assert settings.jwt_secret == "dev-secret-change-me"
    assert settings.database_url.startswith("postgresql+asyncpg://")
```

- [ ] **Step 4: Rodar e ver falhar**

Run: `cd src/back && uv run pytest tests/test_health.py tests/test_config.py -v`
Expected: erros de coleta — `ModuleNotFoundError: No module named 'app.main'` e `No module named 'app.core.config'`

- [ ] **Step 5: Implementar config e main mínimos**

Crie `src/back/app/core/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://plantaovet:plantaovet@localhost:5432/plantaovet"
    jwt_secret: str = "dev-secret-change-me"
    env: str = "dev"


settings = Settings()
```

Crie `src/back/app/main.py` (ainda sem os handlers de erro — entram no ciclo seguinte):

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="PlantaoVet API")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 6: Rodar e ver passar**

Run: `cd src/back && uv run pytest tests/test_health.py tests/test_config.py -v`
Expected: `2 passed`

- [ ] **Step 7: Escrever os testes falhando de erros como códigos**

Crie `src/back/tests/test_errors.py`:

```python
import re

import httpx
from pydantic import BaseModel

from app.core.errors import ERROR_CODES, AppError
from app.main import create_app

SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


def test_app_error_carries_code_status_and_params():
    error = AppError("pin_duplicate", 409, membership_id="abc")
    assert error.code == "pin_duplicate"
    assert error.status_code == 409
    assert error.params == {"membership_id": "abc"}


def test_app_error_defaults_to_status_400():
    error = AppError("forbidden")
    assert error.status_code == 400
    assert error.params == {}


def test_every_known_error_code_is_snake_case():
    assert ERROR_CODES
    for code in ERROR_CODES:
        assert SNAKE_CASE.fullmatch(code), code


async def test_app_error_becomes_error_envelope():
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise AppError("task_already_processed", 409, task_id="t1")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "task_already_processed", "params": {"task_id": "t1"}}
    }


async def test_no_error_response_contains_prose():
    # Dispara todos os erros conhecidos e valida que o envelope só tem
    # code (snake_case) + params — nunca "detail" com prosa.
    app = create_app()

    @app.get("/raise/{code}")
    async def raise_code(code: str) -> None:
        raise AppError(code, 400)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for code in sorted(ERROR_CODES):
            response = await client.get(f"/raise/{code}")
            body = response.json()
            assert set(body) == {"error"}
            assert set(body["error"]) == {"code", "params"}
            assert SNAKE_CASE.fullmatch(body["error"]["code"])


async def test_request_validation_becomes_validation_error_envelope():
    app = create_app()

    class Payload(BaseModel):
        name: str

    @app.post("/echo")
    async def echo(payload: Payload) -> Payload:
        return payload

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/echo", json={})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "validation_error"
    assert "detail" not in body
    assert all(set(field) == {"loc", "type"} for field in body["error"]["params"]["fields"])
```

- [ ] **Step 8: Rodar e ver falhar**

Run: `cd src/back && uv run pytest tests/test_errors.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.core.errors'`

- [ ] **Step 9: Implementar AppError, handlers e registrá-los em create_app**

Crie `src/back/app/core/errors.py`:

```python
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Códigos de erro da v1 (brief, regra transversal 1). Todo código novo entra aqui.
ERROR_CODES: frozenset[str] = frozenset(
    {
        "invalid_credentials",
        "token_expired",
        "operator_required",
        "pin_locked_out",
        "pin_duplicate",
        "station_key_rotated",
        "task_already_processed",
        "early_confirmation_required",
        "prn_guardrail",
        "consent_reason_required",
        "outcome_note_required",
        "pending_tasks_confirmation_required",
        "not_found",
        "forbidden",
        "validation_error",
    }
)


class AppError(Exception):
    def __init__(self, code: str, status_code: int = 400, **params: Any) -> None:
        self.code = code
        self.status_code = status_code
        self.params = params
        super().__init__(code)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "params": exc.params}},
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Sem prosa: só a localização e o tipo do erro; quem traduz é o cliente.
    fields = [
        {"loc": [str(part) for part in error["loc"]], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "params": {"fields": fields}}},
    )
```

Substitua o conteúdo de `src/back/app/main.py` por:

```python
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.core.errors import AppError, app_error_handler, validation_error_handler


def create_app() -> FastAPI:
    app = FastAPI(title="PlantaoVet API")
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 10: Rodar e ver passar**

Run: `cd src/back && uv run pytest tests/test_errors.py -v`
Expected: `6 passed`

- [ ] **Step 11: Escrever os testes falhando de i18n**

Crie `src/back/tests/test_i18n.py`:

```python
import pytest

from app.i18n.catalog import catalog_keys, translate


def test_catalogs_have_identical_keys():
    assert catalog_keys("pt-BR") == catalog_keys("en")


def test_translate_interpolates_params():
    assert translate("task.check", "pt-BR", name="Fluidoterapia") == "Checagem: Fluidoterapia"
    assert translate("task.check", "en", name="Fluid therapy") == "Check: Fluid therapy"


def test_translate_falls_back_to_pt_br_for_unknown_locale():
    assert translate("ceremony.owner_contact", "xx-XX") == "Contato com o tutor"


def test_missing_key_raises_key_error():
    with pytest.raises(KeyError):
        translate("missing.key", "pt-BR")
```

- [ ] **Step 12: Rodar e ver falhar**

Run: `cd src/back && uv run pytest tests/test_i18n.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.i18n.catalog'`

- [ ] **Step 13: Implementar catálogos e translate**

Crie `src/back/app/i18n/pt-BR.json`:

```json
{
  "task.check": "Checagem: {name}",
  "ceremony.owner_contact": "Contato com o tutor",
  "ceremony.daily_progress_note": "Evolução diária",
  "compliance.br.license_authority_label": "CRMV"
}
```

Crie `src/back/app/i18n/en.json`:

```json
{
  "task.check": "Check: {name}",
  "ceremony.owner_contact": "Owner contact",
  "ceremony.daily_progress_note": "Daily progress note",
  "compliance.br.license_authority_label": "CRMV"
}
```

Crie `src/back/app/i18n/catalog.py`:

```python
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_I18N_DIR = Path(__file__).parent
SOURCE_LOCALE = "pt-BR"


@lru_cache
def _load(locale: str) -> dict[str, str]:
    path = _I18N_DIR / f"{locale}.json"
    if not path.exists():
        # Fallback para o idioma-fonte quando o locale não tem catálogo.
        path = _I18N_DIR / f"{SOURCE_LOCALE}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def translate(key: str, locale: str, **params: Any) -> str:
    catalog = _load(locale)
    template = catalog[key]  # chave ausente levanta KeyError: falha no teste,
    return template.format(**params)  # nunca produção silenciosa


def catalog_keys(locale: str) -> set[str]:
    return set(_load(locale))
```

- [ ] **Step 14: Rodar e ver passar**

Run: `cd src/back && uv run pytest tests/test_i18n.py -v`
Expected: `4 passed`

- [ ] **Step 15: Suíte completa e lint**

Run: `cd src/back && uv run pytest -v && uv run ruff check .`
Expected: `12 passed` e `All checks passed!`

- [ ] **Step 16: Commit**

```bash
git add backend docker-compose.yml
git commit -m "feat: scaffold backend with error codes and i18n catalogs"
```

### Task 2: Banco async, Alembic e harness de testes

**Files:**
- Create: `src/back/app/core/db.py`
- Create: `src/back/app/api/__init__.py`
- Create: `src/back/app/api/deps.py`
- Create: `src/back/alembic.ini` (gerado por `alembic init`)
- Create: `src/back/alembic/` (gerado por `alembic init`; `env.py` substituído)
- Create: `src/back/tests/conftest.py`
- Test: `src/back/tests/test_harness.py`

**Interfaces:**
- Consumes: `settings` (`app.core.config`, Task 1), `create_app` (`app.main`, Task 1), serviço postgres do docker-compose (Task 1).
- Produces:
  - `app.core.db.Base` (DeclarativeBase — TODA model herda dela), `engine` (AsyncEngine) e `async_session_factory` (async_sessionmaker de `AsyncSession`).
  - `app.api.deps.get_session() -> AsyncIterator[AsyncSession]` — dependency FastAPI de sessão.
  - Fixtures pytest (em `tests/conftest.py`): `migrated_database` (session-scoped: recria `plantaovet_test` e roda `alembic upgrade head`), `db_session` (AsyncSession com rollback por teste, `join_transaction_mode="create_savepoint"` — `commit()` no código sob teste vira SAVEPOINT) e `client` (httpx.AsyncClient sobre ASGITransport com `get_session` sobrescrita por `db_session`).
  - `tests.conftest.TEST_DATABASE_URL: str` — URL do banco de teste.
  - Alembic async funcional: `env.py` lê `settings.database_url`; migrações das Tasks 3+ só criam arquivos em `alembic/versions/`.

- [ ] **Step 1: Escrever os testes falhando do harness**

Crie `src/back/tests/test_harness.py`:

```python
import sqlalchemy as sa


async def test_db_session_connects_to_test_database(db_session):
    name = (await db_session.execute(sa.text("SELECT current_database()"))).scalar_one()
    assert name == "plantaovet_test"


async def test_rollback_isolation_step1_creates_scratch_table(db_session):
    # Passo 1 de 2: cria uma tabela dentro da transação do teste.
    # DDL é transacional no Postgres; o rollback do fixture deve descartá-la.
    await db_session.execute(sa.text("CREATE TABLE rollback_probe (id int)"))
    await db_session.execute(sa.text("INSERT INTO rollback_probe VALUES (1)"))
    count = (await db_session.execute(sa.text("SELECT count(*) FROM rollback_probe"))).scalar_one()
    assert count == 1


async def test_rollback_isolation_step2_scratch_table_is_gone(db_session):
    # Passo 2 de 2: roda depois do step1 (pytest segue a ordem de definição
    # dentro do arquivo). Se o rollback por teste funciona, a tabela sumiu.
    exists = (
        await db_session.execute(sa.text("SELECT to_regclass('rollback_probe') IS NOT NULL"))
    ).scalar_one()
    assert exists is False


async def test_session_commit_stays_inside_test_transaction(db_session):
    # commit() do código sob teste vira commit de SAVEPOINT
    # (join_transaction_mode="create_savepoint"); a transação externa segue aberta
    # e o teardown ainda desfaz tudo.
    await db_session.execute(sa.text("CREATE TABLE commit_probe (id int)"))
    await db_session.commit()
    exists = (
        await db_session.execute(sa.text("SELECT to_regclass('commit_probe') IS NOT NULL"))
    ).scalar_one()
    assert exists is True


async def test_client_serves_health_with_overridden_session(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd src/back && uv run pytest tests/test_harness.py -v`
Expected: `5 errors` — `fixture 'db_session' not found` / `fixture 'client' not found`

- [ ] **Step 3: Implementar engine, Base e session factory**

Crie `src/back/app/core/db.py`:

```python
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine: AsyncEngine = create_async_engine(settings.database_url)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

- [ ] **Step 4: Implementar a dependency get_session**

Crie `src/back/app/api/__init__.py` vazio e `src/back/app/api/deps.py`:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
```

- [ ] **Step 5: Inicializar o Alembic com template async e ligá-lo às settings**

Run (em `src/back/`):

```bash
uv run alembic init -t async alembic
```

Expected: cria `alembic.ini` e `alembic/` (`env.py`, `script.py.mako`, `versions/`).

Deixe `alembic.ini` como foi gerado (o `env.py` sobrescreve `sqlalchemy.url`). Substitua o conteúdo de `src/back/alembic/env.py` por:

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.core.db import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 6: Sanidade do Alembic contra o banco de dev**

Run: `cd src/back && uv run alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.` e exit 0 (ainda não há revisões — no-op).

- [ ] **Step 7: Implementar o conftest com banco de teste, rollback por teste e client**

Crie `src/back/tests/conftest.py`:

```python
import asyncio
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Fixa o DATABASE_URL no banco de TESTE antes de qualquer import de módulo do
# app (settings é instanciado no import de app.core.config).
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://plantaovet:plantaovet@localhost:5432/plantaovet"
)
_BASE_URL = os.environ["DATABASE_URL"]
TEST_DATABASE_URL = _BASE_URL.rsplit("/", 1)[0] + "/plantaovet_test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.deps import get_session
from app.main import create_app


async def _recreate_test_database() -> None:
    admin_dsn = (
        _BASE_URL.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0] + "/postgres"
    )
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("DROP DATABASE IF EXISTS plantaovet_test WITH (FORCE)")
        await conn.execute("CREATE DATABASE plantaovet_test")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_database() -> None:
    # Fixture síncrona de sessão: roda fora de qualquer event loop, então
    # asyncio.run (aqui e dentro do env.py async do Alembic) funciona.
    asyncio.run(_recreate_test_database())
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture
async def db_session(migrated_database):
    # Engine por teste: conexões asyncpg são presas ao event loop, e o
    # pytest-asyncio cria um loop novo por teste (function scope).
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def session(db_session):
    # Alias curto: as tasks 5+ pedem a sessão como `session` nas assinaturas de teste.
    # É a MESMA sessão de db_session — nunca abra uma segunda.
    return db_session


@pytest.fixture
async def client(db_session):
    app = create_app()

    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
```

- [ ] **Step 8: Rodar e ver passar**

Run: `cd src/back && uv run pytest tests/test_harness.py -v`
Expected: `5 passed`

- [ ] **Step 9: Suíte completa e lint**

Run: `cd src/back && uv run pytest -v && uv run ruff check .`
Expected: `17 passed` e `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add backend
git commit -m "feat: add async db engine, alembic and test harness"
```

### Task 3: Identidade, tenant e compliance profile

**Files:**
- Create: `src/back/app/core/security.py`
- Create: `src/back/app/models/__init__.py`
- Create: `src/back/app/models/clinic.py`
- Create: `src/back/app/models/user.py`
- Create: `src/back/app/models/membership.py`
- Create: `src/back/app/compliance/__init__.py`
- Create: `src/back/app/compliance/br.py`
- Create: `src/back/alembic/versions/0001_identity_tables.py`
- Create: `src/back/tests/factories.py`
- Modify: `src/back/alembic/env.py` (import dos models para o metadata)
- Test: `src/back/tests/test_security.py`
- Test: `src/back/tests/test_identity_models.py`
- Test: `src/back/tests/test_compliance.py`

**Interfaces:**
- Consumes: `Base` (`app.core.db`, Task 2), fixture `db_session` (Task 2), `settings` e `AppError` (Task 1), chave de catálogo `compliance.br.license_authority_label` (Task 1).
- Produces:
  - `app.core.security.hash_password(plain: str) -> str` · `verify_password(plain: str, hashed: str) -> bool` · `create_jwt(claims: dict[str, Any], *, expires_in: timedelta = timedelta(hours=12)) -> str` · `decode_jwt(token: str) -> dict[str, Any]` (HS256; expirado → `AppError("token_expired", 401)`; inválido → `AppError("invalid_credentials", 401)`).
  - Models (reexportados em `app.models`): `Clinic`, `User`, `Membership`, enums `UnitSystem(StrEnum)` (`metric|imperial`) e `Role(StrEnum)` (`vet|tech|admin`), constantes `DEFAULT_ANCHORS` e `DEFAULT_PRESCRIPTIONS`.
  - `app.compliance.ComplianceProfile` (dataclass frozen: `name`, `license_authority_label_key`, `requires_daily_progress_note`, `retention_years`) e `get_profile(name: str) -> ComplianceProfile` (desconhecido → `KeyError`); `app.compliance.br.BR_PROFILE`.
  - Factories async (em `tests/factories.py`): `make_clinic(session, **overrides) -> Clinic`, `make_user(session, **overrides) -> User`, `make_membership(session, *, clinic=None, user=None, **overrides) -> Membership`.
  - Migração `0001` com `clinics`, `users`, `memberships` e os índices aplicáveis (UNIQUE `clinics.slug`, UNIQUE `users.email`, UNIQUE `memberships(clinic_id, user_id)`).

- [ ] **Step 1: Escrever os testes falhando de senha e JWT**

Crie `src/back/tests/test_security.py`:

```python
from datetime import timedelta

import pytest

from app.core.errors import AppError
from app.core.security import create_jwt, decode_jwt, hash_password, verify_password


def test_password_hash_roundtrip():
    hashed = hash_password("s3nh4-f0rte")
    assert hashed != "s3nh4-f0rte"
    assert verify_password("s3nh4-f0rte", hashed) is True
    assert verify_password("errada", hashed) is False


def test_jwt_roundtrip():
    token = create_jwt({"sub": "membership:abc", "kind": "personal"})
    claims = decode_jwt(token)
    assert claims["sub"] == "membership:abc"
    assert claims["kind"] == "personal"
    assert "exp" in claims


def test_expired_jwt_raises_token_expired():
    token = create_jwt({"sub": "x"}, expires_in=timedelta(seconds=-1))
    with pytest.raises(AppError) as exc_info:
        decode_jwt(token)
    assert exc_info.value.code == "token_expired"
    assert exc_info.value.status_code == 401


def test_garbage_jwt_raises_invalid_credentials():
    with pytest.raises(AppError) as exc_info:
        decode_jwt("not-a-token")
    assert exc_info.value.code == "invalid_credentials"
    assert exc_info.value.status_code == 401
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd src/back && uv run pytest tests/test_security.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.core.security'`

- [ ] **Step 3: Implementar security.py**

Crie `src/back/app/core/security.py`:

```python
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import AppError


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_jwt(claims: dict[str, Any], *, expires_in: timedelta = timedelta(hours=12)) -> str:
    payload = {**claims, "exp": datetime.now(UTC) + expires_in}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_jwt(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AppError("token_expired", 401) from None
    except jwt.InvalidTokenError:
        raise AppError("invalid_credentials", 401) from None
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd src/back && uv run pytest tests/test_security.py -v`
Expected: `4 passed`

- [ ] **Step 5: Escrever os testes falhando dos models de identidade**

Crie `src/back/tests/test_identity_models.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Role
from tests.factories import make_clinic, make_membership, make_user


async def test_clinic_defaults(db_session):
    clinic = await make_clinic(db_session)
    assert clinic.locale == "pt-BR"
    assert clinic.currency == "BRL"
    assert clinic.unit_system == "metric"
    assert clinic.compliance_profile == "br"
    assert clinic.timezone == "America/Sao_Paulo"
    assert clinic.station_key_version == 1
    # Âncoras chaveadas por MINUTOS (nunca horas) — default UFMS do brief.
    assert clinic.anchors == {
        "1440": ["10:00"],
        "720": ["10:00", "22:00"],
        "480": ["10:00", "18:00", "02:00"],
        "360": ["10:00", "16:00", "22:00", "04:00"],
    }
    # Cerimônias default por name_key (conteúdo NOSSO: traduzido na criação).
    assert [p["name_key"] for p in clinic.default_prescriptions] == [
        "ceremony.owner_contact",
        "ceremony.daily_progress_note",
    ]
    assert all(p["frequency_minutes"] == 1440 for p in clinic.default_prescriptions)


async def test_membership_unique_per_clinic_and_user(db_session):
    clinic = await make_clinic(db_session)
    user = await make_user(db_session)
    await make_membership(db_session, clinic=clinic, user=user)
    with pytest.raises(IntegrityError):
        await make_membership(db_session, clinic=clinic, user=user, role=Role.tech)


async def test_same_user_can_join_two_clinics(db_session):
    user = await make_user(db_session)
    first = await make_membership(db_session, user=user)
    second = await make_membership(db_session, user=user)
    assert first.clinic_id != second.clinic_id
    assert first.user_id == second.user_id
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `cd src/back && uv run pytest tests/test_identity_models.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 7: Implementar o model Clinic com defaults**

Crie `src/back/app/models/clinic.py`:

```python
import enum
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UnitSystem(enum.StrEnum):
    metric = "metric"
    imperial = "imperial"


# Âncoras default (UFMS), chaveadas por MINUTOS de frequência.
DEFAULT_ANCHORS: dict[str, list[str]] = {
    "1440": ["10:00"],
    "720": ["10:00", "22:00"],
    "480": ["10:00", "18:00", "02:00"],
    "360": ["10:00", "16:00", "22:00", "04:00"],
}

# Cerimônias criadas automaticamente na admissão. name_key é chave de
# catálogo: o name gravado na prescrição é translate(name_key, clinic.locale).
DEFAULT_PRESCRIPTIONS: list[dict[str, Any]] = [
    {
        "name_key": "ceremony.owner_contact",
        "category": "care",
        "kind": "recurring",
        "frequency_minutes": 1440,
        "criticality": "normal",
        "anchor": "16:00",
    },
    {
        "name_key": "ceremony.daily_progress_note",
        "category": "care",
        "kind": "recurring",
        "frequency_minutes": 1440,
        "criticality": "normal",
        "anchor": "08:00",
    },
]


class Clinic(Base):
    __tablename__ = "clinics"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.Text)
    slug: Mapped[str] = mapped_column(sa.Text, unique=True)
    locale: Mapped[str] = mapped_column(sa.Text, default="pt-BR")
    currency: Mapped[str] = mapped_column(sa.String(3), default="BRL")
    unit_system: Mapped[UnitSystem] = mapped_column(
        sa.Enum(
            UnitSystem,
            name="unit_system",
            native_enum=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=UnitSystem.metric,
    )
    compliance_profile: Mapped[str] = mapped_column(sa.Text, default="br")
    timezone: Mapped[str] = mapped_column(sa.Text, default="America/Sao_Paulo")
    anchors: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=lambda: {key: list(value) for key, value in DEFAULT_ANCHORS.items()}
    )
    default_prescriptions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=lambda: [dict(item) for item in DEFAULT_PRESCRIPTIONS]
    )
    plan_tier: Mapped[str | None] = mapped_column(sa.Text, default=None)
    bed_limit: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    station_key_hash: Mapped[str | None] = mapped_column(sa.Text, default=None)
    station_key_version: Mapped[int] = mapped_column(sa.Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
```

- [ ] **Step 8: Implementar User, Membership e o pacote app.models**

Crie `src/back/app/models/user.py`:

```python
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.Text)
    email: Mapped[str] = mapped_column(sa.Text, unique=True)
    password_hash: Mapped[str] = mapped_column(sa.Text)
    locale: Mapped[str | None] = mapped_column(sa.Text, default=None)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
```

Crie `src/back/app/models/membership.py`:

```python
import enum
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Role(enum.StrEnum):
    vet = "vet"
    tech = "tech"
    admin = "admin"


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        sa.UniqueConstraint("clinic_id", "user_id", name="uq_memberships_clinic_id_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("clinics.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    role: Mapped[Role] = mapped_column(
        sa.Enum(
            Role,
            name="role",
            native_enum=False,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    license_number: Mapped[str | None] = mapped_column(sa.Text, default=None)
    license_authority: Mapped[str | None] = mapped_column(sa.Text, default=None)
    pin_hash: Mapped[str | None] = mapped_column(sa.Text, default=None)
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
```

Crie `src/back/app/models/__init__.py`:

```python
from app.models.clinic import DEFAULT_ANCHORS, DEFAULT_PRESCRIPTIONS, Clinic, UnitSystem
from app.models.membership import Membership, Role
from app.models.user import User

__all__ = [
    "DEFAULT_ANCHORS",
    "DEFAULT_PRESCRIPTIONS",
    "Clinic",
    "Membership",
    "Role",
    "UnitSystem",
    "User",
]
```

Em `src/back/alembic/env.py`, logo abaixo da linha `from app.core.db import Base`, adicione:

```python
import app.models  # noqa: F401  (registra os models no metadata)
```

- [ ] **Step 9: Escrever a migração 0001**

Run (em `src/back/`):

```bash
uv run alembic revision -m "identity tables" --rev-id 0001
```

Expected: cria `alembic/versions/0001_identity_tables.py`.

Substitua o conteúdo do arquivo por:

```python
"""identity tables

Revision ID: 0001
Revises:
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clinics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("locale", sa.Text(), nullable=False, server_default="pt-BR"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("unit_system", sa.Text(), nullable=False, server_default="metric"),
        sa.Column("compliance_profile", sa.Text(), nullable=False, server_default="br"),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="America/Sao_Paulo"),
        sa.Column("anchors", postgresql.JSONB(), nullable=False),
        sa.Column("default_prescriptions", postgresql.JSONB(), nullable=False),
        sa.Column("plan_tier", sa.Text(), nullable=True),
        sa.Column("bed_limit", sa.Integer(), nullable=True),
        sa.Column("station_key_hash", sa.Text(), nullable=True),
        sa.Column("station_key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("unit_system IN ('metric', 'imperial')", name="ck_clinics_unit_system"),
        sa.UniqueConstraint("slug", name="uq_clinics_slug"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("locale", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("license_number", sa.Text(), nullable=True),
        sa.Column("license_authority", sa.Text(), nullable=True),
        sa.Column("pin_hash", sa.Text(), nullable=True),
        sa.Column(
            "permissions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint("role IN ('vet', 'tech', 'admin')", name="ck_memberships_role"),
        sa.UniqueConstraint("clinic_id", "user_id", name="uq_memberships_clinic_id_user_id"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("clinics")
```

Sanidade contra o banco de dev:

Run: `cd src/back && uv run alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Running upgrade  -> 0001, identity tables`

- [ ] **Step 10: Implementar as factories de teste**

Crie `src/back/tests/factories.py`:

```python
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import Clinic, Membership, Role, User


async def make_clinic(session: AsyncSession, **overrides: Any) -> Clinic:
    suffix = uuid.uuid4().hex[:8]
    fields: dict[str, Any] = {"name": f"Clinic {suffix}", "slug": f"clinic-{suffix}"}
    fields.update(overrides)
    clinic = Clinic(**fields)
    session.add(clinic)
    await session.flush()
    return clinic


async def make_user(session: AsyncSession, **overrides: Any) -> User:
    suffix = uuid.uuid4().hex[:8]
    fields: dict[str, Any] = {
        "name": f"User {suffix}",
        "email": f"user-{suffix}@example.com",
        "password_hash": hash_password("secret123"),
    }
    fields.update(overrides)
    user = User(**fields)
    session.add(user)
    await session.flush()
    return user


async def make_membership(
    session: AsyncSession,
    *,
    clinic: Clinic | None = None,
    user: User | None = None,
    **overrides: Any,
) -> Membership:
    clinic = clinic or await make_clinic(session)
    user = user or await make_user(session)
    fields: dict[str, Any] = {"clinic_id": clinic.id, "user_id": user.id, "role": Role.vet}
    fields.update(overrides)
    membership = Membership(**fields)
    session.add(membership)
    await session.flush()
    return membership
```

- [ ] **Step 11: Rodar e ver passar**

Run: `cd src/back && uv run pytest tests/test_identity_models.py -v`
Expected: `3 passed`

- [ ] **Step 12: Escrever os testes falhando do compliance profile**

Crie `src/back/tests/test_compliance.py`:

```python
import pytest

from app.compliance import ComplianceProfile, get_profile
from app.i18n.catalog import catalog_keys


def test_get_profile_br():
    profile = get_profile("br")
    assert profile == ComplianceProfile(
        name="br",
        license_authority_label_key="compliance.br.license_authority_label",
        requires_daily_progress_note=True,
        retention_years=5,
    )


def test_get_profile_unknown_raises():
    with pytest.raises(KeyError):
        get_profile("atlantis")


def test_license_authority_label_key_exists_in_both_catalogs():
    key = get_profile("br").license_authority_label_key
    assert key in catalog_keys("pt-BR")
    assert key in catalog_keys("en")
```

- [ ] **Step 13: Rodar e ver falhar**

Run: `cd src/back && uv run pytest tests/test_compliance.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.compliance'`

- [ ] **Step 14: Implementar o pacote de compliance**

Crie `src/back/app/compliance/__init__.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ComplianceProfile:
    name: str
    license_authority_label_key: str
    requires_daily_progress_note: bool
    retention_years: int


def get_profile(name: str) -> ComplianceProfile:
    # Import tardio para evitar ciclo (br.py importa ComplianceProfile daqui).
    from app.compliance.br import BR_PROFILE

    profiles = {BR_PROFILE.name: BR_PROFILE}
    try:
        return profiles[name]
    except KeyError:
        raise KeyError(f"unknown compliance profile: {name}") from None
```

Crie `src/back/app/compliance/br.py`:

```python
from app.compliance import ComplianceProfile

# Perfil brasileiro (CFMV Res. 1321/2020 + 1653/2025). Nenhuma regra
# específica de país vive fora deste módulo.
BR_PROFILE = ComplianceProfile(
    name="br",
    license_authority_label_key="compliance.br.license_authority_label",
    requires_daily_progress_note=True,
    retention_years=5,
)
```

- [ ] **Step 15: Rodar e ver passar**

Run: `cd src/back && uv run pytest tests/test_compliance.py -v`
Expected: `3 passed`

- [ ] **Step 16: Suíte completa e lint**

Run: `cd src/back && uv run pytest -v && uv run ruff check .`
Expected: `27 passed` e `All checks passed!`

- [ ] **Step 17: Commit**

```bash
git add backend
git commit -m "feat: add clinic, user and membership models with security and compliance profile"
```

### Task 4: Auditoria append-only com before/after e hash encadeado

**Files:**
- Create: `src/back/app/models/audit.py`
- Create: `src/back/app/services/__init__.py`
- Create: `src/back/app/services/audit.py`
- Create: `src/back/alembic/versions/0002_audit_entries.py`
- Create: `src/back/alembic/versions/0003_audit_append_only_trigger.py`
- Modify: `src/back/app/models/__init__.py`
- Test: `src/back/tests/test_audit.py`

**Interfaces:**
- Consumes: `Base` (Task 2), fixture `db_session` (Task 2), factories `make_clinic`/`make_membership` (Task 3), migrações `0001` (Task 3).
- Produces:
  - Model `app.models.AuditEntry` (tabela `audit_entries`: `id` bigint identity, `clinic_id`, `actor_membership_id`, `actor_name`, `actor_license`, `actor_license_authority`, `action`, `entity_type`, `entity_id`, `payload` jsonb, `prev_hash`, `entry_hash`, `created_at`).
  - `app.services.audit.ActorInfo` (dataclass: `membership_id: uuid.UUID | None`, `name: str`, `license_number: str | None`, `license_authority: str | None`).
  - `app.services.audit.AuditService` com `REDACTED = {"phone_e164", "tax_id", "password_hash", "pin_hash", "station_key_hash"}`, `snapshot(entity: Any) -> dict` (estático) e `record(session, *, clinic_id, actor, action, entity_type, entity_id, before=None, after=None, extra=None) -> None` (estático, async) — assinaturas EXATAS do brief; toda mutação clínica das Tasks 5+ chama `AuditService.record`.
  - Migrações `0002` (tabela + índices `audit_entries(clinic_id, id DESC)` e `audit_entries(clinic_id, entity_type, entity_id)`) e `0003` (trigger Postgres que levanta exceção em UPDATE/DELETE).

> Regra transversal 3 levada ao banco: além de nenhum endpoint de DELETE, `audit_entries` rejeita UPDATE/DELETE no próprio Postgres. LGPD (spec §2): o `payload` carrega ids e dados clínicos, nunca dados de contato do tutor — por isso o `snapshot` redige `phone_e164`/`tax_id` antes de qualquer gravação.

- [ ] **Step 1: Escrever o teste falhando de snapshot com redação**

Crie `src/back/tests/test_audit.py`:

```python
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.services.audit import AuditService


class _SnapshotBase(DeclarativeBase):
    # Base separada: a tabela-sonda não entra no metadata do app
    # (nunca é criada no banco; snapshot só inspeciona o objeto mapeado).
    pass


class SnapshotProbe(_SnapshotBase):
    __tablename__ = "snapshot_probe"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(sa.Text)
    phone_e164: Mapped[str] = mapped_column(sa.Text)
    tax_id: Mapped[str] = mapped_column(sa.Text)
    password_hash: Mapped[str] = mapped_column(sa.Text)
    pin_hash: Mapped[str] = mapped_column(sa.Text)
    station_key_hash: Mapped[str] = mapped_column(sa.Text)


def test_redacted_set_covers_all_sensitive_columns():
    assert AuditService.REDACTED == {
        "phone_e164",
        "tax_id",
        "password_hash",
        "pin_hash",
        "station_key_hash",
    }


def test_snapshot_redacts_sensitive_columns_and_stringifies_uuid():
    probe_id = uuid.uuid4()
    probe = SnapshotProbe(
        id=probe_id,
        name="Rex",
        phone_e164="+5511999998888",
        tax_id="123.456.789-00",
        password_hash="x",
        pin_hash="y",
        station_key_hash="z",
    )
    snap = AuditService.snapshot(probe)
    assert snap == {"id": str(probe_id), "name": "Rex"}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd src/back && uv run pytest tests/test_audit.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.services'`

- [ ] **Step 3: Implementar ActorInfo e snapshot**

Crie `src/back/app/services/__init__.py` vazio e `src/back/app/services/audit.py`:

```python
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa


@dataclass
class ActorInfo:
    membership_id: uuid.UUID | None
    name: str
    license_number: str | None
    license_authority: str | None


class AuditService:
    REDACTED = {"phone_e164", "tax_id", "password_hash", "pin_hash", "station_key_hash"}

    @staticmethod
    def snapshot(entity: Any) -> dict:
        # dict das colunas do model, excluindo REDACTED;
        # uuid/datetime/Decimal como str (payload precisa ser JSON puro).
        snap: dict[str, Any] = {}
        for column in sa.inspect(entity).mapper.columns:
            if column.key in AuditService.REDACTED:
                continue
            value = getattr(entity, column.key)
            if isinstance(value, (uuid.UUID, datetime, Decimal)):
                value = str(value)
            snap[column.key] = value
        return snap
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd src/back && uv run pytest tests/test_audit.py -v`
Expected: `2 passed`

- [ ] **Step 5: Escrever os testes falhando de record e da cadeia de hash**

Substitua o bloco de imports no TOPO de `src/back/tests/test_audit.py` por:

```python
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models import AuditEntry
from app.services.audit import ActorInfo, AuditService
from tests.factories import make_clinic, make_membership
```

E adicione ao FINAL do arquivo:

```python
async def test_record_stores_before_and_after(db_session):
    clinic = await make_clinic(db_session)
    membership = await make_membership(db_session, clinic=clinic)
    actor = ActorInfo(
        membership_id=membership.id,
        name="Dra. Ana",
        license_number="12345",
        license_authority="CRMV-SP",
    )
    entity_id = uuid.uuid4()
    await AuditService.record(
        db_session,
        clinic_id=clinic.id,
        actor=actor,
        action="task_executed",
        entity_type="task",
        entity_id=entity_id,
        before={"status": "pending"},
        after={"status": "done"},
        extra={"early": False},
    )
    entry = (
        await db_session.execute(
            sa.select(AuditEntry).where(AuditEntry.clinic_id == clinic.id)
        )
    ).scalar_one()
    assert entry.payload == {
        "before": {"status": "pending"},
        "after": {"status": "done"},
        "extra": {"early": False},
    }
    assert entry.actor_membership_id == membership.id
    assert entry.actor_name == "Dra. Ana"
    assert entry.actor_license == "12345"
    assert entry.actor_license_authority == "CRMV-SP"
    assert entry.action == "task_executed"
    assert entry.entity_type == "task"
    assert entry.entity_id == entity_id
    assert entry.prev_hash == ""
    assert len(entry.entry_hash) == 64


async def test_hash_chain_links_entries_of_same_clinic(db_session):
    clinic = await make_clinic(db_session)
    await AuditService.record(
        db_session,
        clinic_id=clinic.id,
        actor=None,
        action="hospitalization_admitted",
        entity_type="hospitalization",
        entity_id=None,
    )
    await AuditService.record(
        db_session,
        clinic_id=clinic.id,
        actor=None,
        action="prescription_created",
        entity_type="prescription",
        entity_id=None,
    )
    entries = (
        (
            await db_session.execute(
                sa.select(AuditEntry)
                .where(AuditEntry.clinic_id == clinic.id)
                .order_by(AuditEntry.id)
            )
        )
        .scalars()
        .all()
    )
    assert entries[0].prev_hash == ""
    assert entries[0].actor_name == "system"
    assert entries[1].prev_hash == entries[0].entry_hash
    assert entries[1].entry_hash != entries[0].entry_hash


async def test_hash_chains_are_independent_per_clinic(db_session):
    clinic_a = await make_clinic(db_session)
    clinic_b = await make_clinic(db_session)
    await AuditService.record(
        db_session,
        clinic_id=clinic_a.id,
        actor=None,
        action="hospitalization_admitted",
        entity_type="hospitalization",
        entity_id=None,
    )
    await AuditService.record(
        db_session,
        clinic_id=clinic_b.id,
        actor=None,
        action="hospitalization_admitted",
        entity_type="hospitalization",
        entity_id=None,
    )
    entry_b = (
        await db_session.execute(
            sa.select(AuditEntry).where(AuditEntry.clinic_id == clinic_b.id)
        )
    ).scalar_one()
    # A cadeia da clínica B começa do zero: não herda o hash da clínica A.
    assert entry_b.prev_hash == ""
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `cd src/back && uv run pytest tests/test_audit.py -v`
Expected: erro de coleta do arquivo inteiro — `ImportError: cannot import name 'AuditEntry' from 'app.models'`

- [ ] **Step 7: Implementar o model AuditEntry e a migração 0002**

Crie `src/back/app/models/audit.py`:

```python
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditEntry(Base):
    __tablename__ = "audit_entries"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("clinics.id"))
    actor_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("memberships.id"), default=None
    )
    actor_name: Mapped[str] = mapped_column(sa.Text)
    actor_license: Mapped[str | None] = mapped_column(sa.Text, default=None)
    actor_license_authority: Mapped[str | None] = mapped_column(sa.Text, default=None)
    action: Mapped[str] = mapped_column(sa.Text)
    entity_type: Mapped[str] = mapped_column(sa.Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[str] = mapped_column(sa.Text)
    entry_hash: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True))
```

Substitua o conteúdo de `src/back/app/models/__init__.py` por:

```python
from app.models.audit import AuditEntry
from app.models.clinic import DEFAULT_ANCHORS, DEFAULT_PRESCRIPTIONS, Clinic, UnitSystem
from app.models.membership import Membership, Role
from app.models.user import User

__all__ = [
    "DEFAULT_ANCHORS",
    "DEFAULT_PRESCRIPTIONS",
    "AuditEntry",
    "Clinic",
    "Membership",
    "Role",
    "UnitSystem",
    "User",
]
```

Run (em `src/back/`):

```bash
uv run alembic revision -m "audit entries" --rev-id 0002
```

Substitua o conteúdo de `src/back/alembic/versions/0002_audit_entries.py` por:

```python
"""audit entries

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column(
            "actor_membership_id",
            sa.Uuid(),
            sa.ForeignKey("memberships.id"),
            nullable=True,
        ),
        sa.Column("actor_name", sa.Text(), nullable=False),
        sa.Column("actor_license", sa.Text(), nullable=True),
        sa.Column("actor_license_authority", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("prev_hash", sa.Text(), nullable=False),
        sa.Column("entry_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    # Índices da spec §4: paginação por cursor (id DESC) e busca por entidade.
    op.create_index(
        "ix_audit_entries_clinic_id_id_desc",
        "audit_entries",
        ["clinic_id", sa.text("id DESC")],
    )
    op.create_index(
        "ix_audit_entries_clinic_entity",
        "audit_entries",
        ["clinic_id", "entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_entries_clinic_entity", table_name="audit_entries")
    op.drop_index("ix_audit_entries_clinic_id_id_desc", table_name="audit_entries")
    op.drop_table("audit_entries")
```

- [ ] **Step 8: Implementar AuditService.record com hash encadeado**

Substitua o conteúdo de `src/back/app/services/audit.py` por:

```python
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


class AuditService:
    REDACTED = {"phone_e164", "tax_id", "password_hash", "pin_hash", "station_key_hash"}

    @staticmethod
    def snapshot(entity: Any) -> dict:
        # dict das colunas do model, excluindo REDACTED;
        # uuid/datetime/Decimal como str (payload precisa ser JSON puro).
        snap: dict[str, Any] = {}
        for column in sa.inspect(entity).mapper.columns:
            if column.key in AuditService.REDACTED:
                continue
            value = getattr(entity, column.key)
            if isinstance(value, (uuid.UUID, datetime, Decimal)):
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
```

- [ ] **Step 9: Rodar e ver passar**

Run: `cd src/back && uv run pytest tests/test_audit.py -v`
Expected: `5 passed`

- [ ] **Step 10: Escrever os testes falhando do append-only no banco**

Substitua o bloco de imports no TOPO de `src/back/tests/test_audit.py` por:

```python
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models import AuditEntry
from app.services.audit import ActorInfo, AuditService
from tests.factories import make_clinic, make_membership
```

E adicione ao FINAL do arquivo:

```python
async def test_direct_update_on_audit_entries_is_blocked(db_session):
    clinic = await make_clinic(db_session)
    await AuditService.record(
        db_session,
        clinic_id=clinic.id,
        actor=None,
        action="hospitalization_admitted",
        entity_type="hospitalization",
        entity_id=None,
    )
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(sa.text("UPDATE audit_entries SET action = 'tampered'"))


async def test_direct_delete_on_audit_entries_is_blocked(db_session):
    clinic = await make_clinic(db_session)
    await AuditService.record(
        db_session,
        clinic_id=clinic.id,
        actor=None,
        action="hospitalization_admitted",
        entity_type="hospitalization",
        entity_id=None,
    )
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(sa.text("DELETE FROM audit_entries"))
```

- [ ] **Step 11: Rodar e ver falhar**

Run: `cd src/back && uv run pytest tests/test_audit.py -v`
Expected: os 2 testes novos falham com `Failed: DID NOT RAISE` (ainda não há trigger); os 5 anteriores passam.

- [ ] **Step 12: Escrever a migração 0003 com o trigger append-only**

Run (em `src/back/`):

```bash
uv run alembic revision -m "audit append only trigger" --rev-id 0003
```

Substitua o conteúdo de `src/back/alembic/versions/0003_audit_append_only_trigger.py` por:

```python
"""audit append only trigger

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-31
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADR-0003: trilha imutável imposta pelo PRÓPRIO banco — correção é
    # sempre por adendo, nunca UPDATE/DELETE.
    op.execute(
        """
        CREATE FUNCTION audit_entries_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_entries is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_entries_append_only
        BEFORE UPDATE OR DELETE ON audit_entries
        FOR EACH STATEMENT EXECUTE FUNCTION audit_entries_block_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_entries_append_only ON audit_entries")
    op.execute("DROP FUNCTION audit_entries_block_mutation()")
```

- [ ] **Step 13: Rodar e ver passar**

Run: `cd src/back && uv run pytest tests/test_audit.py -v`
Expected: `7 passed` (o conftest recria `plantaovet_test` e aplica 0001→0003 no início da sessão).

- [ ] **Step 14: Suíte completa, migração no banco de dev e lint**

Run: `cd src/back && uv run pytest -v && uv run alembic upgrade head && uv run ruff check .`
Expected: `34 passed`, `Running upgrade 0001 -> 0002` e `Running upgrade 0002 -> 0003`, `All checks passed!`

- [ ] **Step 15: Commit**

```bash
git add backend
git commit -m "feat: add append-only audit trail with before/after snapshots and hash chaining"
```

<!-- Parte B do plano Semana 1 — Tasks 5 a 8. Concatenar após a Task 4. -->
<!-- Contratos exatos: docs/superpowers/plans/_drafts/_drafting-brief-semana1.md (v3). -->

### Task 5: Auth pessoal e tenancy

**Files:**
- Create: `src/back/app/schemas/auth.py`
- Create: `src/back/app/api/routes/auth.py`
- Create: `src/back/tests/helpers.py`
- Modify: `src/back/app/api/deps.py` (adiciona `AuthContext`, `get_current_auth`, `get_operator`, `get_tenant_obj`; o `get_session` da Task 2 fica intacto)
- Modify: `src/back/app/main.py` (registra o router de auth em `create_app()`)
- Test: `src/back/tests/test_auth_login.py`, `src/back/tests/test_deps_tenancy.py`

**Interfaces:**
- Consumes (Tasks 1–4 — se a assinatura implementada divergir, adapte a chamada aqui, nunca o contrato do brief):
  - `app.core.errors.AppError(code: str, status_code: int = 400, **params)` — expõe `.code`, `.status_code`, `.params`; handler registrado em `create_app()` devolve `{"error": {"code": ..., "params": {...}}}`.
  - `app.core.security`: `hash_password(password: str) -> str` · `verify_password(password: str, password_hash: str) -> bool` · `create_jwt(claims: dict[str, Any], *, expires_in: timedelta) -> str` (acrescenta `exp` a partir de `expires_in`) · `decode_jwt(token: str) -> dict[str, Any]` (expirado → `AppError("token_expired", 401)`; assinatura/estrutura inválida → `AppError("invalid_credentials", 401)`).
  - `app.api.deps.get_session() -> AsyncIterator[AsyncSession]`.
  - Models `app.models.clinic.Clinic`, `app.models.user.User`, `app.models.membership.Membership` (campos da spec §4).
  - `app.services.audit.ActorInfo(membership_id, name, license_number, license_authority)`.
  - Fixtures do conftest (Task 2): `client` (`httpx.AsyncClient` com `ASGITransport`, base_url `http://test`) e `session` (`AsyncSession`); factories `make_clinic(session, **overrides)`, `make_user(session, **overrides)`, `make_membership(session, *, clinic, user, **overrides)` — `overrides` são valores de coluna e persistem o objeto.
- Produces:
  - `POST /api/v1/auth/login` → 200 `{"access_token": str, "token_type": "bearer"}`; claims do JWT: `kind="personal"`, `sub` (user id), `clinic_id`, `membership_id`, `exp` (+12h). Credencial inválida (email desconhecido, senha errada, user/membership inativo) → `AppError("invalid_credentials", 401)`.
  - `GET /api/v1/auth/me` → `{"kind", "clinic_id", "membership_id"}` (superfície de teste de `get_current_auth`; a Task 6 a reusa para o modo estação).
  - `app.api.deps.AuthContext` — dataclass EXATA do brief: `kind: Literal["personal", "station"]`, `clinic_id: uuid.UUID`, `membership: Membership | None`.
  - `app.api.deps.get_current_auth(...) -> AuthContext` — caminho pessoal completo; token de estação é rejeitado até a Task 6 estender o branch.
  - `app.api.deps.get_operator(...) -> ActorInfo` — caminho pessoal completo; no modo estação levanta `AppError("operator_required", 403)` (a Task 6 insere a verificação do `X-Operator-Token` antes desse raise).
  - `app.api.deps.get_tenant_obj(session: AsyncSession, model: type, obj_id: uuid.UUID, clinic_id: uuid.UUID) -> Any` — assinatura EXATA do brief; 404 `AppError("not_found")` quando o objeto não existe naquele tenant. Tasks 6, 7, 8 dependem dela.
  - `src/back/tests/helpers.py`: `bearer(token) -> dict`, `personal_token(membership, *, expires_in=timedelta(hours=12)) -> str`. A Task 6 acrescenta `station_token`.

- [ ] **Step 1: Escrever os helpers de teste e os testes de login (que falham)**

Crie `src/back/tests/helpers.py`:

```python
"""Helpers compartilhados pelos testes de API.

Tokens são emitidos direto por create_jwt (sem passar pelo endpoint) para que
cada teste dependa só do contrato de claims, não do fluxo de login inteiro.
"""

from datetime import timedelta

from app.core.security import create_jwt
from app.models.membership import Membership


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def personal_token(
    membership: Membership, *, expires_in: timedelta = timedelta(hours=12)
) -> str:
    return create_jwt(
        {
            "kind": "personal",
            "sub": str(membership.user_id),
            "clinic_id": str(membership.clinic_id),
            "membership_id": str(membership.id),
        },
        expires_in=expires_in,
    )
```

Crie `src/back/tests/test_auth_login.py`:

```python
import time

from app.core.security import decode_jwt, hash_password
from tests.factories import make_clinic, make_membership, make_user


async def _personal_setup(session):
    clinic = await make_clinic(session)
    user = await make_user(
        session, email="vet@plantao.vet", password_hash=hash_password("s3nh4-forte")
    )
    membership = await make_membership(
        session,
        clinic=clinic,
        user=user,
        role="vet",
        license_number="12345",
        license_authority="CRMV-SP",
    )
    return clinic, user, membership


async def test_login_ok_devolve_jwt_pessoal_de_12h(client, session):
    clinic, user, membership = await _personal_setup(session)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "vet@plantao.vet", "password": "s3nh4-forte"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    claims = decode_jwt(data["access_token"])
    assert claims["kind"] == "personal"
    assert claims["sub"] == str(user.id)
    assert claims["clinic_id"] == str(clinic.id)
    assert claims["membership_id"] == str(membership.id)
    assert 11 * 3600 < claims["exp"] - time.time() <= 12 * 3600 + 60


async def test_login_senha_errada_devolve_codigo_nao_prosa(client, session):
    await _personal_setup(session)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "vet@plantao.vet", "password": "senha-errada"},
    )

    assert resp.status_code == 401
    assert resp.json() == {"error": {"code": "invalid_credentials", "params": {}}}


async def test_login_email_desconhecido_mesmo_codigo(client, session):
    # não vaza se o email existe: mesmo código e status da senha errada
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "ninguem@plantao.vet", "password": "qualquer"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_login_de_membership_inativo_e_recusado(client, session):
    clinic = await make_clinic(session)
    user = await make_user(
        session, email="ex@plantao.vet", password_hash=hash_password("s3nh4-forte")
    )
    await make_membership(session, clinic=clinic, user=user, role="tech", is_active=False)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "ex@plantao.vet", "password": "s3nh4-forte"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"
```

- [ ] **Step 2: Rodar e ver falhar**

Run (em `src/back/`): `uv run pytest tests/test_auth_login.py -q`
Expected: 4 FAILED — a rota `/api/v1/auth/login` não existe ainda (o app responde 404, os asserts de 200/401 falham).

- [ ] **Step 3: Implementar schemas e rota de login**

Crie `src/back/app/schemas/auth.py`:

```python
import uuid
from typing import Literal

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    kind: Literal["personal", "station"]
    clinic_id: uuid.UUID
    membership_id: uuid.UUID | None
```

Crie `src/back/app/api/routes/auth.py`:

```python
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import AppError
from app.core.security import create_jwt, verify_password
from app.models.membership import Membership
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login")
async def login(
    body: LoginRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
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
```

Em `src/back/app/main.py`, dentro de `create_app()`, registre o router junto aos `include_router` existentes (import no topo do arquivo):

```python
from app.api.routes import auth as auth_routes

# ... dentro de create_app(), após os routers já registrados:
app.include_router(auth_routes.router)
```

- [ ] **Step 4: Rodar e ver passar**

Run (em `src/back/`): `uv run pytest tests/test_auth_login.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/auth.py backend/app/api/routes/auth.py backend/app/main.py backend/tests/helpers.py backend/tests/test_auth_login.py
git commit -m "feat(auth): login pessoal com JWT de 12h e codigo invalid_credentials"
```

- [ ] **Step 6: Escrever os testes de deps e tenancy (que falham)**

Crie `src/back/tests/test_deps_tenancy.py`:

```python
from datetime import timedelta

import pytest

from app.api.deps import AuthContext, get_operator, get_tenant_obj
from app.core.errors import AppError
from app.models.membership import Membership
from tests.factories import make_clinic, make_membership, make_user
from tests.helpers import bearer, personal_token


async def test_me_com_token_pessoal(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")

    resp = await client.get("/api/v1/auth/me", headers=bearer(personal_token(membership)))

    assert resp.status_code == 200
    assert resp.json() == {
        "kind": "personal",
        "clinic_id": str(clinic.id),
        "membership_id": str(membership.id),
    }


async def test_token_expirado_devolve_token_expired(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    token = personal_token(membership, expires_in=timedelta(seconds=-1))

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "token_expired"


async def test_sem_header_authorization_e_invalid_credentials(client):
    resp = await client.get("/api/v1/auth/me")

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_membership_desativado_nao_autentica_mais(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    token = personal_token(membership)

    membership.is_active = False
    await session.commit()

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_get_operator_pessoal_monta_actor_info_do_membership(session):
    clinic = await make_clinic(session)
    user = await make_user(session, name="Dra. Ana Souza")
    membership = await make_membership(
        session,
        clinic=clinic,
        user=user,
        role="vet",
        license_number="4321",
        license_authority="CRMV-SP",
    )
    auth = AuthContext(kind="personal", clinic_id=clinic.id, membership=membership)

    actor = await get_operator(auth=auth, session=session, x_operator_token=None)

    assert actor.membership_id == membership.id
    assert actor.name == "Dra. Ana Souza"
    assert actor.license_number == "4321"
    assert actor.license_authority == "CRMV-SP"


async def test_get_tenant_obj_cross_tenant_e_404(session):
    clinic_a = await make_clinic(session, slug="clinica-a")
    clinic_b = await make_clinic(session, slug="clinica-b")
    user_b = await make_user(session, email="b@plantao.vet")
    membership_b = await make_membership(session, clinic=clinic_b, user=user_b, role="vet")

    # no tenant certo, devolve o objeto
    obj = await get_tenant_obj(session, Membership, membership_b.id, clinic_b.id)
    assert obj.id == membership_b.id

    # no tenant errado, 404 not_found — nunca 403, para não vazar existência
    with pytest.raises(AppError) as exc:
        await get_tenant_obj(session, Membership, membership_b.id, clinic_a.id)
    assert exc.value.code == "not_found"
    assert exc.value.status_code == 404
```

- [ ] **Step 7: Rodar e ver falhar**

Run (em `src/back/`): `uv run pytest tests/test_deps_tenancy.py -q`
Expected: erro de coleta — `ImportError: cannot import name 'AuthContext' from 'app.api.deps'`.

- [ ] **Step 8: Implementar AuthContext, get_current_auth, get_operator (pessoal) e get_tenant_obj**

Acrescente ao `src/back/app/api/deps.py` (o `get_session` existente fica como está; adicione os imports novos ao topo):

```python
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import decode_jwt
from app.models.membership import Membership
from app.models.user import User
from app.services.audit import ActorInfo


@dataclass
class AuthContext:
    kind: Literal["personal", "station"]
    clinic_id: uuid.UUID
    membership: Membership | None  # None quando kind == "station"


def _decode_bearer(authorization: str | None) -> dict[str, Any]:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AppError("invalid_credentials", 401)
    return decode_jwt(authorization.removeprefix("Bearer "))


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
        return AuthContext(
            kind="personal", clinic_id=membership.clinic_id, membership=membership
        )
    # kind == "station": branch adicionado na Task 6 (valida station_key_version);
    # até lá token de estação não autentica.
    raise AppError("invalid_credentials", 401)


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
        )
    # Modo estação: a Task 6 insere aqui a troca do X-Operator-Token por ActorInfo.
    raise AppError("operator_required", 403)


async def get_tenant_obj(session: AsyncSession, model: type, obj_id: uuid.UUID,
                         clinic_id: uuid.UUID) -> Any:
    obj = (
        await session.execute(
            select(model).where(model.id == obj_id, model.clinic_id == clinic_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise AppError("not_found", 404)
    return obj
```

Acrescente a rota `/me` em `src/back/app/api/routes/auth.py` (imports novos: `get_current_auth`, `AuthContext`, `MeResponse`):

```python
from app.api.deps import AuthContext, get_current_auth
from app.schemas.auth import MeResponse


@router.get("/me")
async def me(auth: AuthContext = Depends(get_current_auth)) -> MeResponse:
    return MeResponse(
        kind=auth.kind,
        clinic_id=auth.clinic_id,
        membership_id=auth.membership.id if auth.membership else None,
    )
```

- [ ] **Step 9: Rodar e ver passar**

Run (em `src/back/`): `uv run pytest tests/test_deps_tenancy.py tests/test_auth_login.py -q`
Expected: `10 passed`

- [ ] **Step 10: Suíte inteira + lint e commit**

Run (em `src/back/`): `uv run pytest -q && uv run ruff check .`
Expected: todos os testes verdes, lint sem apontamentos.

```bash
git add backend/app/api/deps.py backend/app/api/routes/auth.py backend/tests/test_deps_tenancy.py
git commit -m "feat(auth): AuthContext, get_operator pessoal e get_tenant_obj com 404 por tenant"
```

### Task 6: Modo estação endurecido

**Files:**
- Create: `src/back/app/services/pin.py` (`PinThrottle` com clock injetável, singleton `pin_throttle`, `PinService`)
- Create: `src/back/app/api/routes/memberships.py` (definição de PIN, admin-only)
- Modify: `src/back/app/schemas/auth.py` (`StationLoginRequest`, `PinRequest`, `OperatorTokenResponse`, `SetPinRequest`)
- Modify: `src/back/app/api/routes/auth.py` (`POST /station`, `POST /pin`)
- Modify: `src/back/app/api/deps.py` (branch station em `get_current_auth`, `_validate_station`, `get_station_claims`, `get_operator` completo)
- Modify: `src/back/app/main.py` (registra router de memberships)
- Modify: `src/back/tests/helpers.py` (adiciona `station_token`)
- Test: `src/back/tests/test_pin_throttle.py`, `src/back/tests/test_auth_station.py`

**Interfaces:**
- Consumes:
  - Task 5: `AuthContext`, `get_current_auth`, `get_operator`, `get_tenant_obj`, `_decode_bearer`, rota `/api/v1/auth/me`, `tests/helpers.py` (`bearer`, `personal_token`).
  - Tasks 1–4: `AppError`, `hash_password`/`verify_password`/`create_jwt`/`decode_jwt` (contrato declarado na Task 5), `AuditService.record`, models `Clinic` (`slug`, `station_key_hash`, `station_key_version`), `Membership` (`pin_hash`, `role`, `is_active`), `AuditEntry` (`payload`, `action`, `clinic_id`), factories.
- Produces:
  - `POST /api/v1/auth/station` → 200 `TokenResponse`; claims: `kind="station"`, `clinic_id`, `station_key_version`, `station_id` (uuid4 por login — identifica a estação no throttle), `exp` +12h. Slug desconhecido ou chave errada → `invalid_credentials` 401.
  - `get_current_auth` agora aceita `kind="station"`: valida `claims["station_key_version"] != clinics.station_key_version` → `AppError("station_key_rotated", 401)`; devolve `AuthContext(kind="station", clinic_id, membership=None)`.
  - `POST /api/v1/auth/pin` (só token de estação; token pessoal → `forbidden` 403) → 200 `{"operator_token": str}` (claims `kind="operator"`, `clinic_id`, `membership_id`, `exp` +5min). PIN errado → `invalid_credentials` 401 + auditoria `action="pin_failed"`. 5 falhas → `pin_locked_out` 429 com `params.retry_after_seconds` por 15 min, por estação.
  - `app.services.pin.PinThrottle(now_fn)` — `check(station_id)`, `register_failure(station_id)`, `reset(station_id)`; singleton `pin_throttle` usado pela rota.
  - `app.services.pin.PinService.set_pin(session, *, membership, pin, actor) -> None` — PIN já usado por outro membership ativo da clínica → `AppError("pin_duplicate", 409)`; audita `action="pin_set"`.
  - `POST /api/v1/memberships/{membership_id}/pin` → 204; exige token pessoal de `role="admin"` (senão `forbidden` 403); membership de outro tenant → 404.
  - `get_operator` completo: estação sem `X-Operator-Token`, com token expirado, de outro kind ou de outra clínica → `AppError("operator_required", 403)`; válido → `ActorInfo` do membership do operador. Tasks 7 e 8 usam em toda mutação.
  - `tests/helpers.py::station_token(clinic, *, station_id=None, station_key_version=None, expires_in=timedelta(hours=12)) -> str`.

- [ ] **Step 1: Escrever os testes unitários do PinThrottle (que falham)**

Crie `src/back/tests/test_pin_throttle.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AppError
from app.services.pin import PinThrottle


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.value = start

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def _clock() -> FakeClock:
    return FakeClock(datetime(2026, 8, 31, 12, 0, tzinfo=UTC))


def test_quatro_falhas_ainda_passam_quinta_bloqueia():
    clock = _clock()
    throttle = PinThrottle(now_fn=clock.now)
    for _ in range(4):
        throttle.register_failure("station-1")

    throttle.check("station-1")  # 4 falhas: ainda libera

    throttle.register_failure("station-1")
    with pytest.raises(AppError) as exc:
        throttle.check("station-1")
    assert exc.value.code == "pin_locked_out"
    assert exc.value.status_code == 429
    assert exc.value.params["retry_after_seconds"] > 0


def test_lockout_libera_apos_15_minutos():
    clock = _clock()
    throttle = PinThrottle(now_fn=clock.now)
    for _ in range(5):
        throttle.register_failure("station-1")
    with pytest.raises(AppError):
        throttle.check("station-1")

    clock.advance(timedelta(minutes=15, seconds=1))

    throttle.check("station-1")  # liberou: não levanta


def test_lockout_e_por_estacao_nao_por_clinica():
    clock = _clock()
    throttle = PinThrottle(now_fn=clock.now)
    for _ in range(5):
        throttle.register_failure("station-1")

    throttle.check("station-2")  # outra estação segue livre


def test_sucesso_zera_o_contador():
    clock = _clock()
    throttle = PinThrottle(now_fn=clock.now)
    for _ in range(4):
        throttle.register_failure("station-1")

    throttle.reset("station-1")

    for _ in range(4):
        throttle.register_failure("station-1")
    throttle.check("station-1")  # 4 de novo, não 8: não levanta
```

- [ ] **Step 2: Rodar e ver falhar**

Run (em `src/back/`): `uv run pytest tests/test_pin_throttle.py -q`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.services.pin'`.

- [ ] **Step 3: Implementar PinThrottle e PinService**

Crie `src/back/app/services/pin.py`:

```python
"""Rate limit de PIN por estação e definição de PIN único por clínica.

O relógio é injetável (now_fn) para o teste de liberação após 15 minutos não
depender de sleep. O singleton pin_throttle guarda estado em memória por
processo — suficiente para a v1 (uma instância de API); o scheduler da GCP na
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
            await session.execute(
                select(Membership).where(
                    Membership.clinic_id == membership.clinic_id,
                    Membership.id != membership.id,
                    Membership.is_active.is_(True),
                    Membership.pin_hash.is_not(None),
                )
            )
        ).scalars().all()
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
```

- [ ] **Step 4: Rodar e ver passar**

Run (em `src/back/`): `uv run pytest tests/test_pin_throttle.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pin.py backend/tests/test_pin_throttle.py
git commit -m "feat(auth): PinThrottle com clock injetavel e PinService com PIN unico por clinica"
```

- [ ] **Step 6: Escrever os testes do login de estação e da rotação de chave (que falham)**

Adicione `station_token` a `src/back/tests/helpers.py` (imports novos: `uuid`, `Clinic`):

```python
import uuid

from app.models.clinic import Clinic


def station_token(
    clinic: Clinic,
    *,
    station_id: str | None = None,
    station_key_version: int | None = None,
    expires_in: timedelta = timedelta(hours=12),
) -> str:
    return create_jwt(
        {
            "kind": "station",
            "clinic_id": str(clinic.id),
            "station_key_version": (
                station_key_version
                if station_key_version is not None
                else clinic.station_key_version
            ),
            "station_id": station_id or str(uuid.uuid4()),
        },
        expires_in=expires_in,
    )
```

Crie `src/back/tests/test_auth_station.py` com o primeiro bloco de testes:

```python
import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api.deps import AuthContext, get_operator
from app.core.errors import AppError
from app.core.security import create_jwt, decode_jwt, hash_password
from app.models.audit import AuditEntry
from tests.factories import make_clinic, make_membership, make_user
from tests.helpers import bearer, personal_token, station_token

STATION_KEY = "chave-da-estacao"


async def _station_setup(session, *, pin="1234"):
    clinic = await make_clinic(
        session, station_key_hash=hash_password(STATION_KEY), station_key_version=1
    )
    user = await make_user(session, email="tech@plantao.vet", name="Tec. Joao")
    membership = await make_membership(
        session, clinic=clinic, user=user, role="tech", pin_hash=hash_password(pin)
    )
    return clinic, membership


async def _station_login(client, clinic) -> str:
    resp = await client.post(
        "/api/v1/auth/station",
        json={"clinic_slug": clinic.slug, "station_key": STATION_KEY},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


async def test_login_de_estacao_emite_token_com_versao_e_station_id(client, session):
    clinic, _ = await _station_setup(session)

    token = await _station_login(client, clinic)

    claims = decode_jwt(token)
    assert claims["kind"] == "station"
    assert claims["clinic_id"] == str(clinic.id)
    assert claims["station_key_version"] == 1
    assert claims["station_id"]
    assert 11 * 3600 < claims["exp"] - time.time() <= 12 * 3600 + 60

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert resp.status_code == 200
    assert resp.json() == {
        "kind": "station",
        "clinic_id": str(clinic.id),
        "membership_id": None,
    }


async def test_station_key_errada_e_invalid_credentials(client, session):
    clinic, _ = await _station_setup(session)

    resp = await client.post(
        "/api/v1/auth/station",
        json={"clinic_slug": clinic.slug, "station_key": "chave-errada"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_rotacao_da_station_key_revoga_tokens_emitidos(client, session):
    clinic, _ = await _station_setup(session)
    token = station_token(clinic)  # carrega station_key_version=1

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert resp.status_code == 200

    clinic.station_key_version = 2
    await session.commit()

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "station_key_rotated"
```

- [ ] **Step 7: Rodar e ver falhar**

Run (em `src/back/`): `uv run pytest tests/test_auth_station.py -q`
Expected: 3 FAILED — `/api/v1/auth/station` não existe (404) e `/auth/me` com token de estação devolve `invalid_credentials` (o branch station da Task 5 ainda rejeita tudo).

- [ ] **Step 8: Implementar login de estação e validação de station_key_version**

Adicione a `src/back/app/schemas/auth.py`:

```python
from pydantic import Field


class StationLoginRequest(BaseModel):
    clinic_slug: str
    station_key: str


class PinRequest(BaseModel):
    pin: str = Field(pattern=r"^\d{4}$")


class SetPinRequest(BaseModel):
    pin: str = Field(pattern=r"^\d{4}$")


class OperatorTokenResponse(BaseModel):
    operator_token: str
```

Em `src/back/app/api/deps.py`, adicione `_validate_station` e `get_station_claims`, e troque o final de `get_current_auth` (import novo: `from app.models.clinic import Clinic`):

```python
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
```

Em `get_current_auth`, substitua o bloco final

```python
    # kind == "station": branch adicionado na Task 6 (valida station_key_version);
    # até lá token de estação não autentica.
    raise AppError("invalid_credentials", 401)
```

por:

```python
    if claims.get("kind") == "station":
        clinic = await _validate_station(session, claims)
        return AuthContext(kind="station", clinic_id=clinic.id, membership=None)
    raise AppError("invalid_credentials", 401)
```

Adicione a rota em `src/back/app/api/routes/auth.py` (imports novos: `uuid`, `Clinic`, `StationLoginRequest`):

```python
import uuid

from app.models.clinic import Clinic
from app.schemas.auth import StationLoginRequest


@router.post("/station")
async def station_login(
    body: StationLoginRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    clinic = (
        await session.execute(select(Clinic).where(Clinic.slug == body.clinic_slug))
    ).scalar_one_or_none()
    if (
        clinic is None
        or clinic.station_key_hash is None
        or not verify_password(body.station_key, clinic.station_key_hash)
    ):
        raise AppError("invalid_credentials", 401)

    token = create_jwt(
        {
            "kind": "station",
            "clinic_id": str(clinic.id),
            "station_key_version": clinic.station_key_version,
            # station_id identifica ESTA sessão de estação: é a chave do
            # rate limit de PIN (lockout por estação, não por clínica)
            "station_id": str(uuid.uuid4()),
        },
        expires_in=timedelta(hours=12),
    )
    return TokenResponse(access_token=token)
```

- [ ] **Step 9: Rodar e ver passar**

Run (em `src/back/`): `uv run pytest tests/test_auth_station.py tests/test_deps_tenancy.py -q`
Expected: `9 passed` (os testes da Task 5 continuam verdes com o novo branch).

- [ ] **Step 10: Commit**

```bash
git add backend/app/schemas/auth.py backend/app/api/deps.py backend/app/api/routes/auth.py backend/tests/helpers.py backend/tests/test_auth_station.py
git commit -m "feat(auth): login de estacao com station_key_version e revogacao por rotacao"
```

- [ ] **Step 11: Escrever os testes do fluxo de PIN, lockout e get_operator (que falham)**

Adicione a `src/back/tests/test_auth_station.py`:

```python
async def test_fluxo_completo_estacao_pin_operator_token(client, session):
    clinic, membership = await _station_setup(session, pin="1234")
    station_jwt = await _station_login(client, clinic)

    resp = await client.post(
        "/api/v1/auth/pin", json={"pin": "1234"}, headers=bearer(station_jwt)
    )

    assert resp.status_code == 200
    operator_jwt = resp.json()["operator_token"]
    claims = decode_jwt(operator_jwt)
    assert claims["kind"] == "operator"
    assert claims["clinic_id"] == str(clinic.id)
    assert claims["membership_id"] == str(membership.id)
    assert claims["exp"] - time.time() <= 5 * 60 + 5  # operator token vive 5 min

    # o operator token vira ActorInfo no get_operator
    auth = AuthContext(kind="station", clinic_id=clinic.id, membership=None)
    actor = await get_operator(auth=auth, session=session, x_operator_token=operator_jwt)
    assert actor.membership_id == membership.id
    assert actor.name == "Tec. Joao"


async def test_pin_errado_e_401_e_audita_pin_failed(client, session):
    clinic, _ = await _station_setup(session, pin="1234")
    station_jwt = await _station_login(client, clinic)

    resp = await client.post(
        "/api/v1/auth/pin", json={"pin": "9999"}, headers=bearer(station_jwt)
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"

    entry = (
        await session.execute(
            select(AuditEntry)
            .where(AuditEntry.clinic_id == clinic.id, AuditEntry.action == "pin_failed")
            .order_by(AuditEntry.id.desc())
        )
    ).scalars().first()
    assert entry is not None
    assert entry.payload["extra"]["station_id"] == decode_jwt(station_jwt)["station_id"]


async def test_lockout_apos_5_falhas_no_endpoint(client, session):
    clinic, _ = await _station_setup(session, pin="1234")
    station_jwt = await _station_login(client, clinic)

    for _ in range(5):
        resp = await client.post(
            "/api/v1/auth/pin", json={"pin": "0000"}, headers=bearer(station_jwt)
        )
        assert resp.status_code == 401

    # até o PIN CERTO é recusado durante o lockout
    resp = await client.post(
        "/api/v1/auth/pin", json={"pin": "1234"}, headers=bearer(station_jwt)
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "pin_locked_out"
    assert body["error"]["params"]["retry_after_seconds"] > 0
    # a liberação após 15 min é coberta em tests/test_pin_throttle.py com clock injetado


async def test_pin_com_token_pessoal_e_forbidden(client, session):
    clinic, _ = await _station_setup(session)
    user = await make_user(session, email="vet2@plantao.vet")
    vet = await make_membership(session, clinic=clinic, user=user, role="vet")

    resp = await client.post(
        "/api/v1/auth/pin", json={"pin": "1234"}, headers=bearer(personal_token(vet))
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_estacao_sem_operator_token_e_operator_required(session):
    clinic, _ = await _station_setup(session)
    auth = AuthContext(kind="station", clinic_id=clinic.id, membership=None)

    with pytest.raises(AppError) as exc:
        await get_operator(auth=auth, session=session, x_operator_token=None)

    assert exc.value.code == "operator_required"
    assert exc.value.status_code == 403


async def test_operator_token_expirado_e_operator_required(session):
    clinic, membership = await _station_setup(session)
    expired = create_jwt(
        {
            "kind": "operator",
            "clinic_id": str(clinic.id),
            "membership_id": str(membership.id),
        },
        expires_in=timedelta(seconds=-1),
    )
    auth = AuthContext(kind="station", clinic_id=clinic.id, membership=None)

    with pytest.raises(AppError) as exc:
        await get_operator(auth=auth, session=session, x_operator_token=expired)

    assert exc.value.code == "operator_required"


async def test_operator_token_de_outra_clinica_e_operator_required(client, session):
    clinic_a, _ = await _station_setup(session)
    clinic_b = await make_clinic(
        session, slug="clinica-b", station_key_hash=hash_password(STATION_KEY)
    )
    user_b = await make_user(session, email="tech-b@plantao.vet")
    membership_b = await make_membership(
        session, clinic=clinic_b, user=user_b, role="tech", pin_hash=hash_password("4321")
    )
    operator_b = create_jwt(
        {
            "kind": "operator",
            "clinic_id": str(clinic_b.id),
            "membership_id": str(membership_b.id),
        },
        expires_in=timedelta(minutes=5),
    )
    auth_a = AuthContext(kind="station", clinic_id=clinic_a.id, membership=None)

    with pytest.raises(AppError) as exc:
        await get_operator(auth=auth_a, session=session, x_operator_token=operator_b)

    assert exc.value.code == "operator_required"
```

- [ ] **Step 12: Rodar e ver falhar**

Run (em `src/back/`): `uv run pytest tests/test_auth_station.py -q`
Expected: os 3 testes anteriores passam; os 7 novos FALHAM (`/api/v1/auth/pin` responde 404; `get_operator` com operator token válido levanta `operator_required` porque o caminho estação ainda é o stub da Task 5).

- [ ] **Step 13: Implementar POST /auth/pin e completar get_operator**

Adicione a rota em `src/back/app/api/routes/auth.py` (imports novos: `get_station_claims`, `pin_throttle`, `AuditService`, `PinRequest`, `OperatorTokenResponse`):

```python
from app.api.deps import get_station_claims
from app.schemas.auth import OperatorTokenResponse, PinRequest
from app.services.audit import AuditService
from app.services.pin import pin_throttle


@router.post("/pin")
async def exchange_pin(
    body: PinRequest,
    claims: dict = Depends(get_station_claims),
    session: AsyncSession = Depends(get_session),
) -> OperatorTokenResponse:
    station_id: str = claims["station_id"]
    clinic_id = uuid.UUID(claims["clinic_id"])

    pin_throttle.check(station_id)  # 429 pin_locked_out durante o lockout

    memberships = (
        await session.execute(
            select(Membership).where(
                Membership.clinic_id == clinic_id,
                Membership.is_active.is_(True),
                Membership.pin_hash.is_not(None),
            )
        )
    ).scalars().all()
    match = next(
        (m for m in memberships if verify_password(body.pin, m.pin_hash)), None
    )

    if match is None:
        pin_throttle.register_failure(station_id)
        await AuditService.record(
            session,
            clinic_id=clinic_id,
            actor=None,
            action="pin_failed",
            entity_type="station",
            entity_id=None,
            extra={"station_id": station_id},
        )
        # commit ANTES do raise: o rastro da falha sobrevive ao rollback do erro
        await session.commit()
        raise AppError("invalid_credentials", 401)

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
```

Em `src/back/app/api/deps.py`, substitua o final de `get_operator`

```python
    # Modo estação: a Task 6 insere aqui a troca do X-Operator-Token por ActorInfo.
    raise AppError("operator_required", 403)
```

por:

```python
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
    )
```

- [ ] **Step 14: Rodar e ver passar**

Run (em `src/back/`): `uv run pytest tests/test_auth_station.py -q`
Expected: `10 passed`

- [ ] **Step 15: Commit**

```bash
git add backend/app/api/routes/auth.py backend/app/api/deps.py backend/tests/test_auth_station.py
git commit -m "feat(auth): troca de PIN por operator token de 5 min com lockout e auditoria"
```

- [ ] **Step 16: Escrever os testes de definição de PIN (que falham)**

Adicione a `src/back/tests/test_auth_station.py`:

```python
async def _admin_headers(session, clinic):
    admin_user = await make_user(session, email="admin@plantao.vet")
    admin = await make_membership(session, clinic=clinic, user=admin_user, role="admin")
    return bearer(personal_token(admin))


async def test_pin_duplicado_na_clinica_e_recusado(client, session):
    clinic, _tech_com_1234 = await _station_setup(session, pin="1234")
    headers = await _admin_headers(session, clinic)
    other_user = await make_user(session, email="vet3@plantao.vet")
    other = await make_membership(session, clinic=clinic, user=other_user, role="vet")

    resp = await client.post(
        f"/api/v1/memberships/{other.id}/pin", json={"pin": "1234"}, headers=headers
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "pin_duplicate"

    resp = await client.post(
        f"/api/v1/memberships/{other.id}/pin", json={"pin": "5678"}, headers=headers
    )
    assert resp.status_code == 204


async def test_definir_pin_exige_admin(client, session):
    clinic, tech = await _station_setup(session, pin="1234")
    vet_user = await make_user(session, email="vet4@plantao.vet")
    vet = await make_membership(session, clinic=clinic, user=vet_user, role="vet")

    resp = await client.post(
        f"/api/v1/memberships/{tech.id}/pin",
        json={"pin": "2222"},
        headers=bearer(personal_token(vet)),
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_definir_pin_de_membership_de_outra_clinica_e_404(client, session):
    clinic_a, _ = await _station_setup(session)
    headers_a = await _admin_headers(session, clinic_a)
    clinic_b = await make_clinic(session, slug="clinica-b-pin")
    user_b = await make_user(session, email="alvo-b@plantao.vet")
    membership_b = await make_membership(session, clinic=clinic_b, user=user_b, role="tech")

    resp = await client.post(
        f"/api/v1/memberships/{membership_b.id}/pin",
        json={"pin": "3333"},
        headers=headers_a,
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
```

- [ ] **Step 17: Rodar e ver falhar**

Run (em `src/back/`): `uv run pytest tests/test_auth_station.py -q`
Expected: os 3 novos testes FALHAM com 404 em `/api/v1/memberships/{id}/pin` (rota não existe; o teste de tenant falha com o assert do código `not_found`).

- [ ] **Step 18: Implementar o endpoint de definição de PIN**

Crie `src/back/app/api/routes/memberships.py`:

```python
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_operator,
    get_session,
    get_tenant_obj,
)
from app.core.errors import AppError
from app.models.membership import Membership
from app.schemas.auth import SetPinRequest
from app.services.audit import ActorInfo
from app.services.pin import PinService

router = APIRouter(prefix="/api/v1/memberships", tags=["memberships"])


@router.post("/{membership_id}/pin", status_code=204)
async def set_pin(
    membership_id: uuid.UUID,
    body: SetPinRequest,
    auth: AuthContext = Depends(get_current_auth),
    actor: ActorInfo = Depends(get_operator),
    session: AsyncSession = Depends(get_session),
) -> None:
    # v1: só o admin da clínica define/troca PINs (tela de gestão da semana 2)
    if auth.kind != "personal" or auth.membership.role != "admin":
        raise AppError("forbidden", 403)
    membership = await get_tenant_obj(session, Membership, membership_id, auth.clinic_id)
    await PinService.set_pin(session, membership=membership, pin=body.pin, actor=actor)
    await session.commit()
```

Em `src/back/app/main.py`, registre o router (import no topo, `include_router` em `create_app()`):

```python
from app.api.routes import memberships as membership_routes

# ... dentro de create_app():
app.include_router(membership_routes.router)
```

- [ ] **Step 19: Rodar e ver passar + suíte inteira**

Run (em `src/back/`): `uv run pytest tests/test_auth_station.py -q && uv run pytest -q && uv run ruff check .`
Expected: `13 passed` no arquivo, suíte inteira verde, lint limpo.

- [ ] **Step 20: Commit**

```bash
git add backend/app/api/routes/memberships.py backend/app/main.py backend/tests/test_auth_station.py
git commit -m "feat(auth): definicao de PIN admin-only com recusa de PIN duplicado"
```

<!-- CONTINUA: Task 7 -->

### Task 7: Owners, Patients e Kennels (CRUD sem DELETE, paginado e auditado)

**Files:**
- Create: `src/back/app/models/kennel.py`
- Create: `src/back/app/models/owner.py`
- Create: `src/back/app/models/patient.py`
- Modify: `src/back/app/models/__init__.py` (reexportar `Kennel`, `Owner`, `Patient`)
- Create: `src/back/app/schemas/pagination.py`
- Create: `src/back/app/schemas/kennel.py`
- Create: `src/back/app/schemas/owner.py`
- Create: `src/back/app/schemas/patient.py`
- Create: `src/back/app/api/routes/kennels.py`
- Create: `src/back/app/api/routes/owners.py`
- Create: `src/back/app/api/routes/patients.py`
- Create: `src/back/alembic/versions/0004_kennels_owners_patients.py`
- Modify: `src/back/app/main.py` (incluir os três routers)
- Modify: `src/back/tests/factories.py` (acrescentar `make_kennel`, `make_owner`, `make_patient`)
- Test: `src/back/tests/test_owners_patients_kennels.py`

**Interfaces:**
- Consumes: `AppError` (Task 1), `Base` (Task 2), `AuditService.record`/`AuditService.snapshot`/`ActorInfo` (Task 4), `get_session`/`get_current_auth`/`get_operator`/`get_tenant_obj` (Tasks 5–6), fixtures `client`/`session` e factories `make_clinic`/`make_user`/`make_membership` (Tasks 2–3), helpers `bearer`/`personal_token` (Task 5).
- Produces:
  - Models `Kennel` (`id, clinic_id, name, area, is_active`), `Owner` (`id, clinic_id, name, phone_e164, tax_id, whatsapp_opt_in_at, is_active`), `Patient` (`id, clinic_id, owner_id, name, species, breed, weight_kg, notes, is_active`).
  - `app.schemas.pagination.Page[T]` — `{"items": [...], "next_cursor": str | None}` — e `paginate(session, stmt, *, limit, cursor, id_column) -> Page` (ordena por `id` asc, `next_cursor` = id do último item quando há mais).
  - Rotas `GET/POST/PATCH /api/v1/kennels`, `/api/v1/owners`, `/api/v1/patients` — sem DELETE (regra transversal 3); `?limit=` default 50, máx 200; `?cursor=`.
  - Factories `make_kennel(session, *, clinic, **overrides)`, `make_owner(session, *, clinic, **overrides)`, `make_patient(session, *, clinic, owner=None, **overrides)`.
  - Migração `0004`.

> Regras transversais 2 e 4 aparecem aqui pela primeira vez em rota de domínio: `POST /patients` com `owner_id` de outra clínica devolve **404 `not_found`**, e cada router tem teste de isolamento em leitura e em FK de body.

- [ ] **Step 1: Escrever os testes que falham (CRUD, paginação, auditoria, tenancy)**

Crie `src/back/tests/test_owners_patients_kennels.py`:

```python
import uuid
from decimal import Decimal

import sqlalchemy as sa

from app.models import AuditEntry, Owner, Patient
from tests.factories import make_clinic, make_kennel, make_membership, make_owner, make_patient, make_user
from tests.helpers import bearer, personal_token


async def _vet(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(
        session, clinic=clinic, user=user, role="vet",
        license_number="12345", license_authority="CRMV-SP",
    )
    return clinic, membership


async def test_criar_e_listar_kennel(client, session):
    clinic, membership = await _vet(session)
    resp = await client.post(
        "/api/v1/kennels", json={"name": "UTI 03", "area": "UTI"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "UTI 03"
    assert resp.json()["is_active"] is True

    listing = await client.get("/api/v1/kennels", headers=bearer(personal_token(membership)))
    assert listing.status_code == 200
    assert [item["name"] for item in listing.json()["items"]] == ["UTI 03"]
    assert listing.json()["next_cursor"] is None


async def test_criar_owner_e_patient_vinculado(client, session):
    clinic, membership = await _vet(session)
    token = bearer(personal_token(membership))
    owner_resp = await client.post(
        "/api/v1/owners",
        json={"name": "Marina Campos", "phone_e164": "+5511999990000", "tax_id": "12345678900"},
        headers=token,
    )
    assert owner_resp.status_code == 201
    owner_id = owner_resp.json()["id"]

    patient_resp = await client.post(
        "/api/v1/patients",
        json={"name": "Thor", "species": "dog", "owner_id": owner_id, "weight_kg": "24.3"},
        headers=token,
    )
    assert patient_resp.status_code == 201
    assert patient_resp.json()["owner_id"] == owner_id
    assert patient_resp.json()["weight_kg"] == "24.3"


async def test_patch_de_peso_e_auditado_com_before_e_after(client, session):
    clinic, membership = await _vet(session)
    owner = await make_owner(session, clinic=clinic)
    patient = await make_patient(session, clinic=clinic, owner=owner, weight_kg=Decimal("24.3"))

    resp = await client.patch(
        f"/api/v1/patients/{patient.id}", json={"weight_kg": "25.1"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert resp.json()["weight_kg"] == "25.1"

    entry = (
        await session.execute(
            sa.select(AuditEntry)
            .where(AuditEntry.entity_type == "patient", AuditEntry.action == "patient_updated")
            .order_by(AuditEntry.id.desc())
        )
    ).scalars().first()
    assert entry is not None
    assert entry.payload["before"]["weight_kg"] == "24.3"
    assert entry.payload["after"]["weight_kg"] == "25.1"
    assert entry.actor_license == "12345"


async def test_snapshot_do_owner_nao_carrega_contato(client, session):
    clinic, membership = await _vet(session)
    resp = await client.post(
        "/api/v1/owners",
        json={"name": "Marina", "phone_e164": "+5511999990000", "tax_id": "12345678900"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201

    entry = (
        await session.execute(
            sa.select(AuditEntry).where(AuditEntry.entity_type == "owner").order_by(AuditEntry.id.desc())
        )
    ).scalars().first()
    assert entry is not None
    assert "phone_e164" not in entry.payload["after"]
    assert "tax_id" not in entry.payload["after"]
    assert entry.payload["after"]["name"] == "Marina"


async def test_paginacao_por_cursor_percorre_tudo_sem_repetir(client, session):
    clinic, membership = await _vet(session)
    for index in range(5):
        await make_owner(session, clinic=clinic, name=f"Tutor {index}")
    token = bearer(personal_token(membership))

    seen: list[str] = []
    cursor = None
    for _ in range(5):
        url = "/api/v1/owners?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(url, headers=token)).json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_limite_maximo_de_paginacao(client, session):
    clinic, membership = await _vet(session)
    resp = await client.get("/api/v1/owners?limit=500", headers=bearer(personal_token(membership)))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_desativacao_em_vez_de_delete(client, session):
    clinic, membership = await _vet(session)
    kennel = await make_kennel(session, clinic=clinic)
    token = bearer(personal_token(membership))

    assert (await client.delete(f"/api/v1/kennels/{kennel.id}", headers=token)).status_code == 405

    resp = await client.patch(f"/api/v1/kennels/{kennel.id}", json={"is_active": False}, headers=token)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    listing = (await client.get("/api/v1/kennels", headers=token)).json()
    assert listing["items"] == []
    todos = (await client.get("/api/v1/kennels?include_inactive=true", headers=token)).json()
    assert len(todos["items"]) == 1


async def test_isolamento_de_tenant_na_leitura(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    owner_b = await make_owner(session, clinic=clinic_b)

    resp = await client.get(f"/api/v1/owners/{owner_b.id}", headers=bearer(personal_token(membership_a)))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_isolamento_de_tenant_em_fk_de_body(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    owner_b = await make_owner(session, clinic=clinic_b)

    resp = await client.post(
        "/api/v1/patients",
        json={"name": "Invasor", "species": "dog", "owner_id": str(owner_b.id)},
        headers=bearer(personal_token(membership_a)),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_patient_de_owner_inexistente_e_404(client, session):
    clinic, membership = await _vet(session)
    resp = await client.post(
        "/api/v1/patients",
        json={"name": "Fantasma", "species": "cat", "owner_id": str(uuid.uuid4())},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Rodar e ver falhar**

Run (em `src/back/`): `uv run pytest tests/test_owners_patients_kennels.py -q`
Expected: erro de coleta — `ImportError: cannot import name 'Owner' from 'app.models'`.

- [ ] **Step 3: Implementar os três models**

Crie `src/back/app/models/kennel.py`:

```python
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Kennel(Base):
    __tablename__ = "kennels"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    name: Mapped[str] = mapped_column(sa.Text)
    area: Mapped[str | None] = mapped_column(sa.Text, default=None)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
```

Crie `src/back/app/models/owner.py`:

```python
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
```

Crie `src/back/app/models/patient.py`:

```python
import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("owners.id"))
    name: Mapped[str] = mapped_column(sa.Text)
    species: Mapped[str] = mapped_column(sa.Text)
    breed: Mapped[str | None] = mapped_column(sa.Text, default=None)
    # SEMPRE em kg (unidade SI). clinics.unit_system decide a exibição — ADR-0004.
    weight_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 3), default=None)
    notes: Mapped[str | None] = mapped_column(sa.Text, default=None)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
```

Acrescente os três a `src/back/app/models/__init__.py` (imports e `__all__`).

- [ ] **Step 4: Implementar o envelope de paginação**

Crie `src/back/app/schemas/pagination.py`:

```python
from typing import Any, Generic, TypeVar

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


async def paginate(
    session: AsyncSession,
    stmt: sa.Select,
    *,
    id_column: Any,
    limit: int,
    cursor: str | None,
    descending: bool = False,
) -> tuple[list[Any], str | None]:
    """Paginação por cursor: pede limit+1 linhas para saber se há próxima página."""
    if cursor is not None:
        stmt = stmt.where(id_column < cursor if descending else id_column > cursor)
    stmt = stmt.order_by(id_column.desc() if descending else id_column.asc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars())
    if len(rows) > limit:
        rows = rows[:limit]
        return rows, str(rows[-1].id)
    return rows, None
```

- [ ] **Step 5: Rodar e ver falhar por rota ausente**

Run: `uv run pytest tests/test_owners_patients_kennels.py -q`
Expected: falhas com `404` nas rotas (models importam, rotas ainda não existem).

- [ ] **Step 6: Implementar os schemas**

Crie `src/back/app/schemas/owner.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OwnerCreate(BaseModel):
    name: str = Field(min_length=1)
    # E.164: '+' seguido de 8 a 15 dígitos.
    phone_e164: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    tax_id: str | None = None


class OwnerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    phone_e164: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")
    tax_id: str | None = None
    whatsapp_opt_in_at: datetime | None = None
    is_active: bool | None = None


class OwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone_e164: str
    tax_id: str | None
    whatsapp_opt_in_at: datetime | None
    is_active: bool
```

Crie `src/back/app/schemas/patient.py`:

```python
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PatientCreate(BaseModel):
    name: str = Field(min_length=1)
    species: str = Field(min_length=1)
    owner_id: uuid.UUID
    breed: str | None = None
    weight_kg: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None


class PatientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    species: str | None = None
    breed: str | None = None
    weight_kg: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None
    is_active: bool | None = None


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    species: str
    breed: str | None
    weight_kg: Decimal | None
    notes: str | None
    is_active: bool
```

Crie `src/back/app/schemas/kennel.py` no mesmo formato, com `KennelCreate` (`name`, `area`), `KennelUpdate` (`name`, `area`, `is_active`) e `KennelOut` (`id`, `name`, `area`, `is_active`).

- [ ] **Step 7: Implementar o router de owners**

Crie `src/back/app/api/routes/owners.py`:

```python
import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_operator, get_session, get_tenant_obj
from app.models import Owner
from app.schemas.owner import OwnerCreate, OwnerOut, OwnerUpdate
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.services.audit import ActorInfo, AuditService

router = APIRouter(prefix="/api/v1/owners", tags=["owners"])


@router.get("", response_model=Page[OwnerOut])
async def list_owners(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
    include_inactive: bool = False,
) -> Page[OwnerOut]:
    stmt = sa.select(Owner).where(Owner.clinic_id == auth.clinic_id)
    if not include_inactive:
        stmt = stmt.where(Owner.is_active.is_(True))
    rows, next_cursor = await paginate(session, stmt, id_column=Owner.id, limit=limit, cursor=cursor)
    return Page[OwnerOut](items=[OwnerOut.model_validate(row) for row in rows], next_cursor=next_cursor)


@router.get("/{owner_id}", response_model=OwnerOut)
async def get_owner(
    owner_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OwnerOut:
    owner = await get_tenant_obj(session, Owner, owner_id, auth.clinic_id)
    return OwnerOut.model_validate(owner)


@router.post("", response_model=OwnerOut, status_code=201)
async def create_owner(
    payload: OwnerCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OwnerOut:
    owner = Owner(clinic_id=auth.clinic_id, **payload.model_dump())
    session.add(owner)
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="owner_created",
        entity_type="owner", entity_id=owner.id, after=AuditService.snapshot(owner),
    )
    await session.commit()
    return OwnerOut.model_validate(owner)


@router.patch("/{owner_id}", response_model=OwnerOut)
async def update_owner(
    owner_id: uuid.UUID,
    payload: OwnerUpdate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OwnerOut:
    owner = await get_tenant_obj(session, Owner, owner_id, auth.clinic_id)
    before = AuditService.snapshot(owner)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(owner, field, value)
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="owner_updated",
        entity_type="owner", entity_id=owner.id,
        before=before, after=AuditService.snapshot(owner),
    )
    await session.commit()
    return OwnerOut.model_validate(owner)
```

- [ ] **Step 8: Implementar os routers de kennels e patients**

`src/back/app/api/routes/kennels.py` repete a estrutura acima trocando `Owner`→`Kennel` e as actions para `kennel_created`/`kennel_updated`.

`src/back/app/api/routes/patients.py` faz o mesmo com uma diferença — a **regra transversal 2** na criação e a validação de `owner_id`:

```python
@router.post("", response_model=PatientOut, status_code=201)
async def create_patient(
    payload: PatientCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PatientOut:
    # FK vinda do body é validada contra o tenant: 404 (nunca 403 — não vazar existência).
    await get_tenant_obj(session, Owner, payload.owner_id, auth.clinic_id)
    patient = Patient(clinic_id=auth.clinic_id, **payload.model_dump())
    session.add(patient)
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="patient_created",
        entity_type="patient", entity_id=patient.id, after=AuditService.snapshot(patient),
    )
    await session.commit()
    return PatientOut.model_validate(patient)
```

Registre os três routers em `create_app()` (`app.include_router(...)`).

- [ ] **Step 9: Gerar e revisar a migração 0004**

Run (em `src/back/`): `uv run alembic revision --autogenerate -m "kennels owners patients"`
Confira que o arquivo gerado cria as três tabelas com os índices de `clinic_id` e a FK `patients.owner_id → owners.id`. Renomeie o arquivo para `0004_kennels_owners_patients.py` e ajuste `revision`/`down_revision` para `"0004"`/`"0003"`.

- [ ] **Step 10: Acrescentar as factories**

Em `src/back/tests/factories.py`:

```python
async def make_kennel(session, *, clinic, **overrides):
    kennel = Kennel(clinic_id=clinic.id, **{"name": "Box 01", **overrides})
    session.add(kennel)
    await session.flush()
    return kennel


async def make_owner(session, *, clinic, **overrides):
    owner = Owner(
        clinic_id=clinic.id,
        **{"name": "Tutor Teste", "phone_e164": "+5511999990000", **overrides},
    )
    session.add(owner)
    await session.flush()
    return owner


async def make_patient(session, *, clinic, owner=None, **overrides):
    owner = owner or await make_owner(session, clinic=clinic)
    patient = Patient(
        clinic_id=clinic.id, owner_id=owner.id,
        **{"name": "Thor", "species": "dog", **overrides},
    )
    session.add(patient)
    await session.flush()
    return patient
```

- [ ] **Step 11: Rodar e ver passar**

Run (em `src/back/`): `uv run pytest tests/test_owners_patients_kennels.py -q && uv run pytest -q && uv run ruff check .`
Expected: `10 passed` no arquivo, suíte inteira verde, lint limpo.

- [ ] **Step 12: Commit**

```bash
git add backend/app/models backend/app/schemas backend/app/api/routes backend/alembic/versions/0004_kennels_owners_patients.py backend/app/main.py backend/tests
git commit -m "feat(cadastro): owners, patients e kennels paginados, auditados e isolados por tenant"
```

---

### Task 8: Hospitalizations (admissão com consentimento, limite suave de leitos e desfecho)

**Files:**
- Create: `src/back/app/models/hospitalization.py`
- Modify: `src/back/app/models/__init__.py` (reexportar `Hospitalization`, `HospitalizationStatus`, `ConsentStatus`)
- Create: `src/back/app/schemas/hospitalization.py`
- Create: `src/back/app/services/hospitalization.py`
- Create: `src/back/app/api/routes/hospitalizations.py`
- Create: `src/back/alembic/versions/0005_hospitalizations.py`
- Modify: `src/back/app/main.py` (incluir o router)
- Modify: `src/back/tests/factories.py` (acrescentar `make_hospitalization`)
- Test: `src/back/tests/test_hospitalizations.py`

**Interfaces:**
- Consumes: `AppError` (Task 1), `Base` (Task 2), `Clinic`/`Membership` (Task 3), `AuditService` (Task 4), deps (Tasks 5–6), `Kennel`/`Owner`/`Patient` e `Page`/`paginate` (Task 7).
- Produces:
  - Model `Hospitalization` + enums `HospitalizationStatus` (`active|discharged|died|left_ama`) e `ConsentStatus` (`consent_recorded|emergency_no_consent`), com `UNIQUE (id, clinic_id)` e índice `(clinic_id, status)`.
  - `app.services.hospitalization.HospitalizationService.admit(session, *, clinic, payload, actor) -> tuple[Hospitalization, str | None]` — devolve a internação e um `warning` (`"bed_limit_exceeded"` ou `None`).
  - `app.services.hospitalization.HospitalizationService.close(session, *, hospitalization, outcome, note, actor) -> Hospitalization`.
  - `POST /api/v1/hospitalizations` → 201 `{"hospitalization": {...}, "warning": str | None}`.
  - `GET /api/v1/hospitalizations/{id}` → `{"hospitalization": {...}, "prescriptions": [], "tasks": []}` — **as duas listas nascem vazias aqui e são preenchidas pelas Tasks 9 (prescriptions) e 13 (tasks)**; o schema já reserva os campos para que o contrato não mude depois.
  - `POST /api/v1/hospitalizations/{id}/outcome` → 200; nota obrigatória em `died`/`left_ama`; `confirm_pending_tasks` já aceito no schema — **a contagem e o cancelamento de tarefas futuras entram na Task 12**, quando o model `Task` existir.
  - Factory `make_hospitalization(session, *, clinic, patient=None, membership=None, **overrides)`.
  - Migração `0005`.

> Decisão de produto (spec §1): o limite de leitos é **suave**. Estourar cria a internação assim mesmo e devolve `warning`, porque bloquear uma admissão por causa de plano seria indefensável num sistema de cuidado.

- [ ] **Step 1: Escrever os testes que falham**

Crie `src/back/tests/test_hospitalizations.py`:

```python
import sqlalchemy as sa

from app.models import AuditEntry, Hospitalization
from tests.factories import (
    make_clinic, make_kennel, make_membership, make_owner, make_patient, make_user,
)
from tests.helpers import bearer, personal_token


async def _vet(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(
        session, clinic=clinic, user=user, role="vet",
        license_number="12345", license_authority="CRMV-SP",
    )
    return clinic, membership


async def test_admitir_paciente(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    kennel = await make_kennel(session, clinic=clinic)

    resp = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(patient.id),
            "kennel_id": str(kennel.id),
            "vet_membership_id": str(membership.id),
            "consent_status": "consent_recorded",
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["warning"] is None
    assert body["hospitalization"]["status"] == "active"
    assert body["hospitalization"]["admitted_at"] is not None


async def test_emergencia_sem_termo_exige_motivo(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)

    resp = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(patient.id),
            "vet_membership_id": str(membership.id),
            "consent_status": "emergency_no_consent",
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "consent_reason_required"

    ok = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(patient.id),
            "vet_membership_id": str(membership.id),
            "consent_status": "emergency_no_consent",
            "consent_reason": "Paciente chegou em parada; tutor a caminho.",
        },
        headers=bearer(personal_token(membership)),
    )
    assert ok.status_code == 201


async def test_limite_de_leitos_e_suave(client, session):
    clinic, membership = await _vet(session)
    clinic.bed_limit = 1
    await session.flush()
    token = bearer(personal_token(membership))

    primeiro = await make_patient(session, clinic=clinic, name="Thor")
    segundo = await make_patient(session, clinic=clinic, name="Nina")

    r1 = await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(primeiro.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=token,
    )
    assert r1.json()["warning"] is None

    r2 = await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(segundo.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=token,
    )
    # Cria assim mesmo: cuidado nunca é bloqueado por plano.
    assert r2.status_code == 201
    assert r2.json()["warning"] == "bed_limit_exceeded"

    entry = (
        await session.execute(
            sa.select(AuditEntry).where(AuditEntry.action == "bed_limit_exceeded")
        )
    ).scalars().first()
    assert entry is not None


async def test_desfecho_alta(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    token = bearer(personal_token(membership))
    created = (await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=token,
    )).json()["hospitalization"]

    resp = await client.post(
        f"/api/v1/hospitalizations/{created['id']}/outcome",
        json={"outcome": "discharged"}, headers=token,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "discharged"
    assert resp.json()["ended_at"] is not None


async def test_obito_e_retirada_exigem_nota(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    token = bearer(personal_token(membership))
    created = (await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=token,
    )).json()["hospitalization"]

    for outcome in ("died", "left_ama"):
        resp = await client.post(
            f"/api/v1/hospitalizations/{created['id']}/outcome",
            json={"outcome": outcome}, headers=token,
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "outcome_note_required"

    ok = await client.post(
        f"/api/v1/hospitalizations/{created['id']}/outcome",
        json={"outcome": "died", "note": "Parada cardiorrespiratória às 03:12."},
        headers=token,
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "died"


async def test_ficha_reserva_prescriptions_e_tasks(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    token = bearer(personal_token(membership))
    created = (await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=token,
    )).json()["hospitalization"]

    resp = await client.get(f"/api/v1/hospitalizations/{created['id']}", headers=token)
    assert resp.status_code == 200
    # Contrato reservado: Task 9 preenche prescriptions, Task 13 preenche tasks.
    assert resp.json()["prescriptions"] == []
    assert resp.json()["tasks"] == []


async def test_isolamento_de_tenant_em_fk_de_body(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    patient_b = await make_patient(session, clinic=clinic_b)

    resp = await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient_b.id), "vet_membership_id": str(membership_a.id),
              "consent_status": "consent_recorded"},
        headers=bearer(personal_token(membership_a)),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_hospitalizations.py -q`
Expected: `ImportError: cannot import name 'Hospitalization' from 'app.models'`.

- [ ] **Step 3: Implementar o model**

Crie `src/back/app/models/hospitalization.py`:

```python
import uuid
from datetime import UTC, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class HospitalizationStatus(StrEnum):
    active = "active"
    discharged = "discharged"
    died = "died"
    left_ama = "left_ama"


class ConsentStatus(StrEnum):
    consent_recorded = "consent_recorded"
    emergency_no_consent = "emergency_no_consent"


def _enum(enum_cls: type[StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_cls, name=name, native_enum=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Hospitalization(Base):
    __tablename__ = "hospitalizations"
    __table_args__ = (
        # Barreira de tenancy no banco: filhos apontam para (id, clinic_id).
        sa.UniqueConstraint("id", "clinic_id", name="uq_hospitalizations_id_clinic"),
        sa.Index("ix_hospitalizations_clinic_status", "clinic_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("patients.id"))
    kennel_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("kennels.id"), default=None)
    vet_membership_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("memberships.id"))
    status: Mapped[HospitalizationStatus] = mapped_column(
        _enum(HospitalizationStatus, "hospitalization_status"),
        default=HospitalizationStatus.active,
    )
    admitted_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
    ended_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    outcome_note: Mapped[str | None] = mapped_column(sa.Text, default=None)
    consent_status: Mapped[ConsentStatus] = mapped_column(_enum(ConsentStatus, "consent_status"))
    consent_reason: Mapped[str | None] = mapped_column(sa.Text, default=None)
```

- [ ] **Step 4: Implementar os schemas**

Crie `src/back/app/schemas/hospitalization.py`:

```python
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class HospitalizationCreate(BaseModel):
    patient_id: uuid.UUID
    vet_membership_id: uuid.UUID
    kennel_id: uuid.UUID | None = None
    consent_status: Literal["consent_recorded", "emergency_no_consent"]
    consent_reason: str | None = None


class HospitalizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    kennel_id: uuid.UUID | None
    vet_membership_id: uuid.UUID
    status: str
    admitted_at: datetime
    ended_at: datetime | None
    outcome_note: str | None
    consent_status: str
    consent_reason: str | None


class HospitalizationCreated(BaseModel):
    hospitalization: HospitalizationOut
    warning: str | None = None


class HospitalizationDetail(BaseModel):
    hospitalization: HospitalizationOut
    # Reservados aqui para o contrato não mudar: Task 9 preenche prescriptions,
    # Task 13 preenche tasks.
    prescriptions: list[Any] = []
    tasks: list[Any] = []


class OutcomeRequest(BaseModel):
    outcome: Literal["discharged", "died", "left_ama"]
    note: str | None = None
    confirm_pending_tasks: bool = False
```

- [ ] **Step 5: Implementar o serviço**

Crie `src/back/app/services/hospitalization.py`:

```python
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Clinic, Hospitalization, HospitalizationStatus
from app.schemas.hospitalization import HospitalizationCreate
from app.services.audit import ActorInfo, AuditService

OUTCOMES_REQUIRING_NOTE = {"died", "left_ama"}


class HospitalizationService:
    @staticmethod
    async def admit(
        session: AsyncSession, *, clinic: Clinic, payload: HospitalizationCreate, actor: ActorInfo
    ) -> tuple[Hospitalization, str | None]:
        if payload.consent_status == "emergency_no_consent" and not payload.consent_reason:
            raise AppError("consent_reason_required", 422)

        hospitalization = Hospitalization(clinic_id=clinic.id, **payload.model_dump())
        session.add(hospitalization)
        await session.flush()
        await AuditService.record(
            session, clinic_id=clinic.id, actor=actor, action="hospitalization_admitted",
            entity_type="hospitalization", entity_id=hospitalization.id,
            after=AuditService.snapshot(hospitalization),
        )

        warning = None
        if clinic.bed_limit is not None:
            active = await session.scalar(
                sa.select(sa.func.count())
                .select_from(Hospitalization)
                .where(
                    Hospitalization.clinic_id == clinic.id,
                    Hospitalization.status == HospitalizationStatus.active,
                )
            )
            if active > clinic.bed_limit:
                warning = "bed_limit_exceeded"
                await AuditService.record(
                    session, clinic_id=clinic.id, actor=actor, action="bed_limit_exceeded",
                    entity_type="clinic", entity_id=clinic.id,
                    extra={"active": active, "bed_limit": clinic.bed_limit},
                )
        return hospitalization, warning

    @staticmethod
    async def close(
        session: AsyncSession, *, hospitalization: Hospitalization, outcome: str,
        note: str | None, actor: ActorInfo,
    ) -> Hospitalization:
        if outcome in OUTCOMES_REQUIRING_NOTE and not note:
            raise AppError("outcome_note_required", 422)

        before = AuditService.snapshot(hospitalization)
        hospitalization.status = HospitalizationStatus(outcome)
        hospitalization.ended_at = datetime.now(UTC)
        hospitalization.outcome_note = note
        await session.flush()
        await AuditService.record(
            session, clinic_id=hospitalization.clinic_id, actor=actor,
            action="hospitalization_closed", entity_type="hospitalization",
            entity_id=hospitalization.id, before=before,
            after=AuditService.snapshot(hospitalization),
        )
        return hospitalization
```

- [ ] **Step 6: Implementar o router**

Crie `src/back/app/api/routes/hospitalizations.py`:

```python
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_operator, get_session, get_tenant_obj
from app.models import Clinic, Hospitalization, Kennel, Membership, Patient
from app.schemas.hospitalization import (
    HospitalizationCreate, HospitalizationCreated, HospitalizationDetail,
    HospitalizationOut, OutcomeRequest,
)
from app.services.audit import ActorInfo, AuditService
from app.services.hospitalization import HospitalizationService

router = APIRouter(prefix="/api/v1/hospitalizations", tags=["hospitalizations"])


@router.post("", response_model=HospitalizationCreated, status_code=201)
async def admit(
    payload: HospitalizationCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalizationCreated:
    # Regra transversal 2: toda FK de body é validada contra o tenant.
    await get_tenant_obj(session, Patient, payload.patient_id, auth.clinic_id)
    await get_tenant_obj(session, Membership, payload.vet_membership_id, auth.clinic_id)
    if payload.kennel_id is not None:
        await get_tenant_obj(session, Kennel, payload.kennel_id, auth.clinic_id)

    clinic = await session.get(Clinic, auth.clinic_id)
    hospitalization, warning = await HospitalizationService.admit(
        session, clinic=clinic, payload=payload, actor=actor
    )
    await session.commit()
    return HospitalizationCreated(
        hospitalization=HospitalizationOut.model_validate(hospitalization), warning=warning
    )


@router.get("/{hospitalization_id}", response_model=HospitalizationDetail)
async def detail(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalizationDetail:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    return HospitalizationDetail(
        hospitalization=HospitalizationOut.model_validate(hospitalization)
    )


@router.post("/{hospitalization_id}/outcome", response_model=HospitalizationOut)
async def close(
    hospitalization_id: uuid.UUID,
    payload: OutcomeRequest,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalizationOut:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    await HospitalizationService.close(
        session, hospitalization=hospitalization, outcome=payload.outcome,
        note=payload.note, actor=actor,
    )
    await session.commit()
    return HospitalizationOut.model_validate(hospitalization)
```

Registre o router em `create_app()`.

- [ ] **Step 7: Gerar a migração 0005**

Run: `uv run alembic revision --autogenerate -m "hospitalizations"`
Confira `UniqueConstraint("id", "clinic_id")` e `Index("ix_hospitalizations_clinic_status")` no arquivo gerado; renomeie para `0005_hospitalizations.py` com `revision="0005"`, `down_revision="0004"`.

- [ ] **Step 8: Acrescentar a factory**

Em `src/back/tests/factories.py`:

```python
async def make_hospitalization(session, *, clinic, patient=None, membership=None, **overrides):
    patient = patient or await make_patient(session, clinic=clinic)
    if membership is None:
        user = await make_user(session)
        membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hospitalization = Hospitalization(
        clinic_id=clinic.id, patient_id=patient.id, vet_membership_id=membership.id,
        **{"consent_status": "consent_recorded", **overrides},
    )
    session.add(hospitalization)
    await session.flush()
    return hospitalization
```

- [ ] **Step 9: Rodar e ver passar**

Run (em `src/back/`): `uv run pytest tests/test_hospitalizations.py -q && uv run pytest -q && uv run ruff check .`
Expected: `7 passed` no arquivo, suíte inteira verde, lint limpo.

- [ ] **Step 10: Commit**

```bash
git add backend/app/models/hospitalization.py backend/app/models/__init__.py backend/app/schemas/hospitalization.py backend/app/services/hospitalization.py backend/app/api/routes/hospitalizations.py backend/alembic/versions/0005_hospitalizations.py backend/app/main.py backend/tests
git commit -m "feat(internacao): admissao com consentimento, limite suave de leitos e desfecho"
```

---
### Task 9: Prescriptions e as cerimônias default da clínica

**Files:**
- Create: `src/back/app/models/prescription.py`
- Modify: `src/back/app/models/__init__.py` (reexportar `Prescription`, `PrescriptionKind`, `PrescriptionCategory`, `Criticality`)
- Create: `src/back/app/schemas/prescription.py`
- Modify: `src/back/app/services/hospitalization.py` (acrescentar `create_default_prescriptions`)
- Create: `src/back/app/api/routes/prescriptions.py`
- Create: `src/back/alembic/versions/0006_prescriptions.py`
- Modify: `src/back/app/api/routes/hospitalizations.py` (chamar as cerimônias na admissão; preencher `prescriptions` no detalhe)
- Modify: `src/back/app/main.py` (incluir o router)
- Modify: `src/back/tests/factories.py` (acrescentar `make_prescription`)
- Test: `src/back/tests/test_prescriptions.py`

**Interfaces:**
- Consumes: `AppError` (Task 1), `translate` (Task 1), `Clinic.default_prescriptions`/`Clinic.locale` (Task 3), `AuditService` (Task 4), deps (Tasks 5–6), `Hospitalization`/`HospitalizationService` (Task 8).
- Produces:
  - Model `Prescription` + enums `PrescriptionKind` (`recurring|continuous|prn`), `PrescriptionCategory` (`medication|fluids|monitoring|nutrition|care|procedure`), `Criticality` (`normal|critical`); `UNIQUE (id, clinic_id)`.
  - `app.schemas.prescription.PrescriptionCreate` — valida por `kind` e resolve `tolerance_minutes` e `ends_at`.
  - `app.schemas.prescription.default_tolerance(criticality: str, frequency_minutes: int | None) -> int` — `critical` → 30; `normal` com `frequency_minutes >= 1440` → 120; senão 60.
  - `HospitalizationService.create_default_prescriptions(session, *, hospitalization, clinic, actor) -> list[Prescription]` — traduz `name_key` com o locale da clínica.
  - `POST /api/v1/hospitalizations/{id}/prescriptions` → 201 `PrescriptionOut`.
  - Factory `make_prescription(session, *, clinic, hospitalization=None, **overrides)`.
  - Migração `0006`.

> Achado clínico 1 (spec §9): a frequência é em **minutos**, não horas — monitoramento de UTI a cada 15–30 min precisa caber no schema. Achado 2: `category` existe desde já mesmo sem UI, porque jejum bloqueando alimentação e contador de dose por fármaco dependem dela.

- [ ] **Step 1: Escrever os testes que falham**

Crie `src/back/tests/test_prescriptions.py`:

```python
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import Prescription
from app.schemas.prescription import default_tolerance
from tests.factories import (
    make_clinic, make_hospitalization, make_membership, make_patient, make_user,
)
from tests.helpers import bearer, personal_token


async def _vet(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(
        session, clinic=clinic, user=user, role="vet",
        license_number="12345", license_authority="CRMV-SP",
    )
    return clinic, membership


def test_tolerancia_default_por_criticidade():
    assert default_tolerance("critical", 480) == 30
    assert default_tolerance("normal", 480) == 60
    assert default_tolerance("normal", 1440) == 120
    assert default_tolerance("critical", 1440) == 30


async def test_prescricao_recorrente(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring", "category": "medication", "name": "Dipirona 25 mg/kg IV",
            "frequency_minutes": 480, "duration_hours": 72, "criticality": "normal",
            "details": {"drug": "dipirona", "dose": "25 mg/kg", "route": "IV"},
            "price_minor": 1800,
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tolerance_minutes"] == 60
    assert body["ends_at"] is not None
    assert body["first_dose_now"] is False


async def test_recorrente_sem_frequencia_e_recusada(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Sem frequência",
              "criticality": "normal"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_continua_exige_taxa(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    sem_taxa = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "continuous", "category": "fluids", "name": "Ringer Lactato",
              "frequency_minutes": 120, "criticality": "normal", "details": {}},
        headers=bearer(personal_token(membership)),
    )
    assert sem_taxa.status_code == 422

    com_taxa = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "continuous", "category": "fluids", "name": "Ringer Lactato",
              "frequency_minutes": 120, "criticality": "normal",
              "details": {"rate_ml_h": 60}},
        headers=bearer(personal_token(membership)),
    )
    assert com_taxa.status_code == 201


async def test_prn_aceita_guardrails_e_dispensa_frequencia(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "prn", "category": "medication", "name": "Metadona 0,2 mg/kg IM",
              "criticality": "critical", "max_doses_24h": 4, "min_interval_minutes": 240,
              "details": {"drug": "metadona"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["frequency_minutes"] is None
    assert resp.json()["tolerance_minutes"] == 30


async def test_admissao_cria_cerimonias_no_locale_da_clinica(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    created = (await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=bearer(personal_token(membership)),
    )).json()["hospitalization"]

    rows = list((await session.execute(
        sa.select(Prescription).where(Prescription.hospitalization_id == created["id"])
    )).scalars())
    nomes = sorted(row.name for row in rows)
    assert nomes == ["Contato com tutor", "Evolução diária"]
    assert all(row.category == "care" for row in rows)
    assert all(row.frequency_minutes == 1440 for row in rows)


async def test_cerimonias_em_ingles_quando_a_clinica_e_en(client, session):
    clinic = await make_clinic(session, slug="us-clinic", locale="en")
    clinic, membership = await _vet(session, clinic)
    patient = await make_patient(session, clinic=clinic)
    created = (await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=bearer(personal_token(membership)),
    )).json()["hospitalization"]

    rows = list((await session.execute(
        sa.select(Prescription).where(Prescription.hospitalization_id == created["id"])
    )).scalars())
    assert sorted(row.name for row in rows) == ["Daily progress note", "Owner contact"]


async def test_detalhe_da_internacao_traz_prescricoes(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona",
              "frequency_minutes": 480, "criticality": "normal", "details": {"drug": "dipirona"}},
        headers=token,
    )
    detail = (await client.get(f"/api/v1/hospitalizations/{hosp.id}", headers=token)).json()
    assert [item["name"] for item in detail["prescriptions"]] == ["Dipirona"]


async def test_isolamento_de_tenant(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    hosp_b = await make_hospitalization(session, clinic=clinic_b)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp_b.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Invasora",
              "frequency_minutes": 480, "criticality": "normal", "details": {}},
        headers=bearer(personal_token(membership_a)),
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_prescriptions.py -q`
Expected: `ImportError: cannot import name 'Prescription' from 'app.models'`.

- [ ] **Step 3: Implementar o model**

Crie `src/back/app/models/prescription.py`:

```python
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PrescriptionKind(StrEnum):
    recurring = "recurring"
    continuous = "continuous"
    prn = "prn"


class PrescriptionCategory(StrEnum):
    medication = "medication"
    fluids = "fluids"
    monitoring = "monitoring"
    nutrition = "nutrition"
    care = "care"
    procedure = "procedure"


class Criticality(StrEnum):
    normal = "normal"
    critical = "critical"


def _enum(enum_cls: type[StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_cls, name=name, native_enum=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Prescription(Base):
    __tablename__ = "prescriptions"
    __table_args__ = (
        sa.UniqueConstraint("id", "clinic_id", name="uq_prescriptions_id_clinic"),
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_prescriptions_hospitalization_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    hospitalization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    kind: Mapped[PrescriptionKind] = mapped_column(_enum(PrescriptionKind, "prescription_kind"))
    category: Mapped[PrescriptionCategory] = mapped_column(
        _enum(PrescriptionCategory, "prescription_category")
    )
    name: Mapped[str] = mapped_column(sa.Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # MINUTOS (achado clínico 1): UTI monitora a cada 15–60 min.
    frequency_minutes: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    duration_hours: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    criticality: Mapped[Criticality] = mapped_column(
        _enum(Criticality, "criticality"), default=Criticality.normal
    )
    tolerance_minutes: Mapped[int] = mapped_column(sa.Integer)
    first_dose_now: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    is_controlled: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    max_doses_24h: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    min_interval_minutes: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    price_minor: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    starts_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    replaces_prescription_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("prescriptions.id"), default=None
    )
    suspended_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    suspended_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
```

- [ ] **Step 4: Implementar os schemas com a validação por kind**

Crie `src/back/app/schemas/prescription.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CRITICAL_TOLERANCE = 30
NORMAL_TOLERANCE = 60
DAILY_TOLERANCE = 120
DAILY_FREQUENCY_MINUTES = 1440


def default_tolerance(criticality: str, frequency_minutes: int | None) -> int:
    """Janelas ISMP (spec §2): crítica 30; normal 60; normal diária ou mais espaçada 120."""
    if criticality == "critical":
        return CRITICAL_TOLERANCE
    if frequency_minutes is not None and frequency_minutes >= DAILY_FREQUENCY_MINUTES:
        return DAILY_TOLERANCE
    return NORMAL_TOLERANCE


class PrescriptionCreate(BaseModel):
    kind: Literal["recurring", "continuous", "prn"]
    category: Literal["medication", "fluids", "monitoring", "nutrition", "care", "procedure"]
    name: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    frequency_minutes: int | None = Field(default=None, ge=5)
    duration_hours: int | None = Field(default=None, ge=1)
    criticality: Literal["normal", "critical"] = "normal"
    tolerance_minutes: int | None = Field(default=None, ge=1)
    first_dose_now: bool = False
    is_controlled: bool = False
    max_doses_24h: int | None = Field(default=None, ge=1)
    min_interval_minutes: int | None = Field(default=None, ge=1)
    price_minor: int | None = Field(default=None, ge=0)
    starts_at: datetime | None = None

    @model_validator(mode="after")
    def check_kind(self) -> "PrescriptionCreate":
        if self.kind in ("recurring", "continuous") and self.frequency_minutes is None:
            raise ValueError("frequency_minutes é obrigatório para recurring e continuous")
        if self.kind == "prn" and self.frequency_minutes is not None:
            raise ValueError("prn não tem agenda: frequency_minutes deve ficar vazio")
        if self.kind == "continuous" and "rate_ml_h" not in self.details:
            raise ValueError("continuous exige details.rate_ml_h")
        return self

    def resolved_tolerance(self) -> int:
        return self.tolerance_minutes or default_tolerance(self.criticality, self.frequency_minutes)

    def resolved_starts_at(self) -> datetime:
        return self.starts_at or datetime.now(UTC)

    def resolved_ends_at(self) -> datetime | None:
        if self.duration_hours is None:
            return None
        return self.resolved_starts_at() + timedelta(hours=self.duration_hours)


class PrescriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hospitalization_id: uuid.UUID
    kind: str
    category: str
    name: str
    details: dict[str, Any]
    frequency_minutes: int | None
    criticality: str
    tolerance_minutes: int
    first_dose_now: bool
    is_controlled: bool
    max_doses_24h: int | None
    min_interval_minutes: int | None
    price_minor: int | None
    starts_at: datetime
    ends_at: datetime | None
    replaces_prescription_id: uuid.UUID | None
    suspended_at: datetime | None
```

- [ ] **Step 5: Implementar as cerimônias default**

Acrescente a `src/back/app/services/hospitalization.py`:

```python
from app.i18n.catalog import translate
from app.models import Prescription
from app.schemas.prescription import default_tolerance


class HospitalizationService:
    # Os métodos `admit` (Task 8) e `close` (Task 8) continuam onde estão;
    # acrescente APENAS o método abaixo à classe existente.

    @staticmethod
    async def create_default_prescriptions(
        session: AsyncSession, *, hospitalization: Hospitalization, clinic: Clinic, actor: ActorInfo
    ) -> list[Prescription]:
        """Cerimônias do dia (spec §2): nascem da admissão e reusam aprazamento,
        tolerância e auditoria — sem entidade nova. O `name_key` é conteúdo NOSSO,
        então é traduzido no locale da clínica; nome digitado pela clínica nunca é."""
        created: list[Prescription] = []
        for template in clinic.default_prescriptions:
            prescription = Prescription(
                clinic_id=clinic.id,
                hospitalization_id=hospitalization.id,
                kind=template.get("kind", "recurring"),
                category=template.get("category", "care"),
                name=translate(template["name_key"], clinic.locale),
                details={"anchor": template["anchor"]} if template.get("anchor") else {},
                frequency_minutes=template["frequency_minutes"],
                criticality=template.get("criticality", "normal"),
                tolerance_minutes=default_tolerance(
                    template.get("criticality", "normal"), template["frequency_minutes"]
                ),
                starts_at=hospitalization.admitted_at,
                created_by=actor.membership_id,
            )
            session.add(prescription)
            created.append(prescription)
        await session.flush()
        for prescription in created:
            await AuditService.record(
                session, clinic_id=clinic.id, actor=actor, action="prescription_created",
                entity_type="prescription", entity_id=prescription.id,
                after=AuditService.snapshot(prescription), extra={"source": "clinic_default"},
            )
        return created
```

- [ ] **Step 6: Chamar as cerimônias na admissão**

Em `src/back/app/api/routes/hospitalizations.py`, dentro de `admit`, depois de `HospitalizationService.admit(...)` e antes do `commit`:

```python
    await HospitalizationService.create_default_prescriptions(
        session, hospitalization=hospitalization, clinic=clinic, actor=actor
    )
```

E no `detail`, preencha `prescriptions` (o campo que a Task 8 reservou):

```python
    prescriptions = list((await session.execute(
        sa.select(Prescription)
        .where(
            Prescription.hospitalization_id == hospitalization.id,
            Prescription.suspended_at.is_(None),
        )
        .order_by(Prescription.starts_at)
    )).scalars())
    return HospitalizationDetail(
        hospitalization=HospitalizationOut.model_validate(hospitalization),
        prescriptions=[PrescriptionOut.model_validate(row) for row in prescriptions],
    )
```

- [ ] **Step 7: Implementar o router de prescrições**

Crie `src/back/app/api/routes/prescriptions.py`:

```python
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_operator, get_session, get_tenant_obj
from app.models import Hospitalization, Prescription
from app.schemas.prescription import PrescriptionCreate, PrescriptionOut
from app.services.audit import ActorInfo, AuditService

router = APIRouter(prefix="/api/v1", tags=["prescriptions"])


@router.post(
    "/hospitalizations/{hospitalization_id}/prescriptions",
    response_model=PrescriptionOut, status_code=201,
)
async def create_prescription(
    hospitalization_id: uuid.UUID,
    payload: PrescriptionCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PrescriptionOut:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    prescription = Prescription(
        clinic_id=auth.clinic_id,
        hospitalization_id=hospitalization.id,
        kind=payload.kind,
        category=payload.category,
        name=payload.name,
        details=payload.details,
        frequency_minutes=payload.frequency_minutes,
        duration_hours=payload.duration_hours,
        criticality=payload.criticality,
        tolerance_minutes=payload.resolved_tolerance(),
        first_dose_now=payload.first_dose_now,
        is_controlled=payload.is_controlled,
        max_doses_24h=payload.max_doses_24h,
        min_interval_minutes=payload.min_interval_minutes,
        price_minor=payload.price_minor,
        starts_at=payload.resolved_starts_at(),
        ends_at=payload.resolved_ends_at(),
        created_by=actor.membership_id,
    )
    session.add(prescription)
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="prescription_created",
        entity_type="prescription", entity_id=prescription.id,
        after=AuditService.snapshot(prescription),
    )
    await session.commit()
    return PrescriptionOut.model_validate(prescription)
```

Registre o router em `create_app()`.

- [ ] **Step 8: Migração, factory e verde**

Run: `uv run alembic revision --autogenerate -m "prescriptions"` → renomeie para `0006_prescriptions.py` (`revision="0006"`, `down_revision="0005"`).

Em `src/back/tests/factories.py`:

```python
async def make_prescription(session, *, clinic, hospitalization=None, **overrides):
    hospitalization = hospitalization or await make_hospitalization(session, clinic=clinic)
    values = {
        "kind": "recurring", "category": "medication", "name": "Dipirona 25 mg/kg IV",
        "details": {"drug": "dipirona"}, "frequency_minutes": 480, "criticality": "normal",
        "tolerance_minutes": 60, "starts_at": datetime.now(UTC), **overrides,
    }
    prescription = Prescription(
        clinic_id=clinic.id, hospitalization_id=hospitalization.id, **values
    )
    session.add(prescription)
    await session.flush()
    return prescription
```

Run: `uv run pytest tests/test_prescriptions.py -q && uv run pytest -q && uv run ruff check .`
Expected: `9 passed` no arquivo, suíte verde, lint limpo.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/prescription.py backend/app/models/__init__.py backend/app/schemas/prescription.py backend/app/services/hospitalization.py backend/app/api/routes backend/alembic/versions/0006_prescriptions.py backend/app/main.py backend/tests
git commit -m "feat(prescricao): kinds, categorias, tolerancia ISMP e cerimonias default da clinica"
```

---

### Task 10: SchedulingService.generate — o aprazamento, puro e sem I/O

**Files:**
- Create: `src/back/app/services/scheduling.py`
- Test: `src/back/tests/test_scheduling.py`

**Interfaces:**
- Consumes: `Prescription` (Task 9), `Clinic` (`timezone`, `anchors`, `locale` — Task 3), `translate` (Task 1), `Task` (model da Task 11 — nesta task o serviço monta objetos `Task` **em memória**, sem persistir; a Task 11 os grava).
- Produces:
  - `app.services.scheduling.SchedulingService.generate(prescription, clinic, until) -> list[Task]` — assinatura EXATA do brief, função pura.
  - `app.services.scheduling.local_to_utc(day, hhmm, tzinfo) -> datetime` — conversão com tratamento de hora inexistente/ambígua (DST).

> Esta é a task mais densa em regra de negócio do plano e a que mais protege o produto: os horários de dose nascem aqui. Por ser pura, cada regra vira um teste com números concretos, sem banco e sem HTTP.

- [ ] **Step 1: Escrever os testes que falham (todas as 8 regras do contrato)**

Crie `src/back/tests/test_scheduling.py`:

```python
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Clinic, Prescription
from app.services.scheduling import SchedulingService

SP = ZoneInfo("America/Sao_Paulo")
ANCHORS = {"1440": ["10:00"], "720": ["10:00", "22:00"], "480": ["10:00", "18:00", "02:00"]}


def _clinic(timezone: str = "America/Sao_Paulo", locale: str = "pt-BR") -> Clinic:
    return Clinic(
        name="Demo", slug="demo", timezone=timezone, locale=locale,
        anchors=dict(ANCHORS), default_prescriptions=[],
    )


def _prescription(**overrides) -> Prescription:
    values = {
        "kind": "recurring", "category": "medication", "name": "Dipirona",
        "details": {}, "frequency_minutes": 480, "criticality": "normal",
        "tolerance_minutes": 60, "first_dose_now": False,
        "starts_at": datetime(2026, 9, 1, 17, 0, tzinfo=UTC),  # 14:00 em São Paulo
        "ends_at": None,
    }
    values.update(overrides)
    return Prescription(**values)


def _local_times(tasks, tz=SP):
    return [task.scheduled_for.astimezone(tz).strftime("%d/%m %H:%M") for task in tasks]


def test_ancoras_a_partir_das_14h():
    tasks = SchedulingService.generate(
        _prescription(), _clinic(), until=datetime(2026, 9, 3, 3, 0, tzinfo=UTC)
    )
    assert _local_times(tasks)[:4] == ["01/09 18:00", "02/09 02:00", "02/09 10:00", "02/09 18:00"]


def test_rollover_quando_as_ancoras_do_dia_acabam():
    # Admissão às 23:00: não existe âncora >= 23:00 nesse dia; a próxima é 02:00 do dia seguinte.
    tasks = SchedulingService.generate(
        _prescription(starts_at=datetime(2026, 9, 2, 2, 0, tzinfo=UTC)),  # 23:00 do dia 01 em SP
        _clinic(), until=datetime(2026, 9, 2, 22, 0, tzinfo=UTC),
    )
    assert _local_times(tasks)[0] == "02/09 02:00"


def test_frequencia_sem_ancora_usa_offset():
    # q30min: monitoramento de UTI — não existe âncora para 30, então offset puro.
    tasks = SchedulingService.generate(
        _prescription(frequency_minutes=30),
        _clinic(), until=datetime(2026, 9, 1, 19, 0, tzinfo=UTC),
    )
    assert _local_times(tasks) == ["01/09 14:00", "01/09 14:30", "01/09 15:00", "01/09 15:30", "01/09 16:00"]


def test_primeira_dose_agora_suprime_a_ancora_proxima():
    # 14:00 + q8h com âncoras 10/18/02: a de 18:00 fica a 4h < (480 - 60)min → suprimida.
    tasks = SchedulingService.generate(
        _prescription(first_dose_now=True),
        _clinic(), until=datetime(2026, 9, 2, 14, 0, tzinfo=UTC),
    )
    assert _local_times(tasks)[:3] == ["01/09 14:00", "02/09 02:00", "02/09 10:00"]


def test_primeira_dose_agora_sem_supressao_quando_a_ancora_esta_longe():
    # 10:30 local: a âncora das 18:00 está a 7h30 >= (480-60)min → NÃO é suprimida.
    tasks = SchedulingService.generate(
        _prescription(first_dose_now=True, starts_at=datetime(2026, 9, 1, 13, 30, tzinfo=UTC)),
        _clinic(), until=datetime(2026, 9, 2, 4, 0, tzinfo=UTC),
    )
    assert _local_times(tasks)[:2] == ["01/09 10:30", "01/09 18:00"]


def test_ends_at_corta_o_horizonte():
    tasks = SchedulingService.generate(
        _prescription(ends_at=datetime(2026, 9, 2, 3, 0, tzinfo=UTC)),  # 02/09 00:00 em SP
        _clinic(), until=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
    )
    assert _local_times(tasks) == ["01/09 18:00"]


def test_until_antes_do_inicio_devolve_vazio():
    tasks = SchedulingService.generate(
        _prescription(), _clinic(), until=datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    )
    assert tasks == []


def test_prn_nao_gera_nada():
    tasks = SchedulingService.generate(
        _prescription(kind="prn", frequency_minutes=None),
        _clinic(), until=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
    )
    assert tasks == []


def test_continua_gera_tarefa_de_checagem_traduzida():
    tasks = SchedulingService.generate(
        _prescription(kind="continuous", name="Ringer Lactato", frequency_minutes=120,
                      details={"rate_ml_h": 60}),
        _clinic(), until=datetime(2026, 9, 1, 21, 0, tzinfo=UTC),
    )
    assert tasks[0].title == "Checagem: Ringer Lactato"
    assert tasks[0].category == "medication"


def test_continua_em_ingles():
    tasks = SchedulingService.generate(
        _prescription(kind="continuous", name="Lactated Ringer", frequency_minutes=120,
                      details={"rate_ml_h": 60}),
        _clinic(locale="en"), until=datetime(2026, 9, 1, 21, 0, tzinfo=UTC),
    )
    assert tasks[0].title == "Check: Lactated Ringer"


def test_horario_de_verao_nao_estoura_nem_duplica():
    # America/Santiago adianta o relógio em 06/09/2026: 00:00 não existe nesse dia.
    clinic = _clinic(timezone="America/Santiago")
    clinic.anchors = {"1440": ["00:00"]}
    tasks = SchedulingService.generate(
        _prescription(frequency_minutes=1440,
                      starts_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC)),
        clinic, until=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    )
    assert len(tasks) == len({task.scheduled_for for task in tasks})
    assert all(task.scheduled_for.tzinfo is not None for task in tasks)
    assert tasks == sorted(tasks, key=lambda task: task.scheduled_for)


def test_ordem_e_sempre_crescente():
    tasks = SchedulingService.generate(
        _prescription(), _clinic(), until=datetime(2026, 9, 5, 0, 0, tzinfo=UTC)
    )
    horarios = [task.scheduled_for for task in tasks]
    assert horarios == sorted(horarios)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_scheduling.py -q`
Expected: `ModuleNotFoundError: No module named 'app.services.scheduling'`.

- [ ] **Step 3: Implementar a conversão local→UTC com DST**

Crie `src/back/app/services/scheduling.py`:

```python
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.i18n.catalog import translate
from app.models import Clinic, Prescription, Task

HORIZON_STEP = timedelta(days=1)


def local_to_utc(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    """Converte 'HH:MM' de um dia local para UTC.

    Regra 7 do contrato: numa virada de horário de verão a hora local pode não
    existir (adiantamento) — nesse caso empurramos para a próxima hora válida —
    ou ser ambígua (atraso), quando usamos a primeira ocorrência (fold=0).
    """
    hour, minute = (int(part) for part in hhmm.split(":"))
    naive = datetime.combine(day, time(hour, minute))
    candidate = naive.replace(tzinfo=tz, fold=0)
    # Hora inexistente: o offset "salta", então a ida-e-volta não bate.
    if candidate.astimezone(ZoneInfo("UTC")).astimezone(tz).replace(tzinfo=None) != naive:
        candidate = (naive + timedelta(hours=1)).replace(tzinfo=tz, fold=0)
    return candidate.astimezone(ZoneInfo("UTC"))
```

- [ ] **Step 4: Rodar e ver falhar no generate**

Run: `uv run pytest tests/test_scheduling.py -q`
Expected: `AttributeError: module 'app.services.scheduling' has no attribute 'SchedulingService'`.

- [ ] **Step 5: Implementar SchedulingService.generate**

Acrescente a `src/back/app/services/scheduling.py`:

```python
class SchedulingService:
    @staticmethod
    def generate(prescription: Prescription, clinic: Clinic, until: datetime) -> list[Task]:
        """Contrato do brief, regras 1 a 8. Função PURA: monta objetos Task em
        memória (quem persiste é a Task 11) e não toca em banco nem em relógio."""
        if prescription.kind == "prn":  # regra 1
            return []

        horizon = until  # regra 2
        if prescription.ends_at is not None:
            horizon = min(horizon, prescription.ends_at)
        start = prescription.starts_at
        if horizon <= start and not prescription.first_dose_now:
            return []

        tz = ZoneInfo(clinic.timezone)
        anchors = clinic.anchors.get(str(prescription.frequency_minutes))
        moments: list[datetime] = []

        if prescription.first_dose_now:  # regra 5 (parte 1)
            moments.append(start)

        if anchors:  # regra 3
            ordered = sorted(anchors)
            day = start.astimezone(tz).date()
            # Uma volta a mais que o horizonte para cobrir o rollover do último dia.
            while True:
                for hhmm in ordered:
                    moment = local_to_utc(day, hhmm, tz)
                    if moment < start:
                        continue
                    if moment > horizon:
                        break
                    moments.append(moment)
                day += HORIZON_STEP
                if local_to_utc(day, ordered[0], tz) > horizon:
                    break
        else:  # regra 4
            step = timedelta(minutes=prescription.frequency_minutes)
            moment = start
            while moment <= horizon:
                if not (prescription.first_dose_now and moment == start):
                    moments.append(moment)
                moment += step

        if prescription.first_dose_now and anchors:  # regra 5 (parte 2)
            gap = timedelta(minutes=prescription.frequency_minutes - prescription.tolerance_minutes)
            moments = [
                moment for moment in moments
                if moment == start or moment - start >= gap
            ]

        title = prescription.name
        if prescription.kind == "continuous":  # regra 6
            title = translate("task.check", clinic.locale, name=prescription.name)

        return [
            Task(
                clinic_id=prescription.clinic_id,
                hospitalization_id=prescription.hospitalization_id,
                prescription_id=prescription.id,
                title=title,
                category=prescription.category,
                scheduled_for=moment,
                criticality=prescription.criticality,
                tolerance_minutes=prescription.tolerance_minutes,
                price_minor=prescription.price_minor,
            )
            for moment in sorted(set(moments))
        ]
```

> A regra 8 (idempotência por `INSERT ... ON CONFLICT`) e o congelamento de `scheduled_for` são responsabilidade de quem persiste — Task 11.

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/test_scheduling.py -q`
Expected: `12 passed`.

> Se `test_horario_de_verao_nao_estoura_nem_duplica` falhar, a causa quase certa é `local_to_utc`: confira a detecção de hora inexistente antes de mexer no `generate`.

- [ ] **Step 7: Suíte e lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: suíte verde, lint limpo.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/scheduling.py backend/tests/test_scheduling.py
git commit -m "feat(aprazamento): horarios por ancora com rollover, primeira dose imediata e DST"
```

---

### Task 11: Persistência idempotente das tarefas e o worker de 48h

**Files:**
- Create: `src/back/app/models/task.py`
- Modify: `src/back/app/models/__init__.py` (reexportar `Task`, `TaskStatus`)
- Create: `src/back/app/services/tasks.py`
- Create: `src/back/app/workers/scheduler.py`
- Create: `src/back/alembic/versions/0007_tasks.py`
- Modify: `src/back/app/api/routes/prescriptions.py` (persistir as tarefas ao criar a prescrição)
- Modify: `src/back/app/services/hospitalization.py` (persistir as tarefas das cerimônias)
- Test: `src/back/tests/test_task_persistence.py`

**Interfaces:**
- Consumes: `SchedulingService.generate` (Task 10), `Prescription` (Task 9), `Hospitalization` (Task 8), `AuditService` (Task 4).
- Produces:
  - Model `Task` + enum `TaskStatus` (`pending|done|partial|not_done|cancelled`), com índice `(clinic_id, status, scheduled_for)`, UNIQUE parcial `(prescription_id, scheduled_for)` e FK composta `(prescription_id, clinic_id)`.
  - `app.services.tasks.TaskService.materialize(session, *, prescription, clinic, until) -> int` — gera e grava com `ON CONFLICT DO NOTHING`; devolve quantas linhas nasceram.
  - `app.workers.scheduler.extend_scheduling_window(session_factory, *, now, horizon_hours=48) -> int` — o job único; carrega cada prescrição ativa com `FOR UPDATE` e re-filtra dentro da transação.
  - `app.workers.scheduler.build_scheduler(session_factory) -> AsyncIOScheduler`.
  - Migração `0007`.

> Achado de engenharia 2 (spec §9): sem o índice único, "idempotência por SELECT-antes-de-INSERT" quebra em corrida. E no GCP o serviço que hospeda o scheduler precisa de `max-instances=1` com CPU always-allocated, ou o job toma `pg_advisory_lock` — Cloud Run escala e rodaria dois schedulers (tratar na semana 4).

- [ ] **Step 1: Escrever os testes que falham**

Crie `src/back/tests/test_task_persistence.py`:

```python
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import Task
from app.services.tasks import TaskService
from app.workers.scheduler import extend_scheduling_window
from tests.factories import make_clinic, make_hospitalization, make_prescription


async def _count(session, prescription_id) -> int:
    return await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(Task.prescription_id == prescription_id)
    )


async def test_materialize_grava_a_janela(session):
    clinic = await make_clinic(session)
    prescription = await make_prescription(
        session, clinic=clinic, starts_at=datetime.now(UTC), frequency_minutes=480
    )
    criadas = await TaskService.materialize(
        session, prescription=prescription, clinic=clinic,
        until=datetime.now(UTC) + timedelta(hours=48),
    )
    assert criadas >= 5
    assert await _count(session, prescription.id) == criadas


async def test_rodar_duas_vezes_nao_duplica(session):
    clinic = await make_clinic(session)
    prescription = await make_prescription(session, clinic=clinic, starts_at=datetime.now(UTC))
    until = datetime.now(UTC) + timedelta(hours=48)

    primeira = await TaskService.materialize(
        session, prescription=prescription, clinic=clinic, until=until
    )
    segunda = await TaskService.materialize(
        session, prescription=prescription, clinic=clinic, until=until
    )
    assert segunda == 0
    assert await _count(session, prescription.id) == primeira


async def test_job_estende_a_janela_sem_duplicar(session, db_session_factory):
    clinic = await make_clinic(session)
    prescription = await make_prescription(session, clinic=clinic, starts_at=datetime.now(UTC))
    await session.commit()

    primeira = await extend_scheduling_window(db_session_factory, now=datetime.now(UTC))
    segunda = await extend_scheduling_window(db_session_factory, now=datetime.now(UTC))
    assert primeira > 0
    assert segunda == 0


async def test_job_ignora_prescricao_suspensa(session, db_session_factory):
    clinic = await make_clinic(session)
    prescription = await make_prescription(
        session, clinic=clinic, starts_at=datetime.now(UTC), suspended_at=datetime.now(UTC)
    )
    await session.commit()
    assert await extend_scheduling_window(db_session_factory, now=datetime.now(UTC)) == 0


async def test_job_ignora_internacao_encerrada(session, db_session_factory):
    clinic = await make_clinic(session)
    hosp = await make_hospitalization(session, clinic=clinic, status="discharged")
    await make_prescription(
        session, clinic=clinic, hospitalization=hosp, starts_at=datetime.now(UTC)
    )
    await session.commit()
    assert await extend_scheduling_window(db_session_factory, now=datetime.now(UTC)) == 0


async def test_prescrever_pela_api_ja_cria_as_tarefas(client, session):
    from tests.factories import make_membership, make_user
    from tests.helpers import bearer, personal_token

    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona",
              "frequency_minutes": 480, "criticality": "normal", "details": {"drug": "dipirona"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert await _count(session, resp.json()["id"]) > 0
```

Acrescente ao `src/back/tests/conftest.py` a fixture que o job precisa (ele abre a própria sessão):

```python
@pytest.fixture
def db_session_factory(db_session):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        # O job roda dentro da MESMA transação do teste — o rollback do harness
        # continua limpando tudo no fim.
        yield db_session

    return _factory
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_task_persistence.py -q`
Expected: `ImportError: cannot import name 'Task' from 'app.models'`.

- [ ] **Step 3: Implementar o model Task**

Crie `src/back/app/models/task.py`:

```python
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.prescription import Criticality, PrescriptionCategory, _enum


class TaskStatus(StrEnum):
    pending = "pending"
    done = "done"
    partial = "partial"
    not_done = "not_done"
    cancelled = "cancelled"


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        sa.Index("ix_tasks_clinic_status_scheduled", "clinic_id", "status", "scheduled_for"),
        # Idempotência do aprazamento (regra 8 do contrato): a corrida entre a
        # criação da prescrição e o job de 48h é resolvida no banco, não em código.
        sa.Index(
            "uq_tasks_prescription_scheduled", "prescription_id", "scheduled_for",
            unique=True, postgresql_where=sa.text("prescription_id IS NOT NULL"),
        ),
        sa.ForeignKeyConstraint(
            ["prescription_id", "clinic_id"],
            ["prescriptions.id", "prescriptions.clinic_id"],
            name="fk_tasks_prescription_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    hospitalization_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("hospitalizations.id"), index=True
    )
    prescription_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, default=None)
    title: Mapped[str] = mapped_column(sa.Text)
    category: Mapped[PrescriptionCategory] = mapped_column(
        _enum(PrescriptionCategory, "prescription_category")
    )
    scheduled_for: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True))
    criticality: Mapped[Criticality] = mapped_column(_enum(Criticality, "criticality"))
    tolerance_minutes: Mapped[int] = mapped_column(sa.Integer)
    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus, "task_status"), default=TaskStatus.pending
    )
    executed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    executed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    retroactive: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    early: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    outcome_reason: Mapped[str | None] = mapped_column(sa.Text, default=None)
    values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    price_minor: Mapped[int | None] = mapped_column(sa.Integer, default=None)
```

- [ ] **Step 4: Implementar TaskService.materialize**

Crie `src/back/app/services/tasks.py`:

```python
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Clinic, Prescription, Task
from app.services.scheduling import SchedulingService


class TaskService:
    @staticmethod
    async def materialize(
        session: AsyncSession, *, prescription: Prescription, clinic: Clinic, until: datetime
    ) -> int:
        """Grava a janela de tarefas da prescrição. Idempotente pelo índice único
        parcial (prescription_id, scheduled_for): o segundo INSERT não faz nada."""
        candidates = SchedulingService.generate(prescription, clinic, until)
        if not candidates:
            return 0
        rows = [
            {
                "id": task.id,
                "clinic_id": task.clinic_id,
                "hospitalization_id": task.hospitalization_id,
                "prescription_id": task.prescription_id,
                "title": task.title,
                "category": task.category,
                "scheduled_for": task.scheduled_for,
                "criticality": task.criticality,
                "tolerance_minutes": task.tolerance_minutes,
                "status": "pending",
                "price_minor": task.price_minor,
            }
            for task in candidates
        ]
        stmt = (
            insert(Task)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["prescription_id", "scheduled_for"])
            .returning(Task.id)
        )
        created = list((await session.execute(stmt)).scalars())
        await session.flush()
        return len(created)
```

- [ ] **Step 5: Implementar o worker**

Crie `src/back/app/workers/scheduler.py`:

```python
from datetime import datetime, timedelta

import sqlalchemy as sa
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.models import Clinic, Hospitalization, Prescription
from app.services.tasks import TaskService

HORIZON_HOURS = 48


async def extend_scheduling_window(session_factory, *, now: datetime, horizon_hours: int = HORIZON_HOURS) -> int:
    """Job ÚNICO do sistema. Não existe verificador de atraso: 'atrasada' é
    computada na leitura (TaskService.display_state), então nada a fazer aqui."""
    until = now + timedelta(hours=horizon_hours)
    created = 0
    async with session_factory() as session:
        stmt = (
            sa.select(Prescription.id)
            .join(Hospitalization, Hospitalization.id == Prescription.hospitalization_id)
            .where(
                Prescription.suspended_at.is_(None),
                Prescription.kind != "prn",
                sa.or_(Prescription.ends_at.is_(None), Prescription.ends_at > now),
                Hospitalization.status == "active",
            )
        )
        for prescription_id in list((await session.execute(stmt)).scalars()):
            # FOR UPDATE serializa com POST /prescriptions/{id}/suspend (Task 12):
            # sem isso, o job pode ressuscitar tarefas de uma prescrição suspensa.
            locked = await session.scalar(
                sa.select(Prescription).where(Prescription.id == prescription_id).with_for_update()
            )
            if locked is None or locked.suspended_at is not None:
                continue
            hospitalization = await session.get(Hospitalization, locked.hospitalization_id)
            if hospitalization is None or hospitalization.status != "active":
                continue
            clinic = await session.get(Clinic, locked.clinic_id)
            created += await TaskService.materialize(
                session, prescription=locked, clinic=clinic, until=until
            )
        await session.commit()
    return created


def build_scheduler(session_factory) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        lambda: extend_scheduling_window(session_factory, now=datetime.now(UTC)),
        trigger="interval", hours=1, id="extend_scheduling_window", max_instances=1,
    )
    return scheduler
```

> **Deploy (semana 4):** Cloud Run escala horizontalmente e rodaria um scheduler por instância. O serviço que hospeda este job vai com `max-instances=1` e CPU always-allocated, ou o job passa a tomar `pg_advisory_lock` antes de rodar.

- [ ] **Step 6: Persistir ao prescrever**

Em `src/back/app/api/routes/prescriptions.py`, depois do `AuditService.record` e antes do `commit`:

```python
    clinic = await session.get(Clinic, auth.clinic_id)
    await TaskService.materialize(
        session, prescription=prescription, clinic=clinic,
        until=datetime.now(UTC) + timedelta(hours=48),
    )
```

Faça o mesmo em `HospitalizationService.create_default_prescriptions`, para cada cerimônia criada.

- [ ] **Step 7: Migração e verde**

Run: `uv run alembic revision --autogenerate -m "tasks"` → renomeie para `0007_tasks.py` (`revision="0007"`, `down_revision="0006"`). **Confira à mão** que o índice único parcial saiu com `postgresql_where` — o autogenerate às vezes o omite.

Run: `uv run pytest tests/test_task_persistence.py -q && uv run pytest -q && uv run ruff check .`
Expected: `6 passed` no arquivo, suíte verde, lint limpo.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/task.py backend/app/models/__init__.py backend/app/services/tasks.py backend/app/workers backend/alembic/versions/0007_tasks.py backend/app/api/routes/prescriptions.py backend/app/services/hospitalization.py backend/tests
git commit -m "feat(tarefas): persistencia idempotente da janela de 48h e worker unico"
```

---

### Task 12: Suspender, ajustar (titulação) e encerrar a internação cancelando o futuro

**Files:**
- Modify: `src/back/app/api/routes/prescriptions.py` (rotas `suspend` e `adjust`)
- Modify: `src/back/app/services/hospitalization.py` (cancelamento no `close`)
- Modify: `src/back/app/api/routes/hospitalizations.py` (guarda `confirm_pending_tasks`)
- Modify: `src/back/app/schemas/prescription.py` (`PrescriptionAdjust`)
- Test: `src/back/tests/test_prescription_lifecycle.py`

**Interfaces:**
- Consumes: `Prescription`/`Task` (Tasks 9, 11), `TaskService.materialize` (Task 11), `AuditService` (Task 4), `HospitalizationService.close` (Task 8).
- Produces:
  - `POST /api/v1/prescriptions/{id}/suspend` → 200 `PrescriptionOut`.
  - `POST /api/v1/prescriptions/{id}/adjust` → 201 `PrescriptionOut` (a nova versão, com `replaces_prescription_id`).
  - `app.services.tasks.TaskService.cancel_future(session, *, clinic_id, prescription_id=None, hospitalization_id=None, now) -> int`.
  - `POST /api/v1/hospitalizations/{id}/outcome` completo: conta pendentes, exige `confirm_pending_tasks`, cancela futuras.

> Achado clínico 4 (spec §9): titular fluidoterapia é rotina. Sem `adjust`, cada ajuste de taxa viraria um par suspender+criar sem vínculo, e a pergunta "qual a taxa atual e qual era antes?" ficaria sem resposta na auditoria.

- [ ] **Step 1: Escrever os testes que falham**

Crie `src/back/tests/test_prescription_lifecycle.py`:

```python
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import Prescription, Task
from app.workers.scheduler import extend_scheduling_window
from tests.factories import (
    make_clinic, make_hospitalization, make_membership, make_prescription, make_user,
)
from tests.helpers import bearer, personal_token


async def _vet(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    return clinic, membership


async def test_suspender_preserva_o_passado_e_cancela_o_futuro(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    created = (await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona",
              "frequency_minutes": 480, "duration_hours": 72, "criticality": "normal",
              "details": {"drug": "dipirona"}},
        headers=token,
    )).json()

    tarefas = list((await session.execute(
        sa.select(Task).where(Task.prescription_id == created["id"]).order_by(Task.scheduled_for)
    )).scalars())
    for tarefa in tarefas[:3]:
        tarefa.status = "done"
        tarefa.executed_at = datetime.now(UTC)
    await session.flush()

    resp = await client.post(f"/api/v1/prescriptions/{created['id']}/suspend", headers=token)
    assert resp.status_code == 200
    assert resp.json()["suspended_at"] is not None

    await session.refresh(tarefas[0])
    depois = list((await session.execute(
        sa.select(Task).where(Task.prescription_id == created["id"]).order_by(Task.scheduled_for)
    )).scalars())
    assert [t.status for t in depois[:3]] == ["done", "done", "done"]
    assert all(t.status == "cancelled" for t in depois[3:])


async def test_job_nao_ressuscita_tarefa_de_prescricao_suspensa(client, session, db_session_factory):
    clinic, membership = await _vet(session)
    prescription = await make_prescription(session, clinic=clinic, starts_at=datetime.now(UTC))
    token = bearer(personal_token(membership))
    await client.post(f"/api/v1/prescriptions/{prescription.id}/suspend", headers=token)
    await session.commit()

    criadas = await extend_scheduling_window(db_session_factory, now=datetime.now(UTC))
    assert criadas == 0
    pendentes = await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(
            Task.prescription_id == prescription.id, Task.status == "pending"
        )
    )
    assert pendentes == 0


async def test_ajustar_taxa_de_fluido_mantem_historico(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    original = (await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "continuous", "category": "fluids", "name": "Ringer Lactato",
              "frequency_minutes": 120, "criticality": "normal",
              "details": {"rate_ml_h": 60}},
        headers=token,
    )).json()

    ajustada = await client.post(
        f"/api/v1/prescriptions/{original['id']}/adjust",
        json={"details": {"rate_ml_h": 40}}, headers=token,
    )
    assert ajustada.status_code == 201
    nova = ajustada.json()
    assert nova["replaces_prescription_id"] == original["id"]
    assert nova["details"]["rate_ml_h"] == 40
    assert nova["suspended_at"] is None

    antiga = await session.get(Prescription, original["id"])
    assert antiga.suspended_at is not None

    futuras_antigas = await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(
            Task.prescription_id == antiga.id, Task.status == "pending"
        )
    )
    assert futuras_antigas == 0
    futuras_novas = await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(
            Task.prescription_id == uuid.UUID(nova["id"]), Task.status == "pending"
        )
    )
    assert futuras_novas > 0


async def test_alta_com_pendencias_exige_confirmacao(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona",
              "frequency_minutes": 480, "criticality": "normal", "details": {}},
        headers=token,
    )

    recusa = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        json={"outcome": "discharged"}, headers=token,
    )
    assert recusa.status_code == 409
    assert recusa.json()["error"]["code"] == "pending_tasks_confirmation_required"
    assert recusa.json()["error"]["params"]["pending"] > 0

    ok = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        json={"outcome": "discharged", "confirm_pending_tasks": True}, headers=token,
    )
    assert ok.status_code == 200
    restantes = await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(
            Task.hospitalization_id == hosp.id, Task.status == "pending"
        )
    )
    assert restantes == 0
```

Acrescente `import uuid` ao topo do arquivo de teste.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_prescription_lifecycle.py -q`
Expected: `404` nas rotas `suspend`/`adjust` (ainda não existem).

- [ ] **Step 3: Implementar o cancelamento em lote**

Acrescente a `src/back/app/services/tasks.py`:

```python
    @staticmethod
    async def cancel_future(
        session: AsyncSession, *, clinic_id: uuid.UUID, now: datetime,
        prescription_id: uuid.UUID | None = None,
        hospitalization_id: uuid.UUID | None = None,
    ) -> int:
        """Cancela tarefas pendentes futuras. Executadas nunca são tocadas —
        o passado do prontuário é imutável (ADR-0003)."""
        stmt = (
            sa.update(Task)
            .where(
                Task.clinic_id == clinic_id,
                Task.status == "pending",
                Task.scheduled_for > now,
            )
            .values(status="cancelled")
            .returning(Task.id)
        )
        if prescription_id is not None:
            stmt = stmt.where(Task.prescription_id == prescription_id)
        if hospitalization_id is not None:
            stmt = stmt.where(Task.hospitalization_id == hospitalization_id)
        return len(list((await session.execute(stmt)).scalars()))
```

- [ ] **Step 4: Implementar suspend e adjust**

Acrescente a `src/back/app/schemas/prescription.py`:

```python
class PrescriptionAdjust(BaseModel):
    """Titulação: o que muda na nova versão. O que não vier é herdado da anterior."""
    name: str | None = None
    details: dict[str, Any] | None = None
    frequency_minutes: int | None = Field(default=None, ge=5)
    criticality: Literal["normal", "critical"] | None = None
    tolerance_minutes: int | None = Field(default=None, ge=1)
    price_minor: int | None = Field(default=None, ge=0)
    reason: str | None = None
```

Acrescente a `src/back/app/api/routes/prescriptions.py`:

```python
@router.post("/prescriptions/{prescription_id}/suspend", response_model=PrescriptionOut)
async def suspend(
    prescription_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PrescriptionOut:
    await get_tenant_obj(session, Prescription, prescription_id, auth.clinic_id)
    # FOR UPDATE serializa com o job de 48h: sem isso o worker pode inserir
    # tarefas futuras logo depois do cancelamento.
    prescription = await session.scalar(
        sa.select(Prescription).where(Prescription.id == prescription_id).with_for_update()
    )
    before = AuditService.snapshot(prescription)
    now = datetime.now(UTC)
    prescription.suspended_at = now
    prescription.suspended_by = actor.membership_id
    cancelled = await TaskService.cancel_future(
        session, clinic_id=auth.clinic_id, prescription_id=prescription.id, now=now
    )
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="prescription_suspended",
        entity_type="prescription", entity_id=prescription.id,
        before=before, after=AuditService.snapshot(prescription),
        extra={"cancelled_tasks": cancelled},
    )
    await session.commit()
    return PrescriptionOut.model_validate(prescription)


@router.post("/prescriptions/{prescription_id}/adjust", response_model=PrescriptionOut, status_code=201)
async def adjust(
    prescription_id: uuid.UUID,
    payload: PrescriptionAdjust,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PrescriptionOut:
    await get_tenant_obj(session, Prescription, prescription_id, auth.clinic_id)
    previous = await session.scalar(
        sa.select(Prescription).where(Prescription.id == prescription_id).with_for_update()
    )
    now = datetime.now(UTC)
    changes = payload.model_dump(exclude_unset=True, exclude={"reason"})

    replacement = Prescription(
        clinic_id=previous.clinic_id,
        hospitalization_id=previous.hospitalization_id,
        kind=previous.kind,
        category=previous.category,
        name=changes.get("name", previous.name),
        details=changes.get("details", previous.details),
        frequency_minutes=changes.get("frequency_minutes", previous.frequency_minutes),
        duration_hours=previous.duration_hours,
        criticality=changes.get("criticality", previous.criticality),
        tolerance_minutes=changes.get("tolerance_minutes", previous.tolerance_minutes),
        first_dose_now=False,
        is_controlled=previous.is_controlled,
        max_doses_24h=previous.max_doses_24h,
        min_interval_minutes=previous.min_interval_minutes,
        price_minor=changes.get("price_minor", previous.price_minor),
        starts_at=now,
        ends_at=previous.ends_at,
        replaces_prescription_id=previous.id,
        created_by=actor.membership_id,
    )
    session.add(replacement)

    before = AuditService.snapshot(previous)
    previous.suspended_at = now
    previous.suspended_by = actor.membership_id
    cancelled = await TaskService.cancel_future(
        session, clinic_id=auth.clinic_id, prescription_id=previous.id, now=now
    )
    await session.flush()

    clinic = await session.get(Clinic, auth.clinic_id)
    await TaskService.materialize(
        session, prescription=replacement, clinic=clinic, until=now + timedelta(hours=48)
    )
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="prescription_adjusted",
        entity_type="prescription", entity_id=replacement.id,
        before=before, after=AuditService.snapshot(replacement),
        extra={"replaces": str(previous.id), "cancelled_tasks": cancelled, "reason": payload.reason},
    )
    await session.commit()
    return PrescriptionOut.model_validate(replacement)
```

- [ ] **Step 5: Completar o desfecho da internação**

Em `src/back/app/api/routes/hospitalizations.py`, dentro de `close`, antes de chamar o serviço:

```python
    now = datetime.now(UTC)
    pending = await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(
            Task.hospitalization_id == hospitalization.id,
            Task.status == "pending",
            Task.scheduled_for > now,
        )
    )
    if pending and not payload.confirm_pending_tasks:
        raise AppError("pending_tasks_confirmation_required", 409, pending=pending)
```

E depois de `HospitalizationService.close(...)`:

```python
    cancelled = await TaskService.cancel_future(
        session, clinic_id=auth.clinic_id, hospitalization_id=hospitalization.id, now=now
    )
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="tasks_cancelled_on_outcome",
        entity_type="hospitalization", entity_id=hospitalization.id,
        extra={"cancelled_tasks": cancelled},
    )
```

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/test_prescription_lifecycle.py -q && uv run pytest -q && uv run ruff check .`
Expected: `4 passed` no arquivo, suíte verde, lint limpo.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes backend/app/services backend/app/schemas/prescription.py backend/tests
git commit -m "feat(prescricao): suspensao, titulacao versionada e cancelamento no desfecho"
```

---
### Task 13: Fila de tarefas por janela e o estado exibido

**Files:**
- Modify: `src/back/app/services/tasks.py` (acrescentar `display_state`)
- Create: `src/back/app/schemas/task.py`
- Create: `src/back/app/api/routes/tasks.py`
- Modify: `src/back/app/api/routes/hospitalizations.py` (preencher `tasks` no detalhe)
- Modify: `src/back/app/main.py` (incluir o router)
- Test: `src/back/tests/test_task_queue.py`

**Interfaces:**
- Consumes: `Task`/`TaskStatus` (Task 11), `Clinic.timezone` (Task 3), deps (Tasks 5–6).
- Produces:
  - `TaskService.display_state(task: Task, now: datetime) -> str` — assinatura EXATA do brief; devolve `on_time | due | overdue` para `pending`, ou o próprio status.
  - `TaskService.default_window(clinic, now) -> tuple[datetime, datetime]` — de `now` a `now + 12h`.
  - `app.schemas.task.TaskOut` — inclui `display_state` calculado.
  - `GET /api/v1/tasks?from=&to=&limit=` → `Page[TaskOut]` ordenado por `scheduled_for`.
  - `GET /api/v1/hospitalizations/{id}` agora traz `tasks` da janela.

> Achado clínico 9 (spec §9): `due=today` esconderia as tarefas de 02h e 04h do plantonista das 22h — justamente as âncoras da madrugada. Por isso a janela é explícita, e o default (`agora → +12h`) cobre um turno inteiro.

- [ ] **Step 1: Escrever os testes que falham**

Crie `src/back/tests/test_task_queue.py`:

```python
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Task
from app.services.tasks import TaskService
from tests.factories import (
    make_clinic, make_hospitalization, make_membership, make_prescription, make_user,
)
from tests.helpers import bearer, personal_token

SP = ZoneInfo("America/Sao_Paulo")


def _task(**overrides) -> Task:
    values = {
        "title": "Dipirona", "category": "medication", "criticality": "normal",
        "tolerance_minutes": 60, "status": "pending",
        "scheduled_for": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return Task(**values)


def test_display_state_antes_do_horario():
    task = _task()
    assert TaskService.display_state(task, datetime(2026, 9, 1, 11, 59, tzinfo=UTC)) == "on_time"


def test_display_state_exatamente_no_horario():
    task = _task()
    assert TaskService.display_state(task, datetime(2026, 9, 1, 12, 0, tzinfo=UTC)) == "due"


def test_display_state_no_limite_da_tolerancia():
    task = _task()
    assert TaskService.display_state(task, datetime(2026, 9, 1, 13, 0, tzinfo=UTC)) == "due"


def test_display_state_um_minuto_depois_da_tolerancia():
    task = _task()
    assert TaskService.display_state(task, datetime(2026, 9, 1, 13, 1, tzinfo=UTC)) == "overdue"


def test_display_state_critica_tem_janela_menor():
    task = _task(criticality="critical", tolerance_minutes=30)
    assert TaskService.display_state(task, datetime(2026, 9, 1, 12, 30, tzinfo=UTC)) == "due"
    assert TaskService.display_state(task, datetime(2026, 9, 1, 12, 31, tzinfo=UTC)) == "overdue"


def test_display_state_de_status_finalizado_e_o_proprio_status():
    agora = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
    for status in ("done", "partial", "not_done", "cancelled"):
        assert TaskService.display_state(_task(status=status), agora) == status


async def test_fila_por_janela_explicita(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    hosp = await make_hospitalization(session, clinic=clinic)
    inicio = datetime.now(UTC)
    prescription = await make_prescription(
        session, clinic=clinic, hospitalization=hosp, starts_at=inicio, frequency_minutes=60
    )
    from app.services.tasks import TaskService as TS
    await TS.materialize(
        session, prescription=prescription, clinic=clinic, until=inicio + timedelta(hours=24)
    )
    await session.flush()

    resp = await client.get(
        f"/api/v1/tasks?from={inicio.isoformat()}&to={(inicio + timedelta(hours=3)).isoformat()}",
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    itens = resp.json()["items"]
    assert 3 <= len(itens) <= 4
    assert itens == sorted(itens, key=lambda item: item["scheduled_for"])
    assert itens[0]["display_state"] in ("on_time", "due")


async def test_plantao_noturno_ve_as_tarefas_da_madrugada(client, session):
    """Às 22h a janela default (12h) precisa alcançar as âncoras de 02h e 04h."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    hosp = await make_hospitalization(session, clinic=clinic)

    agora_local = datetime.now(SP).replace(hour=22, minute=0, second=0, microsecond=0)
    agora = agora_local.astimezone(UTC)
    for hora in (2, 4):
        alvo = (agora_local + timedelta(days=1)).replace(hour=hora, minute=0)
        session.add(Task(
            clinic_id=clinic.id, hospitalization_id=hosp.id, title=f"Madrugada {hora}h",
            category="monitoring", scheduled_for=alvo.astimezone(UTC),
            criticality="normal", tolerance_minutes=60, status="pending",
        ))
    await session.flush()

    resp = await client.get(
        f"/api/v1/tasks?from={agora.isoformat()}&to={(agora + timedelta(hours=12)).isoformat()}",
        headers=bearer(personal_token(membership)),
    )
    titulos = [item["title"] for item in resp.json()["items"]]
    assert "Madrugada 2h" in titulos
    assert "Madrugada 4h" in titulos


async def test_janela_default_sao_12_horas(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    hosp = await make_hospitalization(session, clinic=clinic)
    agora = datetime.now(UTC)
    session.add(Task(
        clinic_id=clinic.id, hospitalization_id=hosp.id, title="Dentro",
        category="care", scheduled_for=agora + timedelta(hours=6),
        criticality="normal", tolerance_minutes=60, status="pending",
    ))
    session.add(Task(
        clinic_id=clinic.id, hospitalization_id=hosp.id, title="Fora",
        category="care", scheduled_for=agora + timedelta(hours=30),
        criticality="normal", tolerance_minutes=60, status="pending",
    ))
    await session.flush()

    itens = (await client.get("/api/v1/tasks", headers=bearer(personal_token(membership)))).json()["items"]
    titulos = [item["title"] for item in itens]
    assert "Dentro" in titulos
    assert "Fora" not in titulos


async def test_detalhe_da_internacao_traz_tarefas(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona",
              "frequency_minutes": 480, "criticality": "normal", "details": {}},
        headers=token,
    )
    detail = (await client.get(f"/api/v1/hospitalizations/{hosp.id}", headers=token)).json()
    assert len(detail["tasks"]) > 0
    assert "display_state" in detail["tasks"][0]


async def test_isolamento_de_tenant_na_fila(client, session):
    clinic_a = await make_clinic(session, slug="a")
    clinic_b = await make_clinic(session, slug="b")
    user = await make_user(session)
    membership_a = await make_membership(session, clinic=clinic_a, user=user, role="tech")
    hosp_b = await make_hospitalization(session, clinic=clinic_b)
    session.add(Task(
        clinic_id=clinic_b.id, hospitalization_id=hosp_b.id, title="Da clínica B",
        category="care", scheduled_for=datetime.now(UTC) + timedelta(hours=1),
        criticality="normal", tolerance_minutes=60, status="pending",
    ))
    await session.flush()

    itens = (await client.get("/api/v1/tasks", headers=bearer(personal_token(membership_a)))).json()["items"]
    assert itens == []
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_task_queue.py -q`
Expected: `AttributeError: type object 'TaskService' has no attribute 'display_state'`.

- [ ] **Step 3: Implementar display_state e a janela default**

Acrescente a `src/back/app/services/tasks.py`:

```python
    @staticmethod
    def display_state(task: Task, now: datetime) -> str:
        """'Atrasada' NUNCA é persistida (spec §2): é derivada na leitura, para
        que board e ficha jamais divirjam — o bug fatal do concorrente."""
        if task.status != TaskStatus.pending:
            return str(task.status)
        if now < task.scheduled_for:
            return "on_time"
        if now <= task.scheduled_for + timedelta(minutes=task.tolerance_minutes):
            return "due"
        return "overdue"

    @staticmethod
    def default_window(clinic: Clinic, now: datetime) -> tuple[datetime, datetime]:
        """Um turno inteiro à frente: às 22h o plantonista precisa enxergar 02h e 04h."""
        return now, now + timedelta(hours=12)
```

- [ ] **Step 4: Implementar o schema e o router**

Crie `src/back/app/schemas/task.py`:

```python
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hospitalization_id: uuid.UUID
    prescription_id: uuid.UUID | None
    title: str
    category: str
    scheduled_for: datetime
    criticality: str
    tolerance_minutes: int
    status: str
    display_state: str
    executed_at: datetime | None
    executed_by: uuid.UUID | None
    retroactive: bool
    early: bool
    outcome_reason: str | None
    values: dict[str, Any] | None
    price_minor: int | None

    @classmethod
    def from_task(cls, task: Any, now: datetime) -> "TaskOut":
        from app.services.tasks import TaskService

        data = {field: getattr(task, field) for field in cls.model_fields if field != "display_state"}
        return cls(**data, display_state=TaskService.display_state(task, now))
```

Crie `src/back/app/api/routes/tasks.py`:

```python
from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_session
from app.models import Clinic, Task
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.schemas.task import TaskOut
from app.services.tasks import TaskService

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("", response_model=Page[TaskOut])
async def list_tasks(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> Page[TaskOut]:
    now = datetime.now(UTC)
    clinic = await session.get(Clinic, auth.clinic_id)
    default_from, default_to = TaskService.default_window(clinic, now)
    window_start = from_ or default_from
    window_end = to or default_to

    rows = list((await session.execute(
        sa.select(Task)
        .where(
            Task.clinic_id == auth.clinic_id,
            Task.scheduled_for >= window_start,
            Task.scheduled_for <= window_end,
            Task.status != "cancelled",
        )
        .order_by(Task.scheduled_for.asc())
        .limit(limit)
    )).scalars())
    return Page[TaskOut](items=[TaskOut.from_task(row, now) for row in rows], next_cursor=None)
```

Registre o router em `create_app()`.

- [ ] **Step 5: Preencher `tasks` no detalhe da internação**

Em `src/back/app/api/routes/hospitalizations.py`, dentro de `detail`:

```python
    now = datetime.now(UTC)
    clinic = await session.get(Clinic, auth.clinic_id)
    window_start, window_end = TaskService.default_window(clinic, now)
    tasks = list((await session.execute(
        sa.select(Task)
        .where(
            Task.hospitalization_id == hospitalization.id,
            Task.scheduled_for >= window_start - timedelta(hours=12),
            Task.scheduled_for <= window_end,
            Task.status != "cancelled",
        )
        .order_by(Task.scheduled_for)
    )).scalars())
```

e passe `tasks=[TaskOut.from_task(row, now) for row in tasks]` ao `HospitalizationDetail`.

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/test_task_queue.py -q && uv run pytest -q && uv run ruff check .`
Expected: `11 passed` no arquivo, suíte verde, lint limpo.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/tasks.py backend/app/schemas/task.py backend/app/api/routes backend/app/main.py backend/tests/test_task_queue.py
git commit -m "feat(tarefas): fila por janela explicita e estado derivado na leitura"
```

---

### Task 14: Execução de tarefas — transição atômica, janela nos dois lados e guardrails PRN

**Files:**
- Modify: `src/back/app/services/tasks.py` (`execute`, `mark_not_done`, `check_prn_guardrails`)
- Modify: `src/back/app/schemas/task.py` (`TaskExecute`, `TaskNotDone`, `TaskAdHoc`)
- Modify: `src/back/app/api/routes/tasks.py` (três rotas)
- Test: `src/back/tests/test_task_execution.py`

**Interfaces:**
- Consumes: `Task`/`TaskStatus` (Task 11), `Prescription` (Task 9), `get_operator`/`ActorInfo` (Tasks 5–6), `AuditService` (Task 4), `AppError` (Task 1).
- Produces:
  - `POST /api/v1/tasks/{id}/execute` · `POST /api/v1/tasks/{id}/not-done` · `POST /api/v1/tasks/ad-hoc`.
  - `TaskService.transition(session, *, task_id, clinic_id, values) -> Task` — o UPDATE condicional atômico; `None` → `AppError("task_already_processed", 409)`.
  - `TaskService.check_prn_guardrails(session, *, prescription, now) -> dict | None` — devolve o motivo da violação ou `None`.

> É a task mais crítica do plano. Achado de engenharia 1 (spec §9): sem transição atômica, painel e app podem baixar a mesma dose e o sistema registra as duas sem piar — risco de dose dupla. Achado clínico 8: a janela ISMP vale **nos dois lados**; executar cedo demais é erro de medicação tanto quanto atrasar.

- [ ] **Step 1: Escrever os testes que falham**

Crie `src/back/tests/test_task_execution.py`:

```python
import asyncio
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import AuditEntry, Task
from tests.factories import (
    make_clinic, make_hospitalization, make_membership, make_prescription, make_user,
)
from tests.helpers import bearer, personal_token


async def _cenario(session, *, scheduled_for=None, tolerance_minutes=60, criticality="normal"):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(
        session, clinic=clinic, user=user, role="tech",
        license_number="9876", license_authority="CRMV-SP",
    )
    hosp = await make_hospitalization(session, clinic=clinic)
    task = Task(
        clinic_id=clinic.id, hospitalization_id=hosp.id, title="Dipirona",
        category="medication", scheduled_for=scheduled_for or datetime.now(UTC),
        criticality=criticality, tolerance_minutes=tolerance_minutes, status="pending",
        price_minor=1800,
    )
    session.add(task)
    await session.flush()
    return clinic, membership, hosp, task


async def test_executar_registra_autor_e_registro_profissional(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"values": {"note": "sem intercorrência"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    assert resp.json()["executed_at"] is not None

    entry = (await session.execute(
        sa.select(AuditEntry).where(AuditEntry.action == "task_executed").order_by(AuditEntry.id.desc())
    )).scalars().first()
    assert entry.actor_license == "9876"
    assert entry.actor_license_authority == "CRMV-SP"


async def test_execucao_concorrente_uma_ganha_outra_recebe_409(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    await session.commit()
    headers = bearer(personal_token(membership))

    primeira, segunda = await asyncio.gather(
        client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers),
        client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers),
    )
    codigos = sorted([primeira.status_code, segunda.status_code])
    assert codigos == [200, 409]
    perdedora = primeira if primeira.status_code == 409 else segunda
    assert perdedora.json()["error"]["code"] == "task_already_processed"


async def test_executar_tarefa_ja_finalizada_e_409(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))
    await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    de_novo = await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    assert de_novo.status_code == 409


async def test_retroativo_exige_hora_real_do_procedimento(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))

    sem_hora = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"retroactive": True}, headers=headers
    )
    assert sem_hora.status_code == 422

    realizada_em = datetime.now(UTC) - timedelta(minutes=40)
    com_hora = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"retroactive": True, "performed_at": realizada_em.isoformat()}, headers=headers,
    )
    assert com_hora.status_code == 200
    assert com_hora.json()["retroactive"] is True

    entry = (await session.execute(
        sa.select(AuditEntry).where(AuditEntry.action == "task_executed").order_by(AuditEntry.id.desc())
    )).scalars().first()
    # Os DOIS instantes ficam registrados: quando foi feito e quando foi apontado.
    assert entry.payload["extra"]["performed_at"] is not None
    assert entry.payload["extra"]["recorded_at"] is not None


async def test_execucao_precoce_exige_confirmacao(client, session):
    daqui_a_muito = datetime.now(UTC) + timedelta(hours=5)
    clinic, membership, hosp, task = await _cenario(session, scheduled_for=daqui_a_muito)
    headers = bearer(personal_token(membership))

    sem_confirmar = await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    assert sem_confirmar.status_code == 409
    assert sem_confirmar.json()["error"]["code"] == "early_confirmation_required"

    confirmando = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"confirm_early": True}, headers=headers
    )
    assert confirmando.status_code == 200
    assert confirmando.json()["early"] is True


async def test_parcial_exige_dose_administrada(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))

    sem_dose = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"partial": True}, headers=headers
    )
    assert sem_dose.status_code == 422

    com_dose = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"partial": True, "values": {"dose_given": "metade"}}, headers=headers,
    )
    assert com_dose.status_code == 200
    assert com_dose.json()["status"] == "partial"


async def test_nao_realizada_com_motivo(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/not-done", json={"reason": "fasting"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_done"
    assert resp.json()["outcome_reason"] == "fasting"


async def test_motivo_outro_exige_detalhe(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))
    sem_detalhe = await client.post(
        f"/api/v1/tasks/{task.id}/not-done", json={"reason": "other"}, headers=headers
    )
    assert sem_detalhe.status_code == 422

    com_detalhe = await client.post(
        f"/api/v1/tasks/{task.id}/not-done",
        json={"reason": "other", "values": {"outcome_detail": "acesso venoso perdido"}},
        headers=headers,
    )
    assert com_detalhe.status_code == 200


async def test_prn_respeita_intervalo_minimo_com_override(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    prn = await make_prescription(
        session, clinic=clinic, hospitalization=hosp, kind="prn", frequency_minutes=None,
        name="Metadona", max_doses_24h=4, min_interval_minutes=240,
    )
    headers = bearer(personal_token(membership))

    primeira = await client.post(
        "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
    )
    assert primeira.status_code == 201

    cedo_demais = await client.post(
        "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
    )
    assert cedo_demais.status_code == 409
    assert cedo_demais.json()["error"]["code"] == "prn_guardrail"
    assert cedo_demais.json()["error"]["params"]["rule"] == "min_interval_minutes"

    # Aviso, nunca bloqueio duro: o vet decide e o override fica auditado.
    com_override = await client.post(
        "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id), "override": True},
        headers=headers,
    )
    assert com_override.status_code == 201
    entry = (await session.execute(
        sa.select(AuditEntry).where(AuditEntry.action == "task_executed").order_by(AuditEntry.id.desc())
    )).scalars().first()
    assert entry.payload["extra"]["override"] is True


async def test_prn_respeita_maximo_em_24h(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    prn = await make_prescription(
        session, clinic=clinic, hospitalization=hosp, kind="prn", frequency_minutes=None,
        name="Metadona", max_doses_24h=2, min_interval_minutes=None,
    )
    headers = bearer(personal_token(membership))

    for _ in range(2):
        assert (await client.post(
            "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
        )).status_code == 201

    terceira = await client.post(
        "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
    )
    assert terceira.status_code == 409
    assert terceira.json()["error"]["params"]["rule"] == "max_doses_24h"


async def test_evento_avulso_com_titulo_livre(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    resp = await client.post(
        "/api/v1/tasks/ad-hoc",
        json={"hospitalization_id": str(hosp.id), "title": "Episódio de vômito",
              "category": "care", "values": {"note": "bilioso, pequeno volume"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "done"
    assert resp.json()["title"] == "Episódio de vômito"


async def test_modo_estacao_sem_operator_token_e_403(client, session):
    from tests.helpers import station_token

    clinic, membership, hosp, task = await _cenario(session)
    clinic.station_key_hash = None
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={},
        headers=bearer(station_token(clinic)),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "operator_required"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_task_execution.py -q`
Expected: `404` nas três rotas.

- [ ] **Step 3: Implementar a transição atômica**

Acrescente a `src/back/app/services/tasks.py`:

```python
    @staticmethod
    async def transition(
        session: AsyncSession, *, task_id: uuid.UUID, clinic_id: uuid.UUID, values: dict
    ) -> Task:
        """Transição ATÔMICA (brief): um único UPDATE condicional. Se outra pessoa
        baixou a tarefa primeiro, zero linhas voltam e o segundo recebe 409 — sem
        isso, painel e app registrariam a mesma dose duas vezes."""
        stmt = (
            sa.update(Task)
            .where(Task.id == task_id, Task.clinic_id == clinic_id, Task.status == "pending")
            .values(**values)
            .returning(Task)
        )
        task = (await session.execute(stmt)).scalar_one_or_none()
        if task is None:
            raise AppError("task_already_processed", 409)
        return task

    @staticmethod
    async def check_prn_guardrails(
        session: AsyncSession, *, prescription: Prescription, now: datetime
    ) -> dict | None:
        """Aviso, nunca bloqueio duro (pesquisa §4: fricção gera workaround que
        falsifica o registro). Quem decide é o profissional; o sistema audita."""
        if prescription.min_interval_minutes:
            ultima = await session.scalar(
                sa.select(sa.func.max(Task.executed_at)).where(
                    Task.prescription_id == prescription.id,
                    Task.status.in_(["done", "partial"]),
                )
            )
            if ultima is not None:
                minutos = (now - ultima).total_seconds() / 60
                if minutos < prescription.min_interval_minutes:
                    return {
                        "rule": "min_interval_minutes",
                        "required_minutes": prescription.min_interval_minutes,
                        "elapsed_minutes": int(minutos),
                    }
        if prescription.max_doses_24h:
            doses = await session.scalar(
                sa.select(sa.func.count()).select_from(Task).where(
                    Task.prescription_id == prescription.id,
                    Task.status.in_(["done", "partial"]),
                    Task.executed_at >= now - timedelta(hours=24),
                )
            )
            if doses >= prescription.max_doses_24h:
                return {
                    "rule": "max_doses_24h",
                    "max": prescription.max_doses_24h,
                    "given": doses,
                }
        return None
```

- [ ] **Step 4: Implementar os schemas de execução**

Acrescente a `src/back/app/schemas/task.py`:

```python
class TaskExecute(BaseModel):
    values: dict[str, Any] | None = None
    retroactive: bool = False
    performed_at: datetime | None = None
    partial: bool = False
    confirm_early: bool = False

    @model_validator(mode="after")
    def check(self) -> "TaskExecute":
        if self.retroactive and self.performed_at is None:
            raise ValueError("performed_at é obrigatório quando retroactive=true")
        if self.partial and not (self.values or {}).get("dose_given"):
            raise ValueError("execução parcial exige values.dose_given")
        return self


class TaskNotDone(BaseModel):
    reason: Literal["refused", "fasting", "unavailable", "vet_order", "other"]
    values: dict[str, Any] | None = None

    @model_validator(mode="after")
    def check(self) -> "TaskNotDone":
        if self.reason == "other" and not (self.values or {}).get("outcome_detail"):
            raise ValueError("motivo 'other' exige values.outcome_detail")
        return self


class TaskAdHoc(BaseModel):
    prescription_id: uuid.UUID | None = None
    hospitalization_id: uuid.UUID | None = None
    title: str | None = None
    category: str = "care"
    values: dict[str, Any] | None = None
    override: bool = False

    @model_validator(mode="after")
    def check(self) -> "TaskAdHoc":
        if self.prescription_id is None and not (self.hospitalization_id and self.title):
            raise ValueError("informe prescription_id (PRN) ou hospitalization_id + title")
        return self
```

- [ ] **Step 5: Implementar as três rotas**

Acrescente a `src/back/app/api/routes/tasks.py`:

```python
@router.post("/{task_id}/execute", response_model=TaskOut)
async def execute(
    task_id: uuid.UUID,
    payload: TaskExecute,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskOut:
    now = datetime.now(UTC)
    current = await get_tenant_obj(session, Task, task_id, auth.clinic_id)

    # A janela ISMP vale nos DOIS lados: adiantar dose é erro como atrasar.
    early = now < current.scheduled_for - timedelta(minutes=current.tolerance_minutes)
    if early and not payload.confirm_early:
        raise AppError("early_confirmation_required", 409,
                       scheduled_for=current.scheduled_for.isoformat())

    executed_at = payload.performed_at or now
    task = await TaskService.transition(
        session, task_id=task_id, clinic_id=auth.clinic_id,
        values={
            "status": "partial" if payload.partial else "done",
            "executed_at": executed_at,
            "executed_by": actor.membership_id,
            "retroactive": payload.retroactive,
            "early": early,
            "values": payload.values,
        },
    )
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="task_executed",
        entity_type="task", entity_id=task.id, after=AuditService.snapshot(task),
        extra={
            "performed_at": executed_at.isoformat(),
            "recorded_at": now.isoformat(),
            "early": early,
        },
    )
    await session.commit()
    return TaskOut.from_task(task, now)


@router.post("/{task_id}/not-done", response_model=TaskOut)
async def not_done(
    task_id: uuid.UUID,
    payload: TaskNotDone,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskOut:
    now = datetime.now(UTC)
    await get_tenant_obj(session, Task, task_id, auth.clinic_id)
    task = await TaskService.transition(
        session, task_id=task_id, clinic_id=auth.clinic_id,
        values={
            "status": "not_done", "executed_at": now, "executed_by": actor.membership_id,
            "outcome_reason": payload.reason, "values": payload.values,
        },
    )
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="task_not_done",
        entity_type="task", entity_id=task.id, after=AuditService.snapshot(task),
        extra={"reason": payload.reason},
    )
    await session.commit()
    return TaskOut.from_task(task, now)


@router.post("/ad-hoc", response_model=TaskOut, status_code=201)
async def ad_hoc(
    payload: TaskAdHoc,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskOut:
    now = datetime.now(UTC)
    guardrail = None
    if payload.prescription_id is not None:
        prescription = await get_tenant_obj(
            session, Prescription, payload.prescription_id, auth.clinic_id
        )
        if prescription.kind != "prn":
            raise AppError("validation_error", 422, field="prescription_id")
        guardrail = await TaskService.check_prn_guardrails(
            session, prescription=prescription, now=now
        )
        if guardrail is not None and not payload.override:
            raise AppError("prn_guardrail", 409, **guardrail)
        task = Task(
            clinic_id=auth.clinic_id, hospitalization_id=prescription.hospitalization_id,
            prescription_id=prescription.id, title=prescription.name,
            category=prescription.category, price_minor=prescription.price_minor,
            criticality=prescription.criticality,
            tolerance_minutes=prescription.tolerance_minutes,
        )
    else:
        await get_tenant_obj(session, Hospitalization, payload.hospitalization_id, auth.clinic_id)
        task = Task(
            clinic_id=auth.clinic_id, hospitalization_id=payload.hospitalization_id,
            title=payload.title, category=payload.category,
            criticality="normal", tolerance_minutes=60,
        )

    task.scheduled_for = now
    task.status = "done"
    task.executed_at = now
    task.executed_by = actor.membership_id
    task.values = payload.values
    session.add(task)
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="task_executed",
        entity_type="task", entity_id=task.id, after=AuditService.snapshot(task),
        extra={
            "ad_hoc": True,
            "performed_at": now.isoformat(),
            "recorded_at": now.isoformat(),
            "override": payload.override and guardrail is not None,
            "guardrail": guardrail,
        },
    )
    await session.commit()
    return TaskOut.from_task(task, now)
```

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/test_task_execution.py -q`
Expected: `12 passed`.

> Se `test_execucao_concorrente_uma_ganha_outra_recebe_409` for instável, a causa é o harness compartilhar uma transação entre as duas chamadas: rode esse teste com um `client` que abra sessões independentes (fixture `client_isolated` com engine própria) em vez de relaxar a asserção — a garantia atômica é o ponto da task.

- [ ] **Step 7: Suíte, lint e commit**

```bash
uv run pytest -q && uv run ruff check .
git add backend/app/services/tasks.py backend/app/schemas/task.py backend/app/api/routes/tasks.py backend/tests/test_task_execution.py
git commit -m "feat(execucao): transicao atomica, janela nos dois lados e guardrails de PRN"
```

---

### Task 15: Board e trilha de auditoria paginada

**Files:**
- Create: `src/back/app/schemas/board.py`
- Create: `src/back/app/api/routes/board.py`
- Create: `src/back/app/api/routes/audit.py`
- Modify: `src/back/app/main.py` (incluir os routers)
- Test: `src/back/tests/test_board_audit.py`

**Interfaces:**
- Consumes: `TaskService.display_state` (Task 13), `Hospitalization`/`Patient`/`Kennel` (Tasks 7–8), `AuditEntry` (Task 4), `Page`/`paginate` (Task 7).
- Produces:
  - `GET /api/v1/board` → `{"totals": {...}, "rows": [...]}`.
  - `GET /api/v1/audit?entity_type=&entity_id=&limit=&cursor=` → `Page[AuditEntryOut]`, `id DESC`; `tech` → 403.

> O board **não** tem consulta própria de estado: ele chama o mesmo `display_state` da fila. É essa decisão que evita o bug fatal do concorrente — paciente sumindo do painel enquanto a ficha o mostra.

- [ ] **Step 1: Escrever os testes que falham**

Crie `src/back/tests/test_board_audit.py`:

```python
from datetime import UTC, datetime, timedelta

from app.models import Task
from tests.factories import (
    make_clinic, make_hospitalization, make_kennel, make_membership, make_patient, make_user,
)
from tests.helpers import bearer, personal_token


async def _clinica_com_tres_estados(session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    kennel = await make_kennel(session, clinic=clinic, name="UTI 03")
    agora = datetime.now(UTC)

    cenarios = [
        ("Thor", agora + timedelta(hours=2), 60, "normal"),      # on_time
        ("Nina", agora - timedelta(minutes=10), 60, "normal"),   # due
        ("Mel", agora - timedelta(hours=3), 30, "critical"),     # overdue + crítica
    ]
    for nome, quando, tolerancia, criticidade in cenarios:
        patient = await make_patient(session, clinic=clinic, name=nome)
        hosp = await make_hospitalization(
            session, clinic=clinic, patient=patient, membership=membership
        )
        hosp.kennel_id = kennel.id
        session.add(Task(
            clinic_id=clinic.id, hospitalization_id=hosp.id, title=f"Tarefa de {nome}",
            category="medication", scheduled_for=quando, criticality=criticidade,
            tolerance_minutes=tolerancia, status="pending",
        ))
    await session.flush()
    return clinic, membership


async def test_board_agrupa_por_internacao_com_contadores(client, session):
    clinic, membership = await _clinica_com_tres_estados(session)
    resp = await client.get("/api/v1/board", headers=bearer(personal_token(membership)))
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["patients"] == 3
    assert body["totals"]["due"] == 1
    assert body["totals"]["overdue"] == 1

    por_paciente = {row["patient_name"]: row for row in body["rows"]}
    assert por_paciente["Mel"]["critical_overdue"] is True
    assert por_paciente["Thor"]["critical_overdue"] is False
    assert por_paciente["Nina"]["counters"]["due"] == 1
    assert por_paciente["Thor"]["next_task"]["title"] == "Tarefa de Thor"


async def test_board_e_fila_concordam_sobre_a_mesma_tarefa(client, session):
    """Mesma fonte de verdade: o board não pode dizer 'no prazo' e a ficha 'atrasada'."""
    clinic, membership = await _clinica_com_tres_estados(session)
    headers = bearer(personal_token(membership))
    fila = (await client.get(
        f"/api/v1/tasks?from={(datetime.now(UTC) - timedelta(hours=6)).isoformat()}"
        f"&to={(datetime.now(UTC) + timedelta(hours=6)).isoformat()}",
        headers=headers,
    )).json()["items"]
    board = (await client.get("/api/v1/board", headers=headers)).json()

    estados_fila = {item["title"]: item["display_state"] for item in fila}
    for row in board["rows"]:
        proxima = row["next_task"]
        if proxima is not None:
            assert estados_fila[proxima["title"]] == proxima["display_state"]


async def test_auditoria_paginada_por_cursor(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="admin")
    headers = bearer(personal_token(membership))
    for indice in range(5):
        await client.post(
            "/api/v1/owners",
            json={"name": f"Tutor {indice}", "phone_e164": "+5511999990000"}, headers=headers,
        )

    vistos: list[int] = []
    cursor = None
    for _ in range(5):
        url = "/api/v1/audit?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(url, headers=headers)).json()
        vistos.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(vistos) == len(set(vistos))
    assert vistos == sorted(vistos, reverse=True)


async def test_auditoria_filtra_por_entidade(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    headers = bearer(personal_token(membership))
    criado = (await client.post(
        "/api/v1/owners", json={"name": "Marina", "phone_e164": "+5511999990000"}, headers=headers
    )).json()

    page = (await client.get(
        f"/api/v1/audit?entity_type=owner&entity_id={criado['id']}", headers=headers
    )).json()
    assert len(page["items"]) == 1
    assert page["items"][0]["action"] == "owner_created"


async def test_tecnico_nao_le_auditoria(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    resp = await client.get("/api/v1/audit", headers=bearer(personal_token(membership)))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_isolamento_de_tenant_no_board(client, session):
    clinic_a, membership_a = await _clinica_com_tres_estados(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    await make_hospitalization(session, clinic=clinic_b)
    await session.flush()

    body = (await client.get("/api/v1/board", headers=bearer(personal_token(membership_a)))).json()
    assert body["totals"]["patients"] == 3
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_board_audit.py -q`
Expected: `404` em `/api/v1/board` e `/api/v1/audit`.

- [ ] **Step 3: Implementar o schema do board**

Crie `src/back/app/schemas/board.py`:

```python
import uuid

from pydantic import BaseModel

from app.schemas.task import TaskOut


class BoardCounters(BaseModel):
    on_time: int = 0
    due: int = 0
    overdue: int = 0


class BoardRow(BaseModel):
    hospitalization_id: uuid.UUID
    patient_name: str
    kennel_name: str | None
    next_task: TaskOut | None
    counters: BoardCounters
    critical_overdue: bool


class BoardTotals(BaseModel):
    patients: int
    due: int
    overdue: int
    on_time_rate: float


class BoardOut(BaseModel):
    totals: BoardTotals
    rows: list[BoardRow]
```

- [ ] **Step 4: Implementar o router do board**

Crie `src/back/app/api/routes/board.py`:

```python
from datetime import UTC, datetime, timedelta
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_session
from app.models import Hospitalization, Kennel, Patient, Task
from app.schemas.board import BoardCounters, BoardOut, BoardRow, BoardTotals
from app.schemas.task import TaskOut
from app.services.tasks import TaskService

router = APIRouter(prefix="/api/v1/board", tags=["board"])


@router.get("", response_model=BoardOut)
async def board(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BoardOut:
    now = datetime.now(UTC)
    rows_stmt = (
        sa.select(Hospitalization, Patient.name, Kennel.name)
        .join(Patient, Patient.id == Hospitalization.patient_id)
        .outerjoin(Kennel, Kennel.id == Hospitalization.kennel_id)
        .where(
            Hospitalization.clinic_id == auth.clinic_id,
            Hospitalization.status == "active",
        )
        .order_by(Patient.name)
    )
    internacoes = list((await session.execute(rows_stmt)).all())

    tasks_stmt = sa.select(Task).where(
        Task.clinic_id == auth.clinic_id,
        Task.status == "pending",
        Task.scheduled_for <= now + timedelta(hours=12),
    )
    tarefas = list((await session.execute(tasks_stmt)).scalars())
    por_internacao: dict = {}
    for task in tarefas:
        por_internacao.setdefault(task.hospitalization_id, []).append(task)

    linhas: list[BoardRow] = []
    total_due = total_overdue = total_on_time = 0
    for hospitalization, patient_name, kennel_name in internacoes:
        pendentes = sorted(
            por_internacao.get(hospitalization.id, []), key=lambda item: item.scheduled_for
        )
        counters = BoardCounters()
        critica_atrasada = False
        for task in pendentes:
            # MESMA função da fila: nenhuma segunda fonte de verdade.
            estado = TaskService.display_state(task, now)
            setattr(counters, estado, getattr(counters, estado) + 1)
            if estado == "overdue" and task.criticality == "critical":
                critica_atrasada = True
        total_due += counters.due
        total_overdue += counters.overdue
        total_on_time += counters.on_time
        linhas.append(BoardRow(
            hospitalization_id=hospitalization.id,
            patient_name=patient_name,
            kennel_name=kennel_name,
            next_task=TaskOut.from_task(pendentes[0], now) if pendentes else None,
            counters=counters,
            critical_overdue=critica_atrasada,
        ))

    total = total_due + total_overdue + total_on_time
    return BoardOut(
        totals=BoardTotals(
            patients=len(internacoes), due=total_due, overdue=total_overdue,
            on_time_rate=round(total_on_time / total, 4) if total else 1.0,
        ),
        rows=linhas,
    )
```

- [ ] **Step 5: Implementar o router de auditoria**

Crie `src/back/app/api/routes/audit.py`:

```python
import uuid
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_session
from app.core.errors import AppError
from app.models import AuditEntry
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_name: str
    actor_license: str | None
    actor_license_authority: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    payload: dict[str, Any]
    entry_hash: str
    created_at: datetime


@router.get("", response_model=Page[AuditEntryOut])
async def list_audit(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
) -> Page[AuditEntryOut]:
    if auth.membership is None or auth.membership.role == "tech":
        raise AppError("forbidden", 403)

    stmt = sa.select(AuditEntry).where(AuditEntry.clinic_id == auth.clinic_id)
    if entity_type is not None:
        stmt = stmt.where(AuditEntry.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditEntry.entity_id == entity_id)
    if cursor is not None:
        stmt = stmt.where(AuditEntry.id < int(cursor))
    stmt = stmt.order_by(AuditEntry.id.desc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(rows[-1].id)
    return Page[AuditEntryOut](
        items=[AuditEntryOut.model_validate(row) for row in rows], next_cursor=next_cursor
    )
```

Registre os dois routers em `create_app()`.

- [ ] **Step 6: Rodar, lint e commit**

```bash
uv run pytest tests/test_board_audit.py -q && uv run pytest -q && uv run ruff check .
git add backend/app/schemas/board.py backend/app/api/routes/board.py backend/app/api/routes/audit.py backend/app/main.py backend/tests/test_board_audit.py
git commit -m "feat(board): visao geral da internacao e trilha de auditoria paginada"
```

Expected: `6 passed` no arquivo, suíte verde, lint limpo.

---

### Task 16: Seed de demonstração, README e smoke end-to-end

**Files:**
- Create: `src/back/scripts/seed_demo.py`
- Create: `src/back/README.md`
- Test: `src/back/tests/test_smoke_e2e.py`

**Interfaces:**
- Consumes: tudo das Tasks 1–15.
- Produces:
  - `uv run python -m scripts.seed_demo` — clínica demo pronta para a demonstração de venda.
  - `src/back/README.md` com o passo a passo de subir, testar e semear.
  - Um teste E2E que percorre o fluxo inteiro do produto.

> Este seed é o que o vendedor abre na frente da clínica. Ele precisa mostrar, sem nenhum clique de preparo: paciente crítico com tarefa atrasada, fluidoterapia contínua, PRN com guardrail e cerimônias do dia.

- [ ] **Step 1: Escrever o smoke E2E que falha**

Crie `src/back/tests/test_smoke_e2e.py`:

```python
import hashlib
import json
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.core.security import hash_password
from app.models import AuditEntry
from tests.factories import make_clinic, make_kennel, make_membership, make_owner, make_patient, make_user
from tests.helpers import bearer


async def test_fluxo_completo_da_internacao(client, session):
    clinic = await make_clinic(session, slug="demo", station_key_hash=hash_password("estacao-123"))
    vet_user = await make_user(session, email="paula@demo.vet", password_hash=hash_password("senha-123"))
    vet = await make_membership(
        session, clinic=clinic, user=vet_user, role="vet",
        license_number="12345", license_authority="CRMV-SP", pin_hash=hash_password("4321"),
    )
    kennel = await make_kennel(session, clinic=clinic, name="UTI 03")
    owner = await make_owner(session, clinic=clinic, name="Marina Campos")
    patient = await make_patient(session, clinic=clinic, owner=owner, name="Thor")
    await session.flush()

    # 1. Login pessoal
    login = await client.post(
        "/api/v1/auth/login", json={"email": "paula@demo.vet", "password": "senha-123"}
    )
    assert login.status_code == 200
    token = bearer(login.json()["access_token"])

    # 2. Admitir — as cerimônias do dia nascem junto
    admissao = await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "kennel_id": str(kennel.id),
              "vet_membership_id": str(vet.id), "consent_status": "consent_recorded"},
        headers=token,
    )
    assert admissao.status_code == 201
    hospitalization_id = admissao.json()["hospitalization"]["id"]

    detalhe = (await client.get(f"/api/v1/hospitalizations/{hospitalization_id}", headers=token)).json()
    assert sorted(p["name"] for p in detalhe["prescriptions"]) == ["Contato com tutor", "Evolução diária"]

    # 3. Prescrever
    prescricao = await client.post(
        f"/api/v1/hospitalizations/{hospitalization_id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona 25 mg/kg IV",
              "frequency_minutes": 480, "duration_hours": 72, "criticality": "normal",
              "first_dose_now": True, "price_minor": 1800,
              "details": {"drug": "dipirona", "dose": "25 mg/kg", "route": "IV"}},
        headers=token,
    )
    assert prescricao.status_code == 201

    # 4. A fila da janela já mostra a primeira dose
    fila = (await client.get("/api/v1/tasks", headers=token)).json()["items"]
    primeira = next(item for item in fila if item["title"].startswith("Dipirona"))
    assert primeira["display_state"] in ("on_time", "due")

    # 5. Executar em modo estação, identificando o operador por PIN
    estacao = await client.post(
        "/api/v1/auth/station", json={"clinic_slug": "demo", "station_key": "estacao-123"}
    )
    assert estacao.status_code == 200
    station_headers = bearer(estacao.json()["access_token"])

    pin = await client.post("/api/v1/auth/pin", json={"pin": "4321"}, headers=station_headers)
    assert pin.status_code == 200
    operador = {**station_headers, "X-Operator-Token": pin.json()["operator_token"]}

    execucao = await client.post(
        f"/api/v1/tasks/{primeira['id']}/execute",
        json={"values": {"note": "sem intercorrência"}}, headers=operador,
    )
    assert execucao.status_code == 200
    assert execucao.json()["status"] == "done"

    # 6. O board reflete na hora, com a mesma fonte da fila
    board = (await client.get("/api/v1/board", headers=token)).json()
    linha = next(row for row in board["rows"] if row["patient_name"] == "Thor")
    assert board["totals"]["patients"] == 1
    assert linha["critical_overdue"] is False

    # 7. A trilha tem nome, registro profissional e a cadeia de hash íntegra
    auditoria = (await client.get("/api/v1/audit", headers=token)).json()["items"]
    execucoes = [item for item in auditoria if item["action"] == "task_executed"]
    assert execucoes[0]["actor_license"] == "12345"
    assert execucoes[0]["actor_license_authority"] == "CRMV-SP"

    entradas = list((await session.execute(
        sa.select(AuditEntry).where(AuditEntry.clinic_id == clinic.id).order_by(AuditEntry.id)
    )).scalars())
    anterior = ""
    for entrada in entradas:
        assert entrada.prev_hash == anterior
        esperado = hashlib.sha256(
            f"{anterior}|{entrada.clinic_id}|{entrada.action}|{entrada.entity_type}|"
            f"{entrada.entity_id}|{json.dumps(entrada.payload, sort_keys=True, default=str)}|"
            f"{entrada.created_at.isoformat()}".encode()
        ).hexdigest()
        assert entrada.entry_hash == esperado
        anterior = entrada.entry_hash
```

> Se a fórmula do hash acima divergir da implementada na Task 4, **corrija o teste para espelhar a implementação** — o que importa é a cadeia ser verificável de fora.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_smoke_e2e.py -q`
Expected: falha na primeira asserção que ainda não estiver completa (normalmente o login, se alguma task anterior não estiver mergeada).

- [ ] **Step 3: Escrever o seed de demonstração**

Crie `src/back/scripts/seed_demo.py`:

```python
"""Clínica demo do PlantãoVet — o que o vendedor abre na frente do cliente.

    uv run python -m scripts.seed_demo
"""

import asyncio
from datetime import UTC, datetime, timedelta

from app.core.db import async_session_factory
from app.core.security import hash_password
from app.models import (
    Clinic, Hospitalization, Kennel, Membership, Owner, Patient, Prescription, Task, User,
)
from app.schemas.prescription import default_tolerance
from app.services.audit import ActorInfo
from app.services.hospitalization import HospitalizationService
from app.services.tasks import TaskService

STATION_KEY = "estacao-123"
SENHA = "senha-123"

EQUIPE = [
    ("Dra. Paula Martins", "paula@demo.vet", "vet", "12345", "CRMV-SP", "1234"),
    ("Marina Coelho", "marina@demo.vet", "tech", None, None, "2345"),
    ("Rafael Souza", "rafael@demo.vet", "admin", None, None, "3456"),
]

PACIENTES = [
    ("Thor", "dog", "UTI 03", 24.3),
    ("Nina", "cat", "Box 07", 4.1),
    ("Mel", "cat", "UTI 01", 3.6),
    ("Bob", "dog", "Box 02", 12.0),
    ("Luna", "cat", "Box 04", 3.9),
]


async def main() -> None:
    async with async_session_factory() as session:
        clinic = Clinic(
            name="Clínica Vida Animal", slug="demo", bed_limit=25, plan_tier="hospital",
            station_key_hash=hash_password(STATION_KEY),
        )
        session.add(clinic)
        await session.flush()

        memberships = {}
        for nome, email, papel, registro, orgao, pin in EQUIPE:
            user = User(name=nome, email=email, password_hash=hash_password(SENHA))
            session.add(user)
            await session.flush()
            membership = Membership(
                clinic_id=clinic.id, user_id=user.id, role=papel,
                license_number=registro, license_authority=orgao, pin_hash=hash_password(pin),
            )
            session.add(membership)
            memberships[papel] = membership
        await session.flush()

        vet = memberships["vet"]
        actor = ActorInfo(
            membership_id=vet.id, name="Dra. Paula Martins",
            license_number="12345", license_authority="CRMV-SP",
        )

        kennels = {}
        for nome in ("UTI 01", "UTI 03", "Box 02", "Box 04", "Box 07"):
            kennel = Kennel(clinic_id=clinic.id, name=nome, area="UTI" if "UTI" in nome else "Geral")
            session.add(kennel)
            kennels[nome] = kennel
        await session.flush()

        agora = datetime.now(UTC)
        for nome, especie, box, peso in PACIENTES:
            owner = Owner(
                clinic_id=clinic.id, name=f"Tutor de {nome}", phone_e164="+5511999990000"
            )
            session.add(owner)
            await session.flush()
            patient = Patient(
                clinic_id=clinic.id, owner_id=owner.id, name=nome, species=especie, weight_kg=peso
            )
            session.add(patient)
            await session.flush()
            hospitalization = Hospitalization(
                clinic_id=clinic.id, patient_id=patient.id, kennel_id=kennels[box].id,
                vet_membership_id=vet.id, consent_status="consent_recorded",
                admitted_at=agora - timedelta(days=2),
            )
            session.add(hospitalization)
            await session.flush()
            await HospitalizationService.create_default_prescriptions(
                session, hospitalization=hospitalization, clinic=clinic, actor=actor
            )

            receitas = [
                dict(kind="recurring", category="medication", name="Dipirona 25 mg/kg IV",
                     frequency_minutes=480, criticality="normal", price_minor=1800,
                     details={"drug": "dipirona", "dose": "25 mg/kg", "route": "IV"}),
                dict(kind="continuous", category="fluids", name="Ringer Lactato",
                     frequency_minutes=120, criticality="normal", price_minor=1200,
                     details={"rate_ml_h": 60}),
                dict(kind="recurring", category="monitoring", name="Pressão arterial",
                     frequency_minutes=720, criticality="critical", price_minor=4500,
                     details={}),
                dict(kind="recurring", category="nutrition", name="Alimentação úmida",
                     frequency_minutes=480, criticality="normal", price_minor=0, details={}),
                dict(kind="prn", category="medication", name="Metadona 0,2 mg/kg IM",
                     frequency_minutes=None, criticality="critical", price_minor=3800,
                     max_doses_24h=4, min_interval_minutes=240,
                     details={"drug": "metadona", "route": "IM"}),
            ]
            for receita in receitas:
                prescription = Prescription(
                    clinic_id=clinic.id, hospitalization_id=hospitalization.id,
                    tolerance_minutes=default_tolerance(
                        receita["criticality"], receita["frequency_minutes"]
                    ),
                    starts_at=agora - timedelta(hours=8), created_by=vet.id, **receita,
                )
                session.add(prescription)
                await session.flush()
                await TaskService.materialize(
                    session, prescription=prescription, clinic=clinic,
                    until=agora + timedelta(hours=48),
                )

        # Uma tarefa crítica deliberadamente atrasada: é o que a demo precisa mostrar.
        atrasada = await session.scalar(
            Task.__table__.select().where(Task.criticality == "critical").limit(1)
        )
        if atrasada is not None:
            await session.execute(
                Task.__table__.update()
                .where(Task.id == atrasada.id)
                .values(scheduled_for=agora - timedelta(hours=3))
            )

        await session.commit()
        print(f"Clínica demo criada · slug=demo · senha={SENHA} · station_key={STATION_KEY}")
        print("PINs: vet 1234 · técnico 2345 · admin 3456")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Escrever o README**

Crie `src/back/README.md`:

````markdown
# PlantãoVet — backend

API da internação: prescrição → aprazamento → tarefas, com board, trilha de auditoria
encadeada e dois modos de identidade (pessoal e estação).

## Subir o ambiente

```bash
docker compose up -d postgres     # na raiz do repositório
cd src/back
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

## Testes

```bash
uv run pytest -q          # suíte inteira
uv run ruff check .       # lint
```

## Dados de demonstração

```bash
uv run python -m scripts.seed_demo
```

Cria a clínica **demo** com 5 pacientes internados, prescrições dos três tipos
(recorrente, contínua e PRN), as cerimônias do dia e uma tarefa crítica atrasada.

## Como entrar

**Modo pessoal** (o profissional na própria conta):

```bash
curl -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email": "paula@demo.vet", "password": "senha-123"}'
```

**Modo estação** (computador ou celular compartilhado pela equipe): o dispositivo
entra com a chave da clínica e cada ação clínica exige o PIN do operador.

```bash
curl -X POST localhost:8000/api/v1/auth/station \
  -H 'content-type: application/json' \
  -d '{"clinic_slug": "demo", "station_key": "estacao-123"}'

curl -X POST localhost:8000/api/v1/auth/pin \
  -H "authorization: Bearer $STATION_TOKEN" \
  -H 'content-type: application/json' -d '{"pin": "1234"}'
```

O `operator_token` que volta vale 5 minutos e vai no header `X-Operator-Token` das
mutações clínicas.

## Idioma, moeda e unidades

São da clínica, não do sistema (ADR-0004): `clinics.locale`, `clinics.currency`,
`clinics.unit_system` e `clinics.timezone`. A API nunca devolve texto para exibir —
só códigos de erro estáveis, que o cliente traduz. Para ver uma clínica em inglês,
mude `locale` para `en` e reinternem um paciente: as cerimônias do dia nascem
traduzidas.

## Horários das doses

Cada clínica tem seus horários-âncora em `clinics.anchors`, chaveados por minutos
(`480` = 8/8h → 10:00, 18:00, 02:00). O aprazamento deriva os horários dali; quando
não há âncora para a frequência (ex.: 30 min de UTI), usa offset a partir do início.
````

- [ ] **Step 5: Rodar o seed de verdade**

Run (em `src/back/`, com o Postgres de pé):

```bash
uv run alembic upgrade head && uv run python -m scripts.seed_demo
```

Expected: `Clínica demo criada · slug=demo · senha=senha-123 · station_key=estacao-123`

- [ ] **Step 6: Suíte inteira verde**

Run: `uv run pytest -q && uv run ruff check .`
Expected: toda a suíte das 16 tasks passando, lint limpo.

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/seed_demo.py backend/README.md backend/tests/test_smoke_e2e.py
git commit -m "feat(demo): seed da clinica de demonstracao, README e smoke end-to-end"
```
