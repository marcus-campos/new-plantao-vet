from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from app.models import AuditEntry
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_kennel,
    make_membership,
    make_patient,
    make_task,
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


async def test_admitir_paciente(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    kennel = await make_kennel(session, clinic=clinic)

    resp = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(patient.id),
            "kennel_id": str(kennel.id),
            "vet_membership_id": str(membership.id),
            "consent_status": "consent_recorded",
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["warning"] is None
    assert body["hospitalization"]["status"] == "active"
    assert body["hospitalization"]["admitted_at"] is not None


async def test_emergencia_sem_termo_exige_motivo(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)

    resp = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(patient.id),
            "vet_membership_id": str(membership.id),
            "consent_status": "emergency_no_consent",
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "consent_reason_required"

    ok = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(patient.id),
            "vet_membership_id": str(membership.id),
            "consent_status": "emergency_no_consent",
            "consent_reason": "Paciente chegou em parada; tutor a caminho.",
        },
        headers=bearer(personal_token(membership)),
    )
    assert ok.status_code == 201


async def test_limite_de_leitos_e_suave(client, session):
    clinic, membership = await _vet(session)
    clinic.bed_limit = 1
    await session.flush()
    token = bearer(personal_token(membership))

    primeiro = await make_patient(session, clinic=clinic, name="Thor")
    segundo = await make_patient(session, clinic=clinic, name="Nina")

    r1 = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(primeiro.id),
            "vet_membership_id": str(membership.id),
            "consent_status": "consent_recorded",
        },
        headers=token,
    )
    assert r1.json()["warning"] is None

    r2 = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(segundo.id),
            "vet_membership_id": str(membership.id),
            "consent_status": "consent_recorded",
        },
        headers=token,
    )
    # Cria assim mesmo: cuidado nunca é bloqueado por plano.
    assert r2.status_code == 201
    assert r2.json()["warning"] == "bed_limit_exceeded"

    entry = (
        (
            await session.execute(
                sa.select(AuditEntry).where(AuditEntry.action == "bed_limit_exceeded")
            )
        )
        .scalars()
        .first()
    )
    assert entry is not None


