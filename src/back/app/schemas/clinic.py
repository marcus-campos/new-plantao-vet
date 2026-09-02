import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.clinic import UnitSystem


class ClinicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    locale: str
    currency: str
    unit_system: UnitSystem
    timezone: str
    #: Área de atuação: define identificadores do paciente e retenção.
    compliance_profile: str
    anchors: dict[str, list[str]]
    default_prescriptions: list[dict[str, Any]]
    #: Janelas de tolerância da clínica, em minutos. A tela dizia "só leitura:
    #: nada aqui é configurável" ao lado de três números cravados no código.
    tolerance_critical_minutes: int = 30
    tolerance_normal_minutes: int = 60
    tolerance_daily_minutes: int = 120
    plan_tier: str | None
    #: O nome do plano, para a tela: o código é chave, o nome é o que se lê.
    plan_name: str | None = None
    bed_limit: int | None
    station_key_version: int
    # Timbre do prontuário: sem estes três o PDF entregue ao tutor sai sem
    # endereço, telefone nem CNPJ. Só o administrador os edita, e é por isso
    # que moram aqui e não em `ClinicProfileOut` (aberto a todo membro).
    address: str | None = None
    phone: str | None = None
    tax_id: str | None = None
    # Contagem de internações ativas: a tela de configurações mostra o uso
    # atual ao lado de bed_limit (o limite de leitos é suave, spec §5).
    active_hospitalizations: int = 0


class IdentifierKindOut(BaseModel):
    """Um jeito de identificar o paciente, do perfil de compliance da clínica.

    O front NÃO tem lista fixa de campos: ele desenha o que vier daqui. É isso
    que faz a mesma tela pedir microchip na veterinária e CPF/CNS na saúde
    humana sem uma linha de código a mais."""

    kind: str
    #: Chave de tradução (ADR-0004: a API devolve chave, nunca prosa).
    label_key: str
    pattern: str | None


class ClinicProfileOut(BaseModel):
    """O contrato de regionalização da clínica, aberto a todo membro.

    Fuso, locale, moeda e sistema de unidades vivem aqui, e não em `ClinicOut`,
    que carrega plano e limite de leitos e por isso é do administrador. Sem eles
    abertos, o cliente formatava horário calculado no fuso da clínica usando o
    relógio do aparelho: um quiosque em UTC mostrava a dose das 10h como 13h.
    """

    profile: str
    locale: str = "pt-BR"
    currency: str = "BRL"
    unit_system: UnitSystem = UnitSystem.metric
    timezone: str = "UTC"
    #: Como a área se chama na tela ("Veterinária", "Saúde humana").
    name_key: str
    #: Como se chama quem responde pelo paciente: tutor / responsável.
    responsible_label_key: str
    patient_identifier_kinds: list[IdentifierKindOut]
    retention_years: int
    license_authority_label_key: str
    #: Estado da assinatura, para a interface avisar (trial acabando, boleto
    #: em atraso) sem expor nada de comercial: só o status e a data.
    subscription_status: str = "active"
    trial_ends_at: datetime | None = None


class ClinicUpdate(BaseModel):
    """O que a PRÓPRIA clínica pode mudar em si.

    Plano e limite de leitos não estão aqui de propósito: o leito é a unidade
    de cobrança, e ficava editável pelo administrador da clínica. Quem vende
    é quem muda, em `/platform/clinics/{id}`. `extra="forbid"` para que um
    cliente que ainda mande `bed_limit` receba 422 em vez de silêncio: campo
    ignorado em silêncio parece campo aceito."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    locale: str | None = Field(default=None, min_length=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    unit_system: UnitSystem | None = None
    timezone: str | None = Field(default=None, min_length=1)
    compliance_profile: str | None = Field(default=None, min_length=2)
    anchors: dict[str, Any] | None = None
    default_prescriptions: list[dict[str, Any]] | None = None
    #: Janelas de tolerância em minutos. O piso de 5 e o teto de 24h não são
    #: burocracia: uma janela de zero faria toda tarefa nascer atrasada, e uma
    #: de uma semana faria "atrasada" nunca acontecer. Nos dois casos o estado
    #: para de informar qualquer coisa, que é o oposto do que ele serve.
    tolerance_critical_minutes: int | None = Field(default=None, ge=5, le=1440)
    tolerance_normal_minutes: int | None = Field(default=None, ge=5, le=1440)
    tolerance_daily_minutes: int | None = Field(default=None, ge=5, le=1440)
    #: Timbre do prontuário. Texto livre de propósito: endereço e identificador
    #: fiscal mudam de formato em cada país, e um `pattern` aqui impediria a
    #: clínica de imprimir o próprio timbre. `null` explícito apaga o campo.
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=60)
    tax_id: str | None = Field(default=None, max_length=40)


class StationKeyRotated(BaseModel):
    # A chave em claro sai daqui UMA vez: só o hash fica no banco.
    station_key: str
    station_key_version: int
