### Task 1: Scaffold, erros como códigos e i18n

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/.gitignore`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/core/errors.py`
- Create: `backend/app/i18n/__init__.py`
- Create: `backend/app/i18n/catalog.py`
- Create: `backend/app/i18n/pt-BR.json`
- Create: `backend/app/i18n/en.json`
- Create: `docker-compose.yml`
- Create: `backend/tests/__init__.py`
- Test: `backend/tests/test_health.py`
- Test: `backend/tests/test_config.py`
- Test: `backend/tests/test_errors.py`
- Test: `backend/tests/test_i18n.py`

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
cd backend
uv init --bare --python 3.13
uv python pin 3.13
mkdir -p app/core app/i18n tests
touch app/__init__.py app/core/__init__.py app/i18n/__init__.py tests/__init__.py
```

Substitua o conteúdo de `backend/pyproject.toml` por:

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

Crie `backend/.gitignore`:

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

Crie `docker-compose.yml` na **raiz do repositório** (mesmo nível de `backend/`):

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

Crie `backend/tests/test_health.py`:

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

Crie `backend/tests/test_config.py`:

```python
from app.core.config import Settings


def test_settings_have_dev_defaults():
    settings = Settings(_env_file=None)
    assert settings.env == "dev"
    assert settings.jwt_secret == "dev-secret-change-me"
    assert settings.database_url.startswith("postgresql+asyncpg://")
```

- [ ] **Step 4: Rodar e ver falhar**

Run: `cd backend && uv run pytest tests/test_health.py tests/test_config.py -v`
Expected: erros de coleta — `ModuleNotFoundError: No module named 'app.main'` e `No module named 'app.core.config'`

- [ ] **Step 5: Implementar config e main mínimos**

Crie `backend/app/core/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://plantaovet:plantaovet@localhost:5432/plantaovet"
    jwt_secret: str = "dev-secret-change-me"
    env: str = "dev"


settings = Settings()
```

Crie `backend/app/main.py` (ainda sem os handlers de erro — entram no ciclo seguinte):

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

Run: `cd backend && uv run pytest tests/test_health.py tests/test_config.py -v`
Expected: `2 passed`

- [ ] **Step 7: Escrever os testes falhando de erros como códigos**

Crie `backend/tests/test_errors.py`:

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

Run: `cd backend && uv run pytest tests/test_errors.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.core.errors'`

- [ ] **Step 9: Implementar AppError, handlers e registrá-los em create_app**

Crie `backend/app/core/errors.py`:

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

Substitua o conteúdo de `backend/app/main.py` por:

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

Run: `cd backend && uv run pytest tests/test_errors.py -v`
Expected: `6 passed`

- [ ] **Step 11: Escrever os testes falhando de i18n**

Crie `backend/tests/test_i18n.py`:

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

Run: `cd backend && uv run pytest tests/test_i18n.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.i18n.catalog'`

- [ ] **Step 13: Implementar catálogos e translate**

Crie `backend/app/i18n/pt-BR.json`:

```json
{
  "task.check": "Checagem: {name}",
  "ceremony.owner_contact": "Contato com o tutor",
  "ceremony.daily_progress_note": "Evolução diária",
  "compliance.br.license_authority_label": "CRMV"
}
```

Crie `backend/app/i18n/en.json`:

```json
{
  "task.check": "Check: {name}",
  "ceremony.owner_contact": "Owner contact",
  "ceremony.daily_progress_note": "Daily progress note",
  "compliance.br.license_authority_label": "CRMV"
}
```

Crie `backend/app/i18n/catalog.py`:

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

Run: `cd backend && uv run pytest tests/test_i18n.py -v`
Expected: `4 passed`

- [ ] **Step 15: Suíte completa e lint**

Run: `cd backend && uv run pytest -v && uv run ruff check .`
Expected: `12 passed` e `All checks passed!`

- [ ] **Step 16: Commit**

```bash
git add backend docker-compose.yml
git commit -m "feat: scaffold backend with error codes and i18n catalogs"
```

### Task 2: Banco async, Alembic e harness de testes