async def test_desfecho_alta(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    token = bearer(personal_token(membership))
    created = (
        await client.post(
            "/api/v1/hospitalizations",
            json={
                "patient_id": str(patient.id),
                "vet_membership_id": str(membership.id),
                "consent_status": "consent_recorded",
            },
            headers=token,
        )
    ).json()["hospitalization"]

    resp = await client.post(
        f"/api/v1/hospitalizations/{created['id']}/outcome",
        json={"outcome": "discharged", "confirm_pending_tasks": True},
        headers=token,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "discharged"
    assert resp.json()["ended_at"] is not None


async def test_obito_e_retirada_exigem_nota(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    token = bearer(personal_token(membership))
    created = (
        await client.post(
            "/api/v1/hospitalizations",
            json={
                "patient_id": str(patient.id),
                "vet_membership_id": str(membership.id),
                "consent_status": "consent_recorded",
            },
            headers=token,
        )
    ).json()["hospitalization"]

    for outcome in ("died", "left_ama"):
        resp = await client.post(
            f"/api/v1/hospitalizations/{created['id']}/outcome",
            json={"outcome": outcome, "confirm_pending_tasks": True},
            headers=token,
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "outcome_note_required"

    ok = await client.post(
        f"/api/v1/hospitalizations/{created['id']}/outcome",
        json={
            "outcome": "died",
            "note": "Parada cardiorrespiratória às 03:12.",
            "confirm_pending_tasks": True,
        },
        headers=token,
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "died"


async def test_ficha_reserva_prescriptions_e_tasks(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    token = bearer(personal_token(membership))
    created = (
        await client.post(
            "/api/v1/hospitalizations",
            json={
                "patient_id": str(patient.id),
                "vet_membership_id": str(membership.id),
                "consent_status": "consent_recorded",
            },
            headers=token,
        )
    ).json()["hospitalization"]

    resp = await client.get(f"/api/v1/hospitalizations/{created['id']}", headers=token)
    assert resp.status_code == 200
    corpo = resp.json()
    # A admissão cria as cerimônias do dia e o aprazamento delas.
    assert sorted(p["name"] for p in corpo["prescriptions"]) == [
        "Contato com o tutor",
        "Evolução diária",
    ]
    # E cada uma cai na SUA hora. O `anchor` do template era gravado e nunca
    # lido: as duas caíam em `clinic.anchors["1440"]` = 10:00, no mesmo minuto,
    # enquanto a tela de admissão anunciava "todo dia às 16:00" e "às 08:00".
    #
    # A asserção anterior era `tasks == []`, que só passava por acidente:
    # dependia da hora em que a suíte rodasse.
    tz = ZoneInfo(clinic.timezone)
    horas = {
        task["title"]: datetime.fromisoformat(task["scheduled_for"])
        .astimezone(tz)
        .strftime("%H:%M")
        for task in corpo["tasks"]
    }
    for titulo, hora in horas.items():
        assert hora == {"Contato com o tutor": "16:00", "Evolução diária": "08:00"}[titulo]


async def test_isolamento_de_tenant_em_fk_de_body(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    patient_b = await make_patient(session, clinic=clinic_b)

    resp = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(patient_b.id),
            "vet_membership_id": str(membership_a.id),
            "consent_status": "consent_recorded",
        },
        headers=bearer(personal_token(membership_a)),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_ficha_traz_paciente_box_e_vet_para_o_cabecalho(client, session):
    clinic, membership = await _vet(session)
    kennel = await make_kennel(session, clinic=clinic, name="UTI 03")
    patient = await make_patient(session, clinic=clinic, name="Thor", species="dog")
    token = bearer(personal_token(membership))
    created = (
        await client.post(
            "/api/v1/hospitalizations",
            json={
                "patient_id": str(patient.id),
                "kennel_id": str(kennel.id),
                "vet_membership_id": str(membership.id),
                "consent_status": "consent_recorded",
            },
            headers=token,
        )
    ).json()["hospitalization"]

    detail = (
        await client.get(f"/api/v1/hospitalizations/{created['id']}", headers=token)
    ).json()
    assert detail["patient"]["name"] == "Thor"
    assert detail["patient"]["species"] == "dog"
    assert detail["kennel_name"] == "UTI 03"
    assert detail["vet_license"] == "12345"
    assert detail["vet_name"]


async def test_alta_so_pede_confirmacao_por_dose_ja_vencida(client, session):
    """Confirmação que aparece SEMPRE é confirmação que ninguém lê.

    Dar alta cancela as doses futuras: é o significado da alta. O que a pessoa
    precisa confirmar é abandonar o que já era para ter sido feito."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    agora = datetime.now(UTC)
    # Só doses FUTURAS: a alta as cancela sem perguntar nada.
    for horas in (2, 8, 14):
        await make_task(
            session,
            clinic=clinic,
            hospitalization=hosp,
            scheduled_for=agora + timedelta(hours=horas),
        )
    await session.flush()
    headers = bearer(personal_token(membership))

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        json={"outcome": "discharged"},
        headers=headers,
    )
    assert resp.status_code == 200, "dose futura não devia exigir confirmação"


async def test_alta_pede_confirmacao_quando_ha_dose_vencida(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    agora = datetime.now(UTC)
    await make_task(
        session, clinic=clinic, hospitalization=hosp, scheduled_for=agora + timedelta(hours=4)
    )
    await make_task(
        session, clinic=clinic, hospitalization=hosp, scheduled_for=agora - timedelta(hours=3)
    )
    await session.flush()
    headers = bearer(personal_token(membership))

    bloqueado = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        json={"outcome": "discharged"},
        headers=headers,
    )
    assert bloqueado.status_code == 409
    erro = bloqueado.json()["error"]
    assert erro["code"] == "pending_tasks_confirmation_required"
    # Conta só a vencida: dizer "4 pendentes" incluindo as de amanhã confunde.
    assert erro["params"]["pending"] == 1

    liberado = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        json={"outcome": "discharged", "confirm_pending_tasks": True},
        headers=headers,
    )
    assert liberado.status_code == 200


async def test_internacoes_encerradas_continuam_alcancaveis(client, session):
    """Dar alta fazia o paciente sumir do sistema.

    O painel só lista internação ativa e a busca só devolvia a ativa: quem
    acabava de dar alta não tinha caminho de volta para a conta nem para o
    prontuário, que é exatamente o que se faz depois da alta (cópia ao tutor em
    5 dias úteis). A única ação oferecida era internar de novo.
    """
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    hosp = await make_hospitalization(
        session, clinic=clinic, patient=patient, membership=membership
    )
    headers = bearer(personal_token(membership))
    alta = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        json={"outcome": "discharged", "confirm_pending_tasks": True},
        headers=headers,
    )
    assert alta.status_code == 200

    # Some do painel. Correto: o painel é de quem está internado AGORA.
    painel = (await client.get("/api/v1/board", headers=headers)).json()
    assert all(row["hospitalization_id"] != str(hosp.id) for row in painel["rows"])

    # E continua alcançável pelo paciente, com a conta e o prontuário intactos.
    historico = (
        await client.get(f"/api/v1/hospitalizations?patient_id={patient.id}", headers=headers)
    ).json()
    assert [item["id"] for item in historico] == [str(hosp.id)]
    assert historico[0]["status"] == "discharged"

    assert (
        await client.get(f"/api/v1/hospitalizations/{hosp.id}/charges", headers=headers)
    ).status_code == 200
    assert (
        await client.get(f"/api/v1/hospitalizations/{hosp.id}/record", headers=headers)
    ).status_code == 200
