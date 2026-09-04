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
#
# É o plantaovet.com.br e NÃO o plantao.vet porque canônico só pode ser um
# domínio que aponta para esta VM.
#
# Foi `plantaovet.com.br` por um dia, porque o `plantao.vet` estava estacionado
# na GoDaddy: o desafio do Let's Encrypt saía em 3.33.130.190 e voltava 403, o
# Caddy nunca emitia o certificado, e o único domínio que resolvia redirecionava
# 301 para um domínio sem certificado — o produto no ar e inacessível ao mesmo
# tempo.
#
# Em 04/09/2026 o A do plantao.vet passou a apontar para cá, que era a condição
# escrita aqui para a troca. Trocado agora, e não depois, porque o custo é
# crescente: mudar o canônico invalida sessões abertas, o service worker do push
# e a permissão de notificação, todos ligados à ORIGEM. Com zero clínica
# cadastrada isso custa nada; com a primeira em uso, custa o plantão dela.
variable "domain" {
  type    = string
  default = "plantao.vet"
}

# Quem redireciona para o canônico. O Caddy emite certificado para todos
# (senão o navegador mostra erro ANTES de conseguir redirecionar).
variable "redirect_domains" {
  type = list(string)
  #
  # Todo nome desta lista PRECISA apontar para esta VM: um que não aponte põe o
  # Caddy num laço de emissão que falha a cada 5 minutos contra o Let's Encrypt,
  # que tem limite por domínio, e queima a cota que fará falta depois.
  #
  # Os três apontam (verificado por dig em 04/09/2026, inclusive no 8.8.8.8).
  # O `plantaovet.com` fica de fora: continua servindo um site do WebsiteBuilder
  # na GoDaddy e não resolve para cá.
  default = [
    "www.plantao.vet",
    "plantaovet.com.br",
    "www.plantaovet.com.br",
  ]
}