**Files:**
- Create: `backend/app/core/db.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/deps.py`
- Create: `backend/alembic.ini` (gerado por `alembic init`)
- Create: `backend/alembic/` (gerado por `alembic init`; `env.py` substituído)
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_harness.py`

**Interfaces:**
- Consumes: `settings` (`app.core.config`, Task 1), `create_app` (`app.main`, Task 1), serviço postgres do docker-compose (Task 1).
- Produces:
  - `app.core.db.Base` (DeclarativeBase — TODA model herda dela), `engine` (AsyncEngine) e `async_session_factory` (async_sessionmaker de `AsyncSession`).
  - `app.api.deps.get_session() -> AsyncIterator[AsyncSession]` — dependency FastAPI de sessão.
  - Fixtures pytest (em `tests/conftest.py`): `migrated_database` (session-scoped: recria `plantaovet_test` e roda `alembic upgrade head`), `db_session` (AsyncSession com rollback por teste, `join_transaction_mode="create_savepoint"` — `commit()` no código sob teste vira SAVEPOINT) e `client` (httpx.AsyncClient sobre ASGITransport com `get_session` sobrescrita por `db_session`).
  - `tests.conftest.TEST_DATABASE_URL: str` — URL do banco de teste.
  - Alembic async funcional: `env.py` lê `settings.database_url`; migrações das Tasks 3+ só criam arquivos em `alembic/versions/`.

- [ ] **Step 1: Escrever os testes falhando do harness**

Crie `backend/tests/test_harness.py`:

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

Run: `cd backend && uv run pytest tests/test_harness.py -v`
Expected: `5 errors` — `fixture 'db_session' not found` / `fixture 'client' not found`

- [ ] **Step 3: Implementar engine, Base e session factory**

Crie `backend/app/core/db.py`:

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

Crie `backend/app/api/__init__.py` vazio e `backend/app/api/deps.py`:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
```

- [ ] **Step 5: Inicializar o Alembic com template async e ligá-lo às settings**

Run (em `backend/`):

```bash
uv run alembic init -t async alembic
```

Expected: cria `alembic.ini` e `alembic/` (`env.py`, `script.py.mako`, `versions/`).

Deixe `alembic.ini` como foi gerado (o `env.py` sobrescreve `sqlalchemy.url`). Substitua o conteúdo de `backend/alembic/env.py` por:

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

Run: `cd backend && uv run alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.` e exit 0 (ainda não há revisões — no-op).

- [ ] **Step 7: Implementar o conftest com banco de teste, rollback por teste e client**

Crie `backend/tests/conftest.py`:

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

Run: `cd backend && uv run pytest tests/test_harness.py -v`
Expected: `5 passed`

- [ ] **Step 9: Suíte completa e lint**

Run: `cd backend && uv run pytest -v && uv run ruff check .`
Expected: `17 passed` e `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add backend
git commit -m "feat: add async db engine, alembic and test harness"
```

### Task 3: Identidade, tenant e compliance profile

