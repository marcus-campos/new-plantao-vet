#!/bin/sh
# Sobe a API já com o banco no lugar: espera o Postgres, migra e (opcionalmente) semeia.
set -e

# Em produção o socket do Cloud SQL demora alguns segundos a aparecer; o laço
# abaixo já cobre isso. Teto de 60 s: banco que não vem é erro de deploy, e
# um contêiner que espera para sempre esconde o erro do Cloud Run.
tentativas=0
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
  tentativas=$((tentativas + 1))
  if [ "$tentativas" -ge 60 ]; then
    echo "› Postgres não respondeu em 60 s; abortando"
    exit 1
  fi
  sleep 1
done

echo "› aplicando migrações"
alembic upgrade head

if [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "› semeando a clínica demo (o script é idempotente)"
  python -m scripts.seed_demo
fi

exec "$@"
