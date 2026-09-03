#!/usr/bin/env bash
# Chamado pelo GitHub Actions por SSH: `sudo /opt/plantaovet/deploy.sh <tag>`.
# Troca a tag, puxa, sobe e confere. Se não subir, VOLTA para a tag anterior:
# um deploy que falha não pode deixar a clínica sem sistema no meio do plantão.
set -euo pipefail
TAG="${1:?tag da imagem}"
cd /opt/plantaovet

# Trava: o rollback manual documentado no README e um deploy automático podem
# cair juntos, e dois `docker compose up` concorrentes deixam o .env e os
# contêineres em estados diferentes.
exec 9>/var/lock/plantaovet-deploy
flock -w 300 9 || { echo "outro deploy em andamento"; exit 1; }

ANTERIOR="$(sed -n 's/^TAG=//p' .env)"
HOST="$(sed -n 's/^HOST=//p' .env)"
REGISTRY_HOST="$(sed -n 's/^REGISTRY=\([^/]*\).*/\1/p' .env)"
gcloud auth configure-docker "$REGISTRY_HOST" --quiet >/dev/null

subir() {
  sed -i "s/^TAG=.*/TAG=$1/" .env
  docker compose pull --quiet
  docker compose up -d --remove-orphans
}

# A fumaça bate no Caddy de DENTRO da VM: o teste anterior saía para a
# internet e dependia de DNS público e do certificado, então um problema de
# DNS reprovava um deploy que estava bom (e o contrário também).
saudavel() {
  for _ in $(seq 1 30); do
    if curl -fsS --resolve "$HOST:443:127.0.0.1" "https://$HOST/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

subir "$TAG"
if saudavel; then
  # `-a` para valer: sem ele o prune só remove imagem órfã, e cada deploy
  # deixava a imagem antiga com tag ocupando o disco de 20 GB até encher (e
  # disco cheio leva o Postgres junto). O Docker nunca apaga imagem em uso.
  docker image prune -af --filter "until=168h" >/dev/null 2>&1 || true
  echo "ok: $TAG no ar"
  df -h / | tail -1
  exit 0
fi

echo "a API não respondeu; voltando para $ANTERIOR" >&2
docker compose logs --tail=60 api >&2 || true
if [ -n "$ANTERIOR" ] && [ "$ANTERIOR" != "$TAG" ]; then
  subir "$ANTERIOR"
  saudavel && echo "rollback para $ANTERIOR concluído" >&2 || echo "ROLLBACK TAMBÉM FALHOU: entre na VM" >&2
fi
exit 1
