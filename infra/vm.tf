# IP fixo: o certificado, o DNS (ou o sslip.io) e o hábito de quem acessa
# dependem de o endereço não mudar num reboot.
resource "google_compute_address" "vm" {
  name       = "plantaovet-ip"
  region     = var.region
  depends_on = [google_project_service.apis]
}

locals {
  # Sem domínio, <ip>.sslip.io: resolve para o próprio IP e ganha certificado
  # do Let's Encrypt igual. Serve para testar enquanto o DNS não propaga.
  host     = var.domain != "" ? var.domain : local.fallback_host
  site_url = "https://${local.host}"
  # O sslip.io resolve para o próprio IP sem DNS nenhum, e o Caddy serve ele
  # SEMPRE, ao lado do domínio: é o endereço que funciona no minuto seguinte
  # ao apply, antes de qualquer registro propagar. Sem isto, a instrução de
  # "teste por aqui antes do DNS" apontava para um host que o Caddy não servia.
  fallback_host = "${replace(google_compute_address.vm.address, ".", "-")}.sslip.io"
  # Só com domínio de verdade: pedir certificado de um nome que ninguém
  # aponta daria erro de emissão em todo boot do Caddy.
  redirects = var.domain != "" ? join(", ", var.redirect_domains) : ""
}

resource "google_compute_firewall" "web" {
  name    = "plantaovet-web"
  network = "default"
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["plantaovet"]
}

# SSH só pelo IAP (o túnel do Google), nunca da internet aberta.
#
# A rede `default` do GCP já vem com uma regra `default-allow-ssh` que libera a
# porta 22 para 0.0.0.0/0. Criar a regra do IAP não apaga aquela: as duas
# convivem, e ALLOW mais permissivo vence. Por isso vêm as duas abaixo, com
# prioridade explícita: o IAP passa (800) e o resto do mundo bate no DENY
# (900), que fica na frente da regra default (65534). O bootstrap ainda tenta
# apagar a `default-allow-ssh`; isto aqui é o cinto, caso ela volte.
resource "google_compute_firewall" "ssh_iap" {
  name     = "plantaovet-ssh-iap"
  network  = "default"
  priority = 800
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["plantaovet"]
}

resource "google_compute_firewall" "ssh_deny" {
  name     = "plantaovet-ssh-deny"
  network  = "default"
  priority = 900
  deny {
    protocol = "tcp"
    ports    = ["22"]
  }
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["plantaovet"]
}

# O disco do banco. Separado da VM de propósito: substituir a máquina deixa de
# ser um evento de perda de prontuário.
resource "google_compute_disk" "data" {
  name = "plantaovet-data"
  type = "pd-standard"
  size = 10
  zone = var.zone

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis]
}

# Backups do Postgres: um pg_dump por noite, 30 dias, depois some. Banco que
# mora num disco de VM sem cópia fora dela não é banco de produção.
resource "google_storage_bucket" "backups" {
  name                        = "${var.project_id}-backups"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 30
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_compute_instance" "vm" {
  name         = "plantaovet"
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["plantaovet"]

  boot_disk {
    initialize_params {
      # Debian com Docker instalado pelo startup. 20 GB para o sistema e as
      # imagens; os outros 10 vão para o disco do banco. O free tier são 30 GB
      # de pd-standard no total, então os dois juntos continuam de graça.
      image = "debian-cloud/debian-12"
      size  = 20
      type  = "pd-standard"
    }
  }

  # O banco num disco PRÓPRIO. Antes o volume do Postgres vivia em
  # /var/lib/docker/volumes, no disco de boot, que morre junto com a máquina:
  # perder a VM era perder o prontuário desde o dump das 03:00. Assim o disco
  # sobrevive e é só reanexar.
  attached_disk {
    source      = google_compute_disk.data.id
    device_name = "pgdata"
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.vm.address
    }
  }

  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
    # O que o servidor É: compose, Caddy e os dois scripts. Muda aqui, muda
    # no próximo boot (ou com `sudo google_metadata_script_runner startup`).
    startup-script = templatefile("${path.module}/vm/startup.sh", {
      project_id = var.project_id
      region     = var.region
      registry   = local.registry
      host       = local.host
      redirects  = local.redirects
      site_url   = local.site_url
      bucket     = google_storage_bucket.backups.name
      fallback   = local.fallback_host
      compose    = file("${path.module}/vm/docker-compose.yml")
      caddyfile  = file("${path.module}/vm/Caddyfile")
      deploy_sh  = file("${path.module}/vm/deploy.sh")
      backup_sh  = file("${path.module}/vm/backup.sh")
    })
  }

  # `prevent_destroy`, e NÃO `ignore_changes`. O que estava aqui antes era
  # `ignore_changes = [metadata["startup-script"]]`, com a justificativa de
  # que trocar o startup recriaria a máquina. A justificativa é falsa
  # (`metadata` é atualização in-place; quem recria é `metadata_startup_script`)
  # e o efeito era o oposto do pretendido: o startup carrega o compose, o
  # Caddyfile, o deploy.sh e o backup.sh, então qualquer correção neles dava
  # "No changes" no plan e nunca chegava no servidor.
  #
  # `prevent_destroy` é o que protege: um plan que queira SUBSTITUIR a máquina
  # (trocar zona, nome ou imagem são ForceNew) falha com erro em vez de
  # destruir. O banco agora mora num disco separado, então nem isso o perde.
  lifecycle {
    prevent_destroy = true
  }

  allow_stopping_for_update = true

  # Os cofres e a permissão de leitura precisam existir antes do primeiro
  # boot: é no boot que o startup lê os segredos para o .env.
  depends_on = [
    google_project_iam_member.vm_roles,
    google_secret_manager_secret_iam_member.vm_reads,
  ]
}
