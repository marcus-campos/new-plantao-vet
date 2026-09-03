# --- A VM ---------------------------------------------------------------------
# Sem chave JSON em disco: a VM tem identidade própria (metadata server) e é
# com ela que puxa imagens, lê segredos, grava backup e fala com Vertex/FCM.
resource "google_service_account" "vm" {
  account_id   = "plantaovet-vm"
  display_name = "PlantãoVet VM"
}

resource "google_project_iam_member" "vm_roles" {
  for_each = toset([
    "roles/artifactregistry.reader",
    "roles/aiplatform.user",
    "roles/firebasecloudmessaging.admin",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.vm.email}"
}

# Um binding por cofre, em vez de `secretmanager.secretAccessor` no projeto:
# a VM lê o que é dela, e um segredo novo de outro serviço não entra junto.
resource "google_secret_manager_secret_iam_member" "vm_reads" {
  for_each  = google_secret_manager_secret.app
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.vm.email}"
}

# Criar e ler, nunca apagar: quem comprometer a VM não leva os 30 dias de
# dump junto. Quem apaga é a regra de ciclo de vida do bucket.
resource "google_storage_bucket_iam_member" "vm_backups" {
  for_each = toset(["roles/storage.objectCreator", "roles/storage.objectViewer"])
  bucket   = google_storage_bucket.backups.name
  role     = each.value
  member   = "serviceAccount:${google_service_account.vm.email}"
}

# --- O GitHub Actions, sem chave -------------------------------------------
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  # Repositório E branch. Só o repositório deixaria QUALQUER branch dele virar
  # o deployer: bastaria abrir um pull request com o workflow alterado para
  # ganhar a chave de produção. `ref_type == branch` fecha o mesmo furo por tag.
  attribute_condition = join(" && ", [
    "assertion.repository == \"${var.github_repository}\"",
    "assertion.ref == \"refs/heads/main\"",
    "assertion.ref_type == \"branch\"",
  ])
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  account_id   = "github-deployer"
  display_name = "GitHub Actions (deploy)"
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

# O deployer só faz três coisas: empurra imagem, lê a config do push para o
# build do web, e entra na VM por SSH (OS Login) para dizer "atualiza". Não
# roda Terraform: a infra muda da sua máquina, o código muda pelo pipeline.
resource "google_project_iam_member" "deployer_roles" {
  for_each = toset([
    "roles/artifactregistry.writer",
    "roles/compute.osAdminLogin",
    "roles/compute.viewer",
    "roles/iap.tunnelResourceAccessor",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# OS Login como service account exige poder "atuar" como a SA da VM.
resource "google_service_account_iam_member" "deployer_uses_vm_sa" {
  service_account_id = google_service_account.vm.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# Dois cofres, não o projeto inteiro. `secretmanager.secretAccessor` no projeto
# dava ao pipeline a senha do banco e a chave JWT, e ele só precisa da config
# pública do Firebase para o build do web: uma action de terceiro comprometida
# lia tudo.
resource "google_secret_manager_secret_iam_member" "deployer_reads_web_config" {
  for_each  = toset(["web-firebase-config", "web-firebase-vapid-key"])
  secret_id = google_secret_manager_secret.app[each.value].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.deployer.email}"
}
