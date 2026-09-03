#!/usr/bin/env bash
# Roda em todo boot. Idempotente: instala o que falta, monta o disco do banco,
# escreve a configuração a partir do Secret Manager e sobe o compose.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# --- Docker -----------------------------------------------------------------
if ! command -v docker >/dev/null; then
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg >/dev/null
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
fi

# --- Swap -------------------------------------------------------------------
# 1 GB de RAM: sem swap, o build de índice do Postgres ou um pico da API
# derruba o outro. Vem antes de qualquer contêiner subir.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --- O disco do banco -------------------------------------------------------
# Disco separado da VM: substituir a máquina deixa de ser perda de prontuário.
# Formata SÓ se estiver cru; um disco já formatado é remontado como está.
DISCO=/dev/disk/by-id/google-pgdata
if [ -e "$DISCO" ]; then
  if ! blkid "$DISCO" >/dev/null 2>&1; then
    mkfs.ext4 -m 0 -E lazy_itable_init=0,lazy_journal_init=0,discard "$DISCO" >/dev/null
  fi
  mkdir -p /mnt/pgdata
  grep -q ' /mnt/pgdata ' /etc/fstab || echo "$DISCO /mnt/pgdata ext4 discard,defaults,nofail 0 2" >> /etc/fstab
  mountpoint -q /mnt/pgdata || mount /mnt/pgdata
  mkdir -p /mnt/pgdata/data
else
  echo "AVISO: disco do banco não encontrado; os dados ficariam no disco de boot"
  exit 1
fi

mkdir -p /opt/plantaovet && cd /opt/plantaovet
cat > docker-compose.yml <<'EOF'
${compose}
EOF
cat > Caddyfile <<'EOF'
${caddyfile}
EOF
# Os domínios que redirecionam para o canônico. Só entram quando existem: um
# bloco de site com nome vazio é erro de sintaxe e o Caddy não sobe.
if [ -n "${redirects}" ]; then
  cat >> Caddyfile <<'EOF'

${redirects} {
	redir https://${host}{uri} permanent
}
EOF
fi
cat > deploy.sh <<'EOF'
${deploy_sh}
EOF
cat > backup.sh <<'EOF'
${backup_sh}
EOF
chmod +x deploy.sh backup.sh

# --- Segredos ---------------------------------------------------------------
# Dois tipos, e a diferença importa. Antes um único `|| true` engolia qualquer
# erro, e o boot terminava "com sucesso" com DB_PASSWORD e JWT_SECRET vazios:
# o Postgres subia sem senha e a API assinava token com chave vazia, em
# silêncio. Obrigatório que não vem é motivo de parar.
segredo_opcional() {
  gcloud secrets versions access latest --secret="$1" --project="${project_id}" 2>/dev/null | tr -d '\n' || true
}

segredo_obrigatorio() {
  local valor=""
  for _ in 1 2 3 4 5; do
    valor="$(gcloud secrets versions access latest --secret="$1" --project="${project_id}" 2>/dev/null | tr -d '\n')" && [ -n "$valor" ] && break
    sleep 3
  done
  if [ -z "$valor" ]; then
    echo "ERRO: o segredo '$1' está vazio ou ilegível. A stack NÃO vai subir." >&2
    echo "      Semeie com: openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add $1 --data-file=-" >&2
    exit 1
  fi
  printf '%s' "$valor"
}

DB_PASSWORD="$(segredo_obrigatorio db-password)"
JWT_SECRET="$(segredo_obrigatorio jwt-secret)"

# A tag em uso sobrevive ao reboot: sem isto o .env voltaria para "latest",
# que não existe no registry, e a VM reiniciada não subiria.
TAG_ATUAL="$(sed -n 's/^TAG=//p' .env 2>/dev/null || true)"
cat > .env <<EOF
PROJECT_ID=${project_id}
REGISTRY=${registry}
TAG=$${TAG_ATUAL:-latest}
HOST=${host}
FALLBACK_HOST=${fallback}
SITE_URL=${site_url}
BACKUP_BUCKET=${bucket}
DB_PASSWORD=$DB_PASSWORD
JWT_SECRET=$JWT_SECRET
OPENAI_API_KEY=$(segredo_opcional openai-api-key)
ANTHROPIC_API_KEY=$(segredo_opcional anthropic-api-key)
WHATSAPP_TOKEN=$(segredo_opcional whatsapp-token)
WHATSAPP_APP_SECRET=$(segredo_opcional whatsapp-app-secret)
WHATSAPP_VERIFY_TOKEN=$(segredo_opcional whatsapp-verify-token)
WHATSAPP_PHONE_NUMBER_ID=$(segredo_opcional whatsapp-phone-number-id)
EOF
chmod 600 .env

# --- Cron do backup ---------------------------------------------------------
# PATH explícito: o cron roda com um PATH mínimo e não acharia docker nem
# gcloud, e o backup falharia silenciosamente todas as noites.
cat > /etc/cron.d/plantaovet-backup <<'EOF'
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
0 3 * * * root /opt/plantaovet/backup.sh >> /var/log/plantaovet-backup.log 2>&1
EOF
chmod 644 /etc/cron.d/plantaovet-backup

# --- Subir ------------------------------------------------------------------
gcloud auth configure-docker "${region}-docker.pkg.dev" --quiet >/dev/null
# Antes do primeiro deploy não existe imagem publicada: o pull falha e isso é
# normal. `set -e` mataria o script aqui, então o erro é absorvido de propósito.
if docker compose pull --quiet 2>/dev/null; then
  docker compose up -d --remove-orphans
  # O Caddyfile acabou de ser reescrito. Sem isto o contêiner seguiria com a
  # configuração anterior, e uma mudança de domínio nunca valeria.
  docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null || docker compose restart caddy
else
  echo "sem imagem publicada ainda; aguardando o primeiro deploy"
fi