**Files:**
- Create: `backend/app/core/security.py`
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/models/clinic.py`
- Create: `backend/app/models/user.py`
- Create: `backend/app/models/membership.py`
- Create: `backend/app/compliance/__init__.py`
- Create: `backend/app/compliance/br.py`
- Create: `backend/alembic/versions/0001_identity_tables.py`
- Create: `backend/tests/factories.py`
- Modify: `backend/alembic/env.py` (import dos models para o metadata)
- Test: `backend/tests/test_security.py`
- Test: `backend/tests/test_identity_models.py`
- Test: `backend/tests/test_compliance.py`

**Interfaces:**
- Consumes: `Base` (`app.core.db`, Task 2), fixture `db_session` (Task 2), `settings` e `AppError` (Task 1), chave de catálogo `compliance.br.license_authority_label` (Task 1).
- Produces:
  - `app.core.security.hash_password(plain: str) -> str` · `verify_password(plain: str, hashed: str) -> bool` · `create_jwt(claims: dict[str, Any], *, expires_in: timedelta = timedelta(hours=12)) -> str` · `decode_jwt(token: str) -> dict[str, Any]` (HS256; expirado → `AppError("token_expired", 401)`; inválido → `AppError("invalid_credentials", 401)`).
  - Models (reexportados em `app.models`): `Clinic`, `User`, `Membership`, enums `UnitSystem(StrEnum)` (`metric|imperial`) e `Role(StrEnum)` (`vet|tech|admin`), constantes `DEFAULT_ANCHORS` e `DEFAULT_PRESCRIPTIONS`.
  - `app.compliance.ComplianceProfile` (dataclass frozen: `name`, `license_authority_label_key`, `requires_daily_progress_note`, `retention_years`) e `get_profile(name: str) -> ComplianceProfile` (desconhecido → `KeyError`); `app.compliance.br.BR_PROFILE`.
  - Factories async (em `tests/factories.py`): `make_clinic(session, **overrides) -> Clinic`, `make_user(session, **overrides) -> User`, `make_membership(session, *, clinic=None, user=None, **overrides) -> Membership`.
  - Migração `0001` com `clinics`, `users`, `memberships` e os índices aplicáveis (UNIQUE `clinics.slug`, UNIQUE `users.email`, UNIQUE `memberships(clinic_id, user_id)`).

- [ ] **Step 1: Escrever os testes falhando de senha e JWT**

Crie `backend/tests/test_security.py`:

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

Run: `cd backend && uv run pytest tests/test_security.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.core.security'`

- [ ] **Step 3: Implementar security.py**

Crie `backend/app/core/security.py`:

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

Run: `cd backend && uv run pytest tests/test_security.py -v`
Expected: `4 passed`

- [ ] **Step 5: Escrever os testes falhando dos models de identidade**

Crie `backend/tests/test_identity_models.py`:

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

Run: `cd backend && uv run pytest tests/test_identity_models.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 7: Implementar o model Clinic com defaults**

Crie `backend/app/models/clinic.py`:

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

Crie `backend/app/models/user.py`:

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

Crie `backend/app/models/membership.py`:

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

Crie `backend/app/models/__init__.py`:

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

Em `backend/alembic/env.py`, logo abaixo da linha `from app.core.db import Base`, adicione:

```python
import app.models  # noqa: F401  (registra os models no metadata)
```

- [ ] **Step 9: Escrever a migração 0001**

Run (em `backend/`):

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

Run: `cd backend && uv run alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Running upgrade  -> 0001, identity tables`

- [ ] **Step 10: Implementar as factories de teste**

Crie `backend/tests/factories.py`:

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

Run: `cd backend && uv run pytest tests/test_identity_models.py -v`
Expected: `3 passed`

- [ ] **Step 12: Escrever os testes falhando do compliance profile**

Crie `backend/tests/test_compliance.py`:

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

Run: `cd backend && uv run pytest tests/test_compliance.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.compliance'`

- [ ] **Step 14: Implementar o pacote de compliance**

Crie `backend/app/compliance/__init__.py`:

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

Crie `backend/app/compliance/br.py`:

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

Run: `cd backend && uv run pytest tests/test_compliance.py -v`
Expected: `3 passed`

- [ ] **Step 16: Suíte completa e lint**

Run: `cd backend && uv run pytest -v && uv run ruff check .`
Expected: `27 passed` e `All checks passed!`

- [ ] **Step 17: Commit**

```bash
git add backend
git commit -m "feat: add clinic, user and membership models with security and compliance profile"
```

### Task 4: Auditoria append-only com before/after e hash encadeado

