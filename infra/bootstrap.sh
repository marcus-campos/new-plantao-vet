#!/usr/bin/env bash
# Primeira vez, na sua máquina, com o seu gcloud. Depois disto o GitHub
# Actions publica sozinho, sem chave nenhuma.
#
#   ./infra/bootstrap.sh
#
# É idempotente: rodar de novo não duplica nada nem gera cobrança nova.
set -euo pipefail

PROJECT="${PROJECT:-plantao-vet}"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-us-central1-a}"
BUCKET="${BUCKET:-plantao-vet-tfstate}"
REPO="${REPO:-marcus-campos/new-plantao-vet}"
cd "$(dirname "$0")"

say() { printf '\n\033[1m› %s\033[0m\n' "$*"; }
tf() { terraform "$@" -input=false -var "project_id=$PROJECT" -var "region=$REGION" -var "zone=$ZONE" -var "github_repository=$REPO"; }

gcloud config set project "$PROJECT" >/dev/null

say "1/6 faturamento"
if [ "$(gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)' 2>/dev/null)" != "True" ]; then
  cat <<TXT
Este projeto não tem conta de faturamento vinculada, e sem ela nada abaixo
existe (nem o que é grátis: o free tier exige uma conta ativa).

  https://console.cloud.google.com/billing/linkedaccount?project=$PROJECT

Cartão é exigido, mas conta nova costuma vir com US\$ 300 de crédito por 90
dias, e esta arquitetura fica em ~US\$ 3/mês depois disso.
TXT
  exit 1
fi

say "2/6 APIs, bucket do estado e a porta 22"
gcloud services enable \
  compute.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudresourcemanager.googleapis.com serviceusage.googleapis.com storage.googleapis.com \
  iap.googleapis.com firebase.googleapis.com fcm.googleapis.com aiplatform.googleapis.com

if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" \
    --uniform-bucket-level-access --public-access-prevention
fi
# Versionamento: um apply errado se desfaz voltando a versão do estado. E o
# estado guarda dado sensível, então o bucket nunca vira público.
gcloud storage buckets update "gs://$BUCKET" --versioning

# A rede `default` do GCP vem com a porta 22 aberta para a internet inteira.
# O Terraform cria a regra do IAP e um DENY explícito, mas apagar a original é
# mais limpo do que competir com ela por prioridade.
for regra in default-allow-ssh default-allow-rdp; do
  gcloud compute firewall-rules delete "$regra" --quiet >/dev/null 2>&1 || true
done

say "3/6 cofres dos segredos"
# Os cofres primeiro, sozinhos: as chaves precisam existir ANTES de a VM
# subir, e elas não passam pelo Terraform (o que ele gera fica em texto plano
# no tfstate; a senha do banco e a chave JWT não podem estar lá).
terraform init -input=false
tf apply -auto-approve -target=google_secret_manager_secret.app

semear() {
  if gcloud secrets versions access latest --secret="$1" >/dev/null 2>&1; then
    echo "  $1: já tem valor, mantido"
  else
    openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add "$1" --data-file=- >/dev/null
    echo "  $1: gerado"
  fi
}
semear db-password
semear jwt-secret

say "4/6 o resto da infraestrutura"
tf apply -auto-approve
IP="$(terraform output -raw ip)"

say "5/6 Firebase: app web para o push no navegador"
TOKEN="$(gcloud auth print-access-token)"
api() { curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" "$@"; }
api -X POST "https://firebase.googleapis.com/v1beta1/projects/$PROJECT:addFirebase" -d '{}' >/dev/null 2>&1 || true
sleep 3
lista_app() {
  api "https://firebase.googleapis.com/v1beta1/projects/$PROJECT/webApps" \
    | python3 -c 'import sys,json;a=[x for x in json.load(sys.stdin).get("apps",[]) if x.get("displayName")=="PlantaoVet Web"];print(a[0]["appId"] if a else "")'
}
APP_ID="$(lista_app)"
if [ -z "$APP_ID" ]; then
  api -X POST "https://firebase.googleapis.com/v1beta1/projects/$PROJECT/webApps" \
    -d '{"displayName":"PlantaoVet Web"}' >/dev/null
  sleep 6
  APP_ID="$(lista_app)"
fi
if [ -n "$APP_ID" ]; then
  CONFIG="$(api "https://firebase.googleapis.com/v1beta1/projects/$PROJECT/webApps/$APP_ID/config" \
    | python3 -c '
import sys, json
c = json.load(sys.stdin)
campos = ("apiKey","authDomain","projectId","storageBucket","messagingSenderId","appId")
print(json.dumps({k: c[k] for k in campos if k in c}, separators=(",",":")))' | tr -d '\n')"
  # Só grava se mudou: cada versão a mais é cobrada depois das 6 gratuitas, e
  # este script é feito para rodar de novo.
  ATUAL="$(gcloud secrets versions access latest --secret=web-firebase-config 2>/dev/null | tr -d '[:space:]' || true)"
  if [ "$CONFIG" != "$ATUAL" ]; then
    printf '%s' "$CONFIG" | gcloud secrets versions add web-firebase-config --data-file=- >/dev/null
    echo "  config do app web atualizada"
  else
    echo "  config do app web já estava correta"
  fi
else
  echo "  não consegui criar o app web; crie no console e cole o firebaseConfig em web-firebase-config"
fi

say "6/6 GitHub: o que o Actions precisa"
gh secret set GCP_WIF_PROVIDER --repo "$REPO" --body "$(terraform output -raw workload_identity_provider)"
gh secret set GCP_DEPLOYER_SA --repo "$REPO" --body "$(terraform output -raw deployer_service_account)"
gh variable set GCP_PROJECT_ID --repo "$REPO" --body "$PROJECT"
gh variable set GCP_REGION --repo "$REPO" --body "$REGION"
gh variable set GCP_ZONE --repo "$REPO" --body "$ZONE"
gh variable set GCP_VM_NAME --repo "$REPO" --body "plantaovet"

SSLIP="$(echo "$IP" | tr '.' '-').sslip.io"
cat <<TXT

────────────────────────────────────────────────────────────────────
PRONTO. Falta o DNS, a chave do push, e o primeiro deploy.

1) DNS: aponte os dois domínios para $IP (registros A):

     plantao.vet.            A   $IP
     www.plantao.vet.        A   $IP
     plantaovet.com.br.      A   $IP
     www.plantaovet.com.br.  A   $IP

   No registro.br o tipo é "A" e o valor é só o IP. Propaga em minutos a
   algumas horas; o Caddy emite os certificados sozinho quando resolver.

   O site JÁ responde, sem esperar DNS, em:
     https://$SSLIP

2) Chave VAPID do push (não há API para gerá-la):
     https://console.firebase.google.com/project/$PROJECT/settings/cloudmessaging
   Web Push certificates › Generate key pair › copie a chave e rode:
     printf '%s' 'A_CHAVE' | gcloud secrets versions add web-firebase-vapid-key --data-file=-

3) git push origin main  (o workflow Deploy testa e publica)

Segredos opcionais (sem versão = recurso desligado):
  printf '%s' 'sk-...' | gcloud secrets versions add openai-api-key --data-file=-
  idem: anthropic-api-key, whatsapp-token, whatsapp-app-secret,
        whatsapp-verify-token, whatsapp-phone-number-id
────────────────────────────────────────────────────────────────────
TXT
