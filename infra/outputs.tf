output "site_url" {
  value = local.site_url
}

output "ip" {
  value = google_compute_address.vm.address
}

output "registry" {
  value = local.registry
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  value = google_service_account.deployer.email
}

output "vm" {
  value = { name = google_compute_instance.vm.name, zone = var.zone }
}
