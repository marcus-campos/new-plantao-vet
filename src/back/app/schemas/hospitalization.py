import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.vitals import VitalKind, reference_for


class HospitalizationCreate(BaseModel):
    patient_id: uuid.UUID
    vet_membership_id: uuid.UUID
    kennel_id: uuid.UUID | None = None
    consent_status: Literal["consent_recorded", "emergency_no_consent"]
    consent_reason: str | None = None


class HospitalizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    kennel_id: uuid.UUID | None
    vet_membership_id: uuid.UUID
    status: str
    admitted_at: datetime
    ended_at: datetime | None
    outcome_note: str | None
    consent_status: str
    consent_reason: str | None
    #: Desde quando o paciente está em jejum. É o que a tarja "Jejum desde 22h"
    #: do cabeçalho lê – e o que faz a alimentação avisar antes de ser dada.
    fasting_since: datetime | None = None
    fasting_reason: str | None = None


class HospitalizationCreated(BaseModel):
    hospitalization: HospitalizationOut
    warning: str | None = None


class PatientSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    species: str
    breed: str | None
    weight_kg: Decimal | None


class VitalKindOut(BaseModel):
    """Um parâmetro da grade de monitoramento, já resolvido para ESTA espécie.

    Vem na ficha porque a faixa de referência é o que transforma "82" em
    informação: sem ela a tela mostra um número solto e quem está de plantão
    tem de saber de cabeça o normal de cada espécie (mockup `AppTarefa`:
    "82 mg/dL · faixa de referência 70–150")."""

    kind: str
    label_key: str
    unit: str
    value_type: str
    decimals: int
    choices: list[str] = []
    normal_choices: list[str] = []
    #: `None` quando a espécie do paciente não tem faixa conhecida (exótico).
    #: A interface mostra o campo SEM referência – nunca a faixa do cão.
    reference_low: float | None = None
    reference_high: float | None = None
    #: Limite fisiológico: o que a API recusa. Fora da referência é achado.
    min_value: float | None = None
    max_value: float | None = None
    #: Faixa que ainda depende de confirmação de veterinário (spec §8.1).
    needs_vet_review: bool = False

    @classmethod
    def from_kind(cls, vital: "VitalKind", species: str | None) -> "VitalKindOut":
        reference = reference_for(vital, species)
        return cls(
            kind=vital.kind,
            label_key=vital.label_key,
            unit=vital.unit,
            value_type=vital.value_type,
            decimals=vital.decimals,
            choices=list(vital.choices),
            normal_choices=list(vital.normal_choices),
            reference_low=reference.low if reference else None,
            reference_high=reference.high if reference else None,
            min_value=vital.plausible.low if vital.plausible else None,
            max_value=vital.plausible.high if vital.plausible else None,
            needs_vet_review=vital.needs_vet_review,
        )


class DrugDoseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    drug: str
    count_24h: int
    count_total: int
    #: `None` quando a dose prescrita não é legível como número ou o fármaco
    #: mudou de unidade na internação: contagem sem soma, nunca soma inventada.
    dose_sum_24h: Decimal | None = None
    dose_sum_total: Decimal | None = None
    dose_unit: str | None = None


class HospitalizationDetail(BaseModel):
    hospitalization: HospitalizationOut
    patient: PatientSummary | None = None
    kennel_name: str | None = None
    vet_name: str | None = None
    vet_license: str | None = None
    # Reservados aqui para o contrato não mudar: Task 9 preenche prescriptions,
    # Task 13 preenche tasks.
    prescriptions: list[Any] = []
    tasks: list[Any] = []
    #: A grade de monitoramento que a clínica pode preencher neste paciente.
    vitals: list[VitalKindOut] = []
    #: Quanto de cada fármaco este paciente já recebeu (24h e internação).
    drug_doses: list[DrugDoseOut] = []


class OutcomeRequest(BaseModel):
    outcome: Literal["discharged", "died", "left_ama"]
    note: str | None = None
    confirm_pending_tasks: bool = False


class FastingStart(BaseModel):
    """Início do jejum. `since` permite registrar o jejum que começou às 22h
    quando quem documenta só chegou ao sistema às 23h – a mesma razão de
    `performed_at` existir na execução (a hora do ato, não a do apontamento)."""

    reason: str | None = None
    since: datetime | None = None
