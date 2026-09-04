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
REGISTRY="$(sed -n 's/^REGISTRY=//p' .env)"
REGISTRY_HOST="${REGISTRY%%/*}"
gcloud auth configure-docker "$REGISTRY_HOST" --quiet >/dev/null

# O .env precisa SEMPRE descrever o que está rodando: ele é o que o compose lê
# no boot da VM. Se um deploy falhar e deixar aqui uma tag que não está no ar,
# o próximo reboot sobe aquela tag — e se ela nem existir no registry, a VM
# reinicia sem produto. Foi o que aconteceu no primeiro deploy real.
anotar_tag() { sed -i "s/^TAG=.*/TAG=$1/" .env; }

subir() {
  anotar_tag "$1"
  if ! docker compose pull --quiet || ! docker compose up -d --remove-orphans; then
    anotar_tag "$ANTERIOR_REAL"
    return 1
  fi
}

# Só dá para voltar para uma tag cuja imagem exista de fato. No primeiro deploy
# a tag anterior é `latest`, que nunca foi publicada: tentar voltar para ela
# derrubaria um sistema que estava de pé para pôr no lugar uma imagem
# inexistente. `image inspect` é local e não depende do registry responder.
tag_utilizavel() {
  [ -n "$1" ] && [ "$1" != "$TAG" ] \
    && docker image inspect "$REGISTRY/api:$1" >/dev/null 2>&1 \
    && docker image inspect "$REGISTRY/web:$1" >/dev/null 2>&1
}

# O que decide se o deploy vale é a APLICAÇÃO responder, não o certificado
# existir. O teste anterior fazia `curl https://$HOST/health`, e por isso
# reprovava por dois motivos que nada têm a ver com o código: o Let's Encrypt
# ainda não ter emitido o certificado (emissão é assíncrona e pode levar
# minutos) ou o domínio canônico não apontar para esta VM. Aqui a fumaça bate
# nos upstreams pela rede interna do compose: prova que o Caddy está de pé,
# que o DNS entre contêineres resolve, que a API subiu e que o web serve.
#
# A janela é de 5 minutos, e não de 1: o PRIMEIRO boot aplica o schema inteiro
# num banco vazio (21 migrações levaram ~67s num e2-micro) e o gate de 60s
# reprovou um deploy que estava correto — a API subiu 4 segundos depois de o
# script desistir. Deploys seguintes passam em segundos, porque só aplicam as
# migrações novas; a janela larga só custa tempo quando algo está errado.
saudavel() {
  local limite=$((SECONDS + 300))
  while [ "$SECONDS" -lt "$limite" ]; do
    if docker compose exec -T caddy wget -q -O /dev/null -T 5 http://api:8000/health 2>/dev/null \
      && docker compose exec -T caddy wget -q -O /dev/null -T 5 http://web:80/ 2>/dev/null; then
      return 0
    fi
    sleep 3
  done
  return 1
}

# Informativo, NUNCA reprova. Um certificado que ainda não saiu é um problema
# de DNS ou de propagação, não do build que acabou de ser publicado — mas
# precisa aparecer no log, senão o site fica inacessível pelo domínio e o
# deploy segue verde sem ninguém notar.
conferir_tls() {
  if curl -fsS -m 10 --resolve "$HOST:443:127.0.0.1" "https://$HOST/health" >/dev/null 2>&1; then
    echo "tls: $HOST servindo com certificado válido"
  else
    echo "AVISO: $HOST ainda não serve por HTTPS. O produto está no ar, mas não" >&2
    echo "       por este domínio. Confira se o A dele aponta para esta VM e veja" >&2
    echo "       'docker compose logs caddy | grep acme'." >&2
  fi
}

ANTERIOR_REAL="$ANTERIOR"
subir "$TAG" || { echo "não foi possível puxar/subir $TAG" >&2; exit 1; }
if saudavel; then
  # `-a` para valer: sem ele o prune só remove imagem órfã, e cada deploy
  # deixava a imagem antiga com tag ocupando o disco de 20 GB até encher (e
  # disco cheio leva o Postgres junto). O Docker nunca apaga imagem em uso.
  docker image prune -af --filter "until=168h" >/dev/null 2>&1 || true
  echo "ok: $TAG no ar"
  conferir_tls
  df -h / | tail -1
  exit 0
fi

echo "a aplicação não respondeu em 5 minutos com a tag $TAG" >&2
docker compose logs --tail=60 api >&2 || true
if tag_utilizavel "$ANTERIOR"; then
  echo "voltando para $ANTERIOR" >&2
  ANTERIOR_REAL="$TAG"
  if subir "$ANTERIOR" && saudavel; then
    echo "rollback para $ANTERIOR concluído" >&2
  else
    echo "ROLLBACK TAMBÉM FALHOU: entre na VM" >&2
  fi
else
  # Sem rollback possível, o .env fica descrevendo o que está rodando de fato,
  # mesmo que esteja ruim: um reboot repete o estado atual em vez de trocá-lo
  # por uma imagem que não existe.
  echo "sem tag anterior utilizável ('$ANTERIOR' não está no disco); mantendo $TAG" >&2
  anotar_tag "$TAG"
fi
exit 1
