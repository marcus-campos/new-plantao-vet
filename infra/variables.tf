variable "project_id" {
  type    = string
  default = "plantao-vet"
}

# Free tier do GCP: UMA e2-micro por conta de faturamento, só em us-west1,
# us-central1 ou us-east1, com 30 GB de disco padrão. Fora dessas regiões a
# mesma máquina custa ~US$ 7/mês. São Paulo ficaria ~150 ms mais perto, e
# custaria; por enquanto não vendeu nada, então a latência é o preço certo.
variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
}

# e2-micro: 1 GB de RAM compartilhado. Postgres + API + nginx + Caddy cabem,
# com swap. Se a clínica de teste sentir lentidão, `e2-small` (2 GB) custa
# ~US$ 13/mês e é uma linha aqui.
variable "machine_type" {
  type    = string
  default = "e2-micro"
}

variable "github_repository" {
  type    = string
  default = "marcus-campos/new-plantao-vet"
}

# O domínio CANÔNICO: o site inteiro vive nele, e os outros redirecionam.
#
# Uma origem só não é preferência estética. Sessão, service worker do push e
# permissão de notificação são todos por origem: servir o mesmo site em
# plantao.vet e plantaovet.com.br daria duas sessões, dois workers e uma
# permissão de alerta que a pessoa concedeu "no outro site".
#
# Vazio usa <ip>.sslip.io, que resolve para o próprio IP e ganha certificado
# do Let's Encrypt igual: dá para testar antes de o DNS propagar.
variable "domain" {
  type    = string
  default = "plantao.vet"
}

# Quem redireciona para o canônico. O Caddy emite certificado para todos
# (senão o navegador mostra erro ANTES de conseguir redirecionar).
variable "redirect_domains" {
  type = list(string)
  default = [
    "www.plantao.vet",
    "plantaovet.com.br",
    "www.plantaovet.com.br",
  ]
}
