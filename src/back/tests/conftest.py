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
# Um banco POR PROCESSO. Duas execuções simultâneas da suíte (o que acontece o
# tempo todo quando vários agentes trabalham em paralelo no mesmo repositório)
# derrubavam o banco uma da outra no meio do caminho: `DROP DATABASE ... WITH
# (FORCE)` mata as conexões da outra, que então falha em testes aleatórios com
# "connection was closed in the middle of operation" e `relation "clinics" does
# not exist`. Parecia bug de produto e era colisão de harness.
TEST_DATABASE_NAME = os.environ.get("PYTEST_DATABASE", f"plantaovet_test_{os.getpid()}")
TEST_DATABASE_URL = _BASE_URL.rsplit("/", 1)[0] + f"/{TEST_DATABASE_NAME}"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# O agendador é ligado no lifespan da app. O teste chama
# `extend_scheduling_window` direto e não quer um APScheduler vivo no event
# loop do teste, nem uma engine global fora da transação do harness.
os.environ.setdefault("SCHEDULER_ENABLED", "false")

import asyncpg
import httpx
import pytest
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.api.deps import get_session
from app.main import create_app


async def _recreate_test_database() -> None:
    admin_dsn = (
        _BASE_URL.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0] + "/postgres"
    )
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE_NAME}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DATABASE_NAME}"')
    finally:
        await conn.close()


#: Guarda de PROCESSO, não de fixture. O pytest-asyncio reinstancia uma
#: fixture de escopo maior quando o loop do teste muda, e aqui isso significava
#: rodar `DROP DATABASE ... WITH (FORCE)` no meio da suíte: as conexões dos
#: testes em andamento morriam ("connection was closed in the middle of
#: operation") e as seguintes batiam em `relation "clinics" does not exist`.
#: Falhava em testes diferentes a cada execução, o que fazia parecer bug de
#: produto. O banco é preparado UMA vez por processo.
_DATABASE_READY = False


async def _drop_test_database() -> None:
    admin_dsn = (
        _BASE_URL.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0] + "/postgres"
    )
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE_NAME}" WITH (FORCE)')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_database():
    # Fixture síncrona de sessão: roda fora de qualquer event loop, então
    # asyncio.run (aqui e dentro do env.py async do Alembic) funciona.
    global _DATABASE_READY
    if _DATABASE_READY:
        return
    asyncio.run(_recreate_test_database())
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")
    _DATABASE_READY = True
    yield
    # O banco é descartável: deixá-lo para trás encheria o servidor de
    # `plantaovet_test_<pid>` a cada execução.
    asyncio.run(_drop_test_database())


@pytest.fixture
async def db_session(migrated_database):
    # Engine por teste: conexões asyncpg são presas ao event loop, e o
    # pytest-asyncio cria um loop novo por teste (function scope).
    #
    # NullPool porque o pool guardaria a conexão do loop anterior: o teste
    # seguinte a reaproveitaria num loop já fechado e o asyncpg estouraria
    # `ConnectionDoesNotExistError` de forma intermitente, com a suíte inteira
    # falhando em testes diferentes a cada execução.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
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
    # É a MESMA sessão de db_session. Nunca abra uma segunda.
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


@pytest.fixture
def db_session_factory(db_session):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        # O job roda dentro da MESMA transação do teste, e o rollback do harness
        # continua limpando tudo no fim.
        yield db_session

    return _factory
