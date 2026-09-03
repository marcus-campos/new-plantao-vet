#!/usr/bin/env bash
# pg_dump noturno para o bucket. O bucket apaga sozinho o que passa de 30 dias.
#
# O dump vai para um arquivo temporário e é CONFERIDO antes de subir: mandando
# direto pelo pipe, um pg_dump que morre no meio virava um .gz truncado no
# bucket, e o backup só se descobre ruim no dia em que precisa dele.
set -euo pipefail
cd /opt/plantaovet

BUCKET="$(sed -n 's/^BACKUP_BUCKET=//p' .env)"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
TMP="$(mktemp /tmp/plantaovet-XXXXXX.sql.gz)"
trap 'rm -f "$TMP"' EXIT

if ! docker compose exec -T postgres pg_dump -U plantaovet --no-owner plantaovet | gzip -6 > "$TMP"; then
  echo "ERRO: pg_dump falhou; nada foi enviado" >&2
  exit 1
fi

# Um dump íntegro termina com a linha de conclusão do próprio pg_dump. É o
# teste mais barato que distingue "backup" de "arquivo".
if ! gunzip -c "$TMP" | tail -5 | grep -q "PostgreSQL database dump complete"; then
  echo "ERRO: o dump está truncado; nada foi enviado" >&2
  exit 1
fi

gcloud storage cp "$TMP" "gs://$BUCKET/pg/plantaovet-$STAMP.sql.gz" --quiet
echo "backup: plantaovet-$STAMP.sql.gz ($(du -h "$TMP" | cut -f1))"
