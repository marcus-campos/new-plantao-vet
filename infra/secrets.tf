# Os segredos da aplicação. O Terraform cria os COFRES; os valores entram por
# fora, e por dois motivos:
#
# 1. O que o Terraform gera fica em texto plano no tfstate. `random_password`
#    para a senha do banco e para o JWT punha as duas chaves mais sensíveis do
#    sistema num arquivo do bucket. O bootstrap gera com `openssl rand` e
#    grava direto no Secret Manager: nunca passam pelo estado.
# 2. Uma "versão inicial vazia" para cada cofre custava dinheiro: o Secret
#    Manager dá 6 versões ativas de graça e isto criava 10 no primeiro apply,
#    antes de o produto existir. Cofre sem versão custa zero, e o startup da
#    VM já trata segredo ausente como recurso desligado.

locals {
  # Gerados pelo bootstrap com openssl, antes de a VM subir. Sem eles a API
  # não sobe, e é isso que o startup verifica.
  required_secrets = [
    "db-password",
    "jwt-secret",
  ]

  # Preenchidos por você quando (e se) quiser o recurso. Vazio = desligado.
  optional_secrets = [
    "openai-api-key",
    "anthropic-api-key",
    "whatsapp-token",
    "whatsapp-app-secret",
    "whatsapp-verify-token",
    "whatsapp-phone-number-id",
    # Push no navegador: a config pública do app web do Firebase (JSON numa
    # linha) e a chave VAPID. Entram no BUILD do web, não na API.
    "web-firebase-config",
    "web-firebase-vapid-key",
  ]

  all_secrets = concat(local.required_secrets, local.optional_secrets)
}

resource "google_secret_manager_secret" "app" {
  for_each  = toset(local.all_secrets)
  secret_id = each.value
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}
