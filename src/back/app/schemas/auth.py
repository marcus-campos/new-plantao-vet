import uuid
from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    kind: Literal["personal", "station"]
    clinic_id: uuid.UUID
    membership_id: uuid.UUID | None
    role: str | None = None
    #: O que ESTE usuário pode fazer. A interface esconde o que a API recusaria:
    #: oferecer um botão que devolve 403 é pior que não oferecer.
    capabilities: list[str] = []
    #: Se já existe um PIN. Decide se a troca pede o PIN atual: exigir um
    #: valor que não existe deixaria de fora justamente quem nunca definiu um.
    #: Nunca o hash, que não sai da API em rota nenhuma.
    has_pin: bool = False
    #: O teste venceu: a escrita parou. NÃO autoriza nada — quem autoriza é
    #: `capabilities`, que já vem encolhida. Existe para a interface conseguir
    #: EXPLICAR por que os botões sumiram, em vez de parecer quebrada.
    read_only: bool = False


class OperatorResponse(BaseModel):
    """Quem digitou o PIN, e o que essa pessoa pode.

    Sem isto a interface da estação não tinha resposta honesta a dar: `/auth/me`
    devolve papel nulo de propósito (o aparelho não tem papel), então o cliente
    liberava tudo: a IA de gestão inteira aparecia num tablet do corredor, e o
    técnico escrevia uma evolução completa para receber 403 depois de se
    identificar.
    """

    membership_id: uuid.UUID
    name: str
    role: str
    license_number: str | None = None
    license_authority: str | None = None
    capabilities: list[str] = []


class StationLoginRequest(BaseModel):
    """Entrada de um aparelho compartilhado.

    Dois caminhos convivem de propósito. O novo é `device_id` + `device_secret`:
    cada aparelho tem segredo próprio, aparece numa lista e é revogado sozinho.
    O antigo é `station_key`, a senha única da clínica, mantida enquanto houver
    aparelho em campo que só conhece ela: derrubar todos de uma vez para
    estrear um modelo de acesso seria a mesma falha que o modelo novo corrige.
    """

    clinic_slug: str
    station_key: str | None = None
    device_id: uuid.UUID | None = None
    device_secret: str | None = None


class DeviceEnrollRequest(BaseModel):
    """O aparelho apresenta o código de seis dígitos que o administrador leu."""

    clinic_slug: str
    code: str = Field(pattern=r"^\d{6}$")
    #: Como este aparelho vai aparecer na lista. Sem nome, quem revoga escolhe
    #: entre identificadores e não entre aparelhos.
    device_name: str | None = Field(default=None, max_length=80)


class DeviceEnrolledResponse(BaseModel):
    device_id: uuid.UUID
    #: Sai em claro UMA vez: o aparelho guarda, o servidor fica com o hash.
    device_secret: str
    device_name: str


class PinRequest(BaseModel):
    """O PIN digitado no teclado da estação.

    Aceita de 4 a 8 dígitos na LEITURA, mas `SetPinRequest` exige 6 na
    definição: quem já tem um PIN de 4 continua entrando enquanto não troca, e
    ninguém fica de fora da clínica por causa da mudança de tamanho."""

    pin: str = Field(pattern=r"^\d{4,8}$")


class SetPinRequest(BaseModel):
    """Seis dígitos: 10 mil combinações não bastam para uma clínica grande.

    O PIN é único por clínica (dois iguais atribuiriam o ato à pessoa errada),
    e com quatro dígitos o espaço acaba: em algumas centenas de pessoas a
    colisão deixa de ser exceção e vira o caso comum."""

    pin: str = Field(pattern=r"^\d{6}$")


class ChangeMyPinRequest(BaseModel):
    """Trocar o próprio PIN.

    `current_pin` é obrigatório quando já existe um: sem ele, qualquer sessão
    aberta e esquecida num aparelho trocaria o PIN de quem estava logado."""

    current_pin: str | None = Field(default=None, pattern=r"^\d{4,8}$")
    new_pin: str = Field(pattern=r"^\d{6}$")


class OperatorTokenResponse(BaseModel):
    operator_token: str
