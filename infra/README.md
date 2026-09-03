# Produção no GCP (projeto `plantao-vet`)

Uma VM, o mesmo `docker compose` que roda na sua máquina, Postgres num disco
próprio com backup noturno para um bucket, e HTTPS automático. É a coisa mais
barata que ainda é produção de verdade.

```
              plantao.vet ─┐
    www.plantao.vet ───────┤
     plantaovet.com.br ────┼──►  IP fixo  ──►  e2-micro (us-central1)
 www.plantaovet.com.br ────┘                   ├── caddy   (HTTPS, /api → api)
      <ip>.sslip.io ───────┘                   ├── web     (nginx)
                                               ├── api     (uvicorn)
                                               └── postgres
                                                    ├─ disco próprio (10 GB)
                                                    └─ pg_dump 03:00 → GCS
```

## Custo

| item | por mês |
|---|---|
| e2-micro + 30 GB de disco (free tier, us-central1) | US$ 0 |
| IP externo fixo | ~US$ 3 |
| Artifact Registry (3 imagens, 0,5 GB grátis) | ~US$ 0,10 |
| Secret Manager (2 versões ativas, 6 grátis) | US$ 0 |
| Backups e egress | US$ 0 (dentro do free tier) |
| **total** | **~US$ 3** |

Conta nova costuma vir com US$ 300 de crédito por 90 dias. O IP é o único item
que não tem como zerar no GCP.

**Quando vender**, em ordem de necessidade: `e2-small` (2 GB, ~US$ 13) →
Cloud SQL gerenciado (~US$ 30, e o `DATABASE_URL` é a única linha que muda) →
Cloud Run. Nada disso precisa ser decidido agora.

## As decisões que valem explicar

- **Postgres na VM, num disco separado.** Cloud SQL custa mais que todo o resto
  junto. O disco é `plantaovet-data`, com `prevent_destroy` e desanexado do
  ciclo de vida da máquina: perder a VM deixa de ser perder o prontuário, e o
  dump noturno cobre o resto.
- **A senha do banco e a chave JWT não passam pelo Terraform.** O que o
  Terraform gera fica em texto plano no `tfstate`, que mora num bucket. O
  `bootstrap.sh` gera com `openssl rand` e grava direto no Secret Manager.
- **Uma origem só.** `plantao.vet` serve o site e a API (`/api/*` pelo Caddy).
  Sessão, service worker do push e permissão de notificação são todos por
  origem: dois domínios servindo o mesmo site dariam duas sessões e duas
  permissões de alerta. Os outros três redirecionam. O `<ip>.sslip.io` é
  servido junto, sempre, e é por ele que dá para testar antes do DNS.
- **O web não sabe onde a API está.** `VITE_API_URL` vai vazio no build e o
  cliente monta caminhos relativos. Funciona em qualquer domínio novo sem
  rebuild, e não existe CORS.
- **Uma instância da API, sempre ligada.** O aprazamento é um scheduler dentro
  do processo e o orçamento de alertas vive na memória dele.
- **Nenhuma chave de service account existe.** A VM tem identidade própria; o
  GitHub Actions troca o token OIDC dele pela SA do deployer, restrita a este
  repositório **e à branch `main`**. O deployer lê dois segredos (a config
  pública do Firebase), não o projeto inteiro.
- **SSH só pelo IAP.** O `bootstrap.sh` apaga a `default-allow-ssh` da rede
  padrão do GCP, e há um DENY explícito para a porta 22 como cinto.

## Primeira vez (na sua máquina)

1. Vincular faturamento (único passo que só você faz):
   https://console.cloud.google.com/billing/linkedaccount?project=plantao-vet
2. `./infra/bootstrap.sh`. No fim ele imprime o IP e os registros de DNS.
3. **DNS**, nos dois registradores, tipo `A`, valor = o IP impresso:

   | nome | tipo | valor |
   |---|---|---|
   | `@` (ou `plantao.vet`) | A | `<IP>` |
   | `www` | A | `<IP>` |
   | `@` (ou `plantaovet.com.br`) | A | `<IP>` |
   | `www` | A | `<IP>` |

   O Caddy emite os certificados sozinho quando o DNS resolver. Antes disso o
   site já responde em `https://<ip-com-hifens>.sslip.io`, com certificado
   válido.
4. Chave VAPID do push: console do Firebase → Cloud Messaging → *Web Push
   certificates* → *Generate key pair*, e
   `printf '%s' 'CHAVE' | gcloud secrets versions add web-firebase-vapid-key --data-file=-`.
5. `git push origin main`.

## Todo deploy depois disso

Push em `main` → **testes e tipos** (se falhar, nada sobe) → build das duas
imagens no runner do GitHub (a e2-micro não constrói imagem Python sem morrer)
→ push para o Artifact Registry → SSH pelo IAP → `deploy.sh <sha>`, que troca
a tag, sobe e confere `/health` por dentro. **Se a API não responder, ele volta
sozinho para a tag anterior** e o job falha.

Rollback manual:

```
gcloud compute ssh plantaovet --zone us-central1-a --tunnel-through-iap \
  --command "sudo /opt/plantaovet/deploy.sh <sha-anterior>"
```

O registry guarda as 3 últimas imagens.

## Mudar a configuração do servidor

`infra/vm/docker-compose.yml`, `Caddyfile`, `deploy.sh` e `backup.sh` viajam
dentro do `startup-script` da VM. Depois de editar e aplicar:

```
cd infra && terraform apply          # grava o metadata novo
gcloud compute ssh plantaovet --zone us-central1-a --tunnel-through-iap \
  --command "sudo google_metadata_script_runner startup"
```

O startup reescreve os arquivos, relê os segredos e recarrega o Caddy. É o
mesmo caminho para "adicionei um segredo novo".

## Segredos

`db-password` e `jwt-secret` são gerados pelo `bootstrap.sh`. Os outros nascem
**sem versão**, e sem versão é recurso desligado (o código já trata credencial
ausente): `openai-api-key`, `anthropic-api-key`, `whatsapp-token`,
`whatsapp-app-secret`, `whatsapp-verify-token`, `whatsapp-phone-number-id`,
`web-firebase-config`, `web-firebase-vapid-key`.

```
printf '%s' 'sk-...' | gcloud secrets versions add openai-api-key --data-file=-
```

## Operar

```
# entrar
gcloud compute ssh plantaovet --zone us-central1-a --tunnel-through-iap

# logs
cd /opt/plantaovet && sudo docker compose logs -f api

# criar o operador da plataforma (quem vende e dá suporte)
sudo docker compose exec api python -m scripts.platform_operator "Marcus Campos" voce@plantao.vet

# backup na hora (confere a integridade do dump antes de subir)
sudo /opt/plantaovet/backup.sh

# restaurar
gcloud storage cp gs://plantao-vet-backups/pg/<arquivo>.sql.gz - | gunzip \
  | sudo docker compose exec -T postgres psql -U plantaovet plantaovet
```
