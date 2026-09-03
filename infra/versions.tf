terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # O estado mora num bucket do próprio projeto (criado por bootstrap.sh,
  # porque o bucket que guarda o estado não pode ser criado pelo estado).
  # Versionamento ligado: um apply errado se desfaz voltando a versão.
  backend "gcs" {
    bucket = "plantao-vet-tfstate"
    prefix = "prod"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
