"""O teste vence: o que a clínica ainda pode fazer.

Sem banco e sem HTTP de propósito — é a regra pura. O gate na API tem teste
próprio em test_trial_expiry.py.
"""

from datetime import UTC, datetime, timedelta

from app.models.clinic import Clinic
from app.permissions import (
    AUDIT_READ,
    CHARGES_READ,
    HOSPITALIZATION_DISCHARGE,
    OWNER_READ,
    PRESCRIPTION_CREATE,
    READ_ONLY_CAPABILITIES,
    RECORD_READ,
    TASK_EXECUTE,
    TEAM_READ,
    capabilities_of,
)

ONTEM = datetime.now(UTC) - timedelta(days=1)
AMANHA = datetime.now(UTC) + timedelta(days=1)


def test_trial_vencido_e_somente_leitura():
    clinic = Clinic(subscription_status="trial", trial_ends_at=ONTEM)
    assert clinic.is_read_only is True


def test_trial_vigente_nao_e_somente_leitura():
    clinic = Clinic(subscription_status="trial", trial_ends_at=AMANHA)
    assert clinic.is_read_only is False


def test_trial_sem_data_nao_e_somente_leitura():
    # Clínica de cortesia, sem data de fim: teste que não vence não vira nada.
    clinic = Clinic(subscription_status="trial", trial_ends_at=None)
    assert clinic.is_read_only is False


def test_assinatura_ativa_ignora_data_de_teste_no_passado():
    # Quem assinou carrega um trial_ends_at velho. Só `trial` vence.
    clinic = Clinic(subscription_status="active", trial_ends_at=ONTEM)
    assert clinic.is_read_only is False


def test_a_alta_sobrevive_ao_vencimento():
    # Congelar um sistema com paciente internado dentro seria prender o
    # animal num software vencido.
    assert HOSPITALIZATION_DISCHARGE in READ_ONLY_CAPABILITIES


def test_as_cinco_leituras_sensiveis_sobrevivem():
    for capability in (OWNER_READ, RECORD_READ, TEAM_READ, CHARGES_READ, AUDIT_READ):
        assert capability in READ_ONLY_CAPABILITIES


def test_escrita_clinica_nao_sobrevive():
    assert PRESCRIPTION_CREATE not in READ_ONLY_CAPABILITIES
    assert TASK_EXECUTE not in READ_ONLY_CAPABILITIES


def test_capabilities_of_filtra_quando_somente_leitura():
    vet_normal = capabilities_of("vet")
    vet_vencido = capabilities_of("vet", read_only=True)
    assert PRESCRIPTION_CREATE in vet_normal
    assert PRESCRIPTION_CREATE not in vet_vencido
    assert RECORD_READ in vet_vencido
    assert HOSPITALIZATION_DISCHARGE in vet_vencido
    assert vet_vencido < vet_normal


def test_capabilities_of_do_admin_perde_configurar():
    # É a armadilha do banner: `clinic.configure` é escrita e some da lista.
    # Por isso o banner de vencido não pode depender dela para aparecer.
    from app.permissions import CLINIC_CONFIGURE

    assert CLINIC_CONFIGURE not in capabilities_of("admin", read_only=True)


def test_papel_nulo_continua_sem_nada():
    assert capabilities_of(None, read_only=True) == frozenset()