**Files:**
- Create: `backend/app/models/audit.py`
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/audit.py`
- Create: `backend/alembic/versions/0002_audit_entries.py`
- Create: `backend/alembic/versions/0003_audit_append_only_trigger.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_audit.py`

**Interfaces:**
- Consumes: `Base` (Task 2), fixture `db_session` (Task 2), factories `make_clinic`/`make_membership` (Task 3), migrações `0001` (Task 3).
- Produces:
  - Model `app.models.AuditEntry` (tabela `audit_entries`: `id` bigint identity, `clinic_id`, `actor_membership_id`, `actor_name`, `actor_license`, `actor_license_authority`, `action`, `entity_type`, `entity_id`, `payload` jsonb, `prev_hash`, `entry_hash`, `created_at`).
  - `app.services.audit.ActorInfo` (dataclass: `membership_id: uuid.UUID | None`, `name: str`, `license_number: str | None`, `license_authority: str | None`).
  - `app.services.audit.AuditService` com `REDACTED = {"phone_e164", "tax_id", "password_hash", "pin_hash", "station_key_hash"}`, `snapshot(entity: Any) -> dict` (estático) e `record(session, *, clinic_id, actor, action, entity_type, entity_id, before=None, after=None, extra=None) -> None` (estático, async) — assinaturas EXATAS do brief; toda mutação clínica das Tasks 5+ chama `AuditService.record`.
  - Migrações `0002` (tabela + índices `audit_entries(clinic_id, id DESC)` e `audit_entries(clinic_id, entity_type, entity_id)`) e `0003` (trigger Postgres que levanta exceção em UPDATE/DELETE).

> Regra transversal 3 levada ao banco: além de nenhum endpoint de DELETE, `audit_entries` rejeita UPDATE/DELETE no próprio Postgres. LGPD (spec §2): o `payload` carrega ids e dados clínicos, nunca dados de contato do tutor — por isso o `snapshot` redige `phone_e164`/`tax_id` antes de qualquer gravação.

- [ ] **Step 1: Escrever o teste falhando de snapshot com redação**

Crie `backend/tests/test_audit.py`:

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

Run: `cd backend && uv run pytest tests/test_audit.py -v`
Expected: erro de coleta — `ModuleNotFoundError: No module named 'app.services'`

- [ ] **Step 3: Implementar ActorInfo e snapshot**

Crie `backend/app/services/__init__.py` vazio e `backend/app/services/audit.py`:

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

Run: `cd backend && uv run pytest tests/test_audit.py -v`
Expected: `2 passed`

- [ ] **Step 5: Escrever os testes falhando de record e da cadeia de hash**

Substitua o bloco de imports no TOPO de `backend/tests/test_audit.py` por:

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

Run: `cd backend && uv run pytest tests/test_audit.py -v`
Expected: erro de coleta do arquivo inteiro — `ImportError: cannot import name 'AuditEntry' from 'app.models'`

- [ ] **Step 7: Implementar o model AuditEntry e a migração 0002**

Crie `backend/app/models/audit.py`:

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

Substitua o conteúdo de `backend/app/models/__init__.py` por:

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

Run (em `backend/`):

```bash
uv run alembic revision -m "audit entries" --rev-id 0002
```

Substitua o conteúdo de `backend/alembic/versions/0002_audit_entries.py` por:

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

Substitua o conteúdo de `backend/app/services/audit.py` por:

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

Run: `cd backend && uv run pytest tests/test_audit.py -v`
Expected: `5 passed`

- [ ] **Step 10: Escrever os testes falhando do append-only no banco**

Substitua o bloco de imports no TOPO de `backend/tests/test_audit.py` por:

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

Run: `cd backend && uv run pytest tests/test_audit.py -v`
Expected: os 2 testes novos falham com `Failed: DID NOT RAISE` (ainda não há trigger); os 5 anteriores passam.

- [ ] **Step 12: Escrever a migração 0003 com o trigger append-only**

Run (em `backend/`):

```bash
uv run alembic revision -m "audit append only trigger" --rev-id 0003
```

Substitua o conteúdo de `backend/alembic/versions/0003_audit_append_only_trigger.py` por:

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

Run: `cd backend && uv run pytest tests/test_audit.py -v`
Expected: `7 passed` (o conftest recria `plantaovet_test` e aplica 0001→0003 no início da sessão).

- [ ] **Step 14: Suíte completa, migração no banco de dev e lint**

Run: `cd backend && uv run pytest -v && uv run alembic upgrade head && uv run ruff check .`
Expected: `34 passed`, `Running upgrade 0001 -> 0002` e `Running upgrade 0002 -> 0003`, `All checks passed!`

- [ ] **Step 15: Commit**

```bash
git add backend
git commit -m "feat: add append-only audit trail with before/after snapshots and hash chaining"
```

