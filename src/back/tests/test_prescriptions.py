import uuid

import sqlalchemy as sa

from app.models import Prescription
from app.schemas.prescription import default_tolerance
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_patient,
    make_user,
)
from tests.helpers import bearer, personal_token


async def _vet(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(
        session,
        clinic=clinic,
        user=user,
        role="vet",
        license_number="12345",
        license_authority="CRMV-SP",
    )
    return clinic, membership


def test_tolerancia_default_por_criticidade():
    assert default_tolerance("critical", 480) == 30
    assert default_tolerance("normal", 480) == 60
    assert default_tolerance("normal", 1440) == 120
    assert default_tolerance("critical", 1440) == 30


def test_tolerancia_da_clinica_vence_o_default():
    """As janelas ISMP são o ponto de partida, não a lei.

    Uma UTI com bomba de infusão e um hotelzinho de pós-operatório não têm o
    mesmo conceito de atraso, e a janela decide o que o sistema inteiro chama
    de atrasada. Sem a clínica em mãos valem os defaults, para o chamador que
    só está validando um payload."""

    class ClinicaApertada:
        tolerance_critical_minutes = 15
        tolerance_normal_minutes = 30
        tolerance_daily_minutes = 90

    apertada = ClinicaApertada()
    assert default_tolerance("critical", 480, apertada) == 15
    assert default_tolerance("normal", 480, apertada) == 30
    assert default_tolerance("normal", 1440, apertada) == 90
    assert default_tolerance("normal", 480, None) == 60


async def test_prescricao_nasce_com_a_tolerancia_da_clinica(client, session):
    """A janela configurada vale para a prescrição criada de verdade.

    Não basta a função pura respeitar a clínica: a rota precisa passar a
    clínica adiante. Ela carregava a clínica DEPOIS de montar a prescrição."""
    clinic, membership = await _vet(session)
    clinic.tolerance_normal_minutes = 25
    clinic.tolerance_critical_minutes = 10
    await session.flush()
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Dipirona 25 mg/kg IV",
            "frequency_minutes": 480,
            "criticality": "normal",
            "details": {"drug": "dipirona"},
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["tolerance_minutes"] == 25

    critica = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Noradrenalina",
            "frequency_minutes": 480,
            "criticality": "critical",
            "details": {"drug": "noradrenalina"},
        },
        headers=bearer(personal_token(membership)),
    )
    assert critica.status_code == 201, critica.text
    assert critica.json()["tolerance_minutes"] == 10

    # A prescrição que define a própria janela continua mandando nela.
    propria = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Ondansetrona",
            "frequency_minutes": 480,
            "criticality": "normal",
            "tolerance_minutes": 45,
            "details": {"drug": "ondansetrona"},
        },
        headers=bearer(personal_token(membership)),
    )
    assert propria.status_code == 201, propria.text
    assert propria.json()["tolerance_minutes"] == 45


async def test_prescricao_recorrente(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Dipirona 25 mg/kg IV",
            "frequency_minutes": 480,
            "duration_hours": 72,
            "criticality": "normal",
            "details": {"drug": "dipirona", "dose": "25 mg/kg", "route": "IV"},
            "price_minor": 1800,
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tolerance_minutes"] == 60
    assert body["ends_at"] is not None
    assert body["first_dose_now"] is False


async def test_recorrente_sem_frequencia_e_recusada(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Sem frequência",
            "criticality": "normal",
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_continua_exige_taxa(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    sem_taxa = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "continuous",
            "category": "fluids",
            "name": "Ringer Lactato",
            "frequency_minutes": 120,
            "criticality": "normal",
            "details": {},
        },
        headers=bearer(personal_token(membership)),
    )
    assert sem_taxa.status_code == 422

    com_taxa = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "continuous",
            "category": "fluids",
            "name": "Ringer Lactato",
            "frequency_minutes": 120,
            "criticality": "normal",
            "details": {"rate_ml_h": 60},
        },
        headers=bearer(personal_token(membership)),
    )
    assert com_taxa.status_code == 201


