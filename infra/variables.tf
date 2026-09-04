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
# domínio que aponta para esta VM. O plantao.vet continua estacionado na
# GoDaddy: o desafio do Let's Encrypt para ele sai em 3.33.130.190 e volta 403,
# então o Caddy nunca emite o certificado, e no primeiro deploy real o único
# domínio que resolvia (o .com.br) redirecionava 301 para um domínio sem
# certificado — o produto estava no ar e inacessível ao mesmo tempo.
#
# Quando o A do plantao.vet apontar para cá, é trocar este default e mover o
# plantaovet.com.br para a lista de baixo. A troca de canônico custa as sessões
# abertas, o service worker do push e a permissão de notificação, que são por
# origem; hoje isso é grátis porque ninguém acessou ainda, e cada dia de uso
# real encarece a mudança.
variable "domain" {
  type    = string
  default = "plantaovet.com.br"
}

# Quem redireciona para o canônico. O Caddy emite certificado para todos
# (senão o navegador mostra erro ANTES de conseguir redirecionar).
variable "redirect_domains" {
  type = list(string)
  #
  # O plantao.vet e o www.plantao.vet ficam FORA enquanto estiverem na GoDaddy.
  # Não é economia: um nome aqui que não aponta para esta VM põe o Caddy num
  # laço de emissão que falha a cada 5 minutos contra o Let's Encrypt, que tem
  # limite por domínio — insistir queima a cota que vai ser necessária no dia
  # em que o DNS estiver certo. Voltam para cá junto com o A.
  default = [
    "www.plantaovet.com.br",
  ]
}
