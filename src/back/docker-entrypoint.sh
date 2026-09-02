#!/bin/sh
# Sobe a API já com o banco no lugar: espera o Postgres, migra e (opcionalmente) semeia.
set -e

echo "› esperando o Postgres…"
until python -c "
import asyncio, sys
import asyncpg
from app.core.config import settings

async def check():
    dsn = settings.database_url.replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    await conn.close()

try:
    asyncio.run(check())
except Exception:
    sys.exit(1)
" 2>/dev/null; do
  sleep 1
done

echo "› aplicando migrações"
alembic upgrade head

if [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "› semeando a clínica demo (o script é idempotente)"
  python -m scripts.seed_demo
fi

exec "$@"