async def test_prn_aceita_guardrails_e_dispensa_frequencia(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "prn",
            "category": "medication",
            "name": "Metadona 0,2 mg/kg IM",
            "criticality": "critical",
            "max_doses_24h": 4,
            "min_interval_minutes": 240,
            "details": {"drug": "metadona"},
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["frequency_minutes"] is None
    assert resp.json()["tolerance_minutes"] == 30


async def test_admissao_cria_cerimonias_no_locale_da_clinica(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    created = (
        await client.post(
            "/api/v1/hospitalizations",
            json={
                "patient_id": str(patient.id),
                "vet_membership_id": str(membership.id),
                "consent_status": "consent_recorded",
            },
            headers=bearer(personal_token(membership)),
        )
    ).json()["hospitalization"]

    rows = list(
        (
            await session.execute(
                sa.select(Prescription).where(
                    Prescription.hospitalization_id == created["id"]
                )
            )
        ).scalars()
    )
    nomes = sorted(row.name for row in rows)
    assert nomes == ["Contato com o tutor", "Evolução diária"]
    assert all(row.category == "care" for row in rows)
    assert all(row.frequency_minutes == 1440 for row in rows)


async def test_cerimonias_em_ingles_quando_a_clinica_e_en(client, session):
    clinic = await make_clinic(session, slug="us-clinic", locale="en")
    clinic, membership = await _vet(session, clinic)
    patient = await make_patient(session, clinic=clinic)
    created = (
        await client.post(
            "/api/v1/hospitalizations",
            json={
                "patient_id": str(patient.id),
                "vet_membership_id": str(membership.id),
                "consent_status": "consent_recorded",
            },
            headers=bearer(personal_token(membership)),
        )
    ).json()["hospitalization"]

    rows = list(
        (
            await session.execute(
                sa.select(Prescription).where(
                    Prescription.hospitalization_id == created["id"]
                )
            )
        ).scalars()
    )
    assert sorted(row.name for row in rows) == ["Daily progress note", "Owner contact"]


async def test_detalhe_da_internacao_traz_prescricoes(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Dipirona",
            "frequency_minutes": 480,
            "criticality": "normal",
            "details": {"drug": "dipirona"},
        },
        headers=token,
    )
    detail = (await client.get(f"/api/v1/hospitalizations/{hosp.id}", headers=token)).json()
    assert [item["name"] for item in detail["prescriptions"]] == ["Dipirona"]


async def test_isolamento_de_tenant(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    hosp_b = await make_hospitalization(session, clinic=clinic_b)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp_b.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Invasora",
            "frequency_minutes": 480,
            "criticality": "normal",
            "details": {},
        },
        headers=bearer(personal_token(membership_a)),
    )
    assert resp.status_code == 404


async def test_preco_vem_do_catalogo_e_fica_congelado(client, session):
    """Ninguém digita centavos: escolhe o item e o valor vem dele – copiado, não
    referenciado. Reajustar a tabela depois não mexe na prescrição."""
    from app.models.price_list_item import PriceListItem

    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    item = PriceListItem(
        clinic_id=clinic.id,
        code="MED-018",
        name="Dipirona sódica 500 mg/ml",
        category="medication",
        unit="por dose",
        price_minor=1800,
    )
    session.add(item)
    await session.flush()

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Dipirona 25 mg/kg IV",
            "frequency_minutes": 480,
            "criticality": "normal",
            "details": {"drug": "dipirona"},
            "price_list_item_id": str(item.id),
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["price_minor"] == 1800
    assert resp.json()["price_list_item_id"] == str(item.id)

    prescricao = await session.get(Prescription, uuid.UUID(resp.json()["id"]))

    # Reajuste do catálogo NÃO reescreve a prescrição já feita.
    item.price_minor = 2500
    await session.flush()
    await session.refresh(prescricao)
    assert prescricao.price_minor == 1800


async def test_preco_digitado_prevalece_sobre_o_catalogo(client, session):
    """Caso pontual (promoção, cortesia): o valor informado ganha do catálogo."""
    from app.models.price_list_item import PriceListItem

    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    item = PriceListItem(
        clinic_id=clinic.id,
        name="Dipirona",
        category="medication",
        unit="por dose",
        price_minor=1800,
    )
    session.add(item)
    await session.flush()

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Dipirona",
            "frequency_minutes": 480,
            "criticality": "normal",
            "details": {},
            "price_list_item_id": str(item.id),
            "price_minor": 0,
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["price_minor"] == 0


async def test_item_de_catalogo_de_outra_clinica_e_404(client, session):
    from app.models.price_list_item import PriceListItem

    clinic_a, membership_a = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic_a, membership=membership_a)
    clinic_b = await make_clinic(session, slug="clinica-precos-b")
    item_b = PriceListItem(
        clinic_id=clinic_b.id,
        name="De outra clínica",
        category="medication",
        unit="por dose",
        price_minor=999,
    )
    session.add(item_b)
    await session.flush()

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Invasora",
            "frequency_minutes": 480,
            "criticality": "normal",
            "details": {},
            "price_list_item_id": str(item_b.id),
        },
        headers=bearer(personal_token(membership_a)),
    )
    assert resp.status_code == 404
