resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "plantaovet"
  format        = "DOCKER"
  description   = "Imagens da API e do web, uma tag por commit."

  # Três versões, catorze dias. O free tier do Artifact Registry é 0,5 GB, e a
  # imagem da API sozinha passa de 250 MB: guardar vinte versões custaria mais
  # que a máquina. Três é o que dá para voltar dois deploys atrás, que é o
  # rollback que existe na prática.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }
  cleanup_policies {
    id     = "drop-old"
    action = "DELETE"
    condition {
      older_than = "1209600s"
    }
  }

  depends_on = [google_project_service.apis]
}

locals {
  registry = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}
