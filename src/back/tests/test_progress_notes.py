from datetime import UTC, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa

from app.api.deps import get_session
from app.api.routes import progress_notes as progress_note_routes
from app.api.routes import records as record_routes
from app.main import create_app
from app.models import Task
from app.models.progress_note import ProgressNote
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_patient,
    make_prescription,
    make_user,
)
from tests.helpers import bearer, personal_token

# Os routers desta trilha só entram no main.py pela mão do integrador; aqui a
# app de teste os registra por conta própria (sem duplicar, se já estiverem).
LOCAL_ROUTERS = (
    progress_note_routes.router,
    progress_note_routes.compliance_router,
    record_routes.router,
)


def _route_keys(routes) -> set[tuple[str, tuple[str, ...]]]:
    return {
        (route.path, tuple(sorted(route.methods)))
        for route in routes
        if getattr(route, "path", None) and getattr(route, "methods", None)
    }


@pytest.fixture
async def client(db_session):
    app = create_app()
    registered = _route_keys(app.routes)
    for router in LOCAL_ROUTERS:
        keys = {(router.prefix + path, methods) for path, methods in _route_keys(router.routes)}
        if not keys <= registered:
            app.include_router(router)
            registered |= keys

    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def _vet(session, clinic=None, **overrides):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session, name=overrides.pop("name", "Dra. Ana Prado"))
    membership = await make_membership(
        session,
        clinic=clinic,
        user=user,
        role="vet",
        license_number=overrides.pop("license_number", "SP-12345"),
        license_authority=overrides.pop("license_authority", "CRMV-SP"),
    )
    return clinic, membership


async def _executed_task(session, *, clinic, hospitalization, membership, **overrides):
    now = datetime.now(UTC)
    task = Task(
        clinic_id=clinic.id,
        hospitalization_id=hospitalization.id,
        **{
            "title": "Dipirona 25 mg/kg IV",
            "category": "medication",
            "scheduled_for": now,
            "criticality": "normal",
            "tolerance_minutes": 60,
            "status": "done",
            "executed_at": now,
            "executed_by": membership.id,
            **overrides,
        },
    )
    session.add(task)
    await session.flush()
    return task


async def test_evolucao_assinada_grava_nome_e_registro(client, session):
    clinic, membership = await _vet(session)
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=membership)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/progress-notes",
        json={
            "subjective": "Tutor relata que comeu pela manhã.",
            "findings": "TPC 2s, mucosas róseas, FC 120.",
            "assessment": "Estável, hidratação adequada.",
            "plan": "Manter fluidoterapia; reavaliar em 12h.",
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["author_name"] == "Dra. Ana Prado"
    assert body["author_license"] == "SP-12345"
    assert body["author_license_authority"] == "CRMV-SP"
    assert body["membership_id"] == str(membership.id)
    assert body["signed_at"] is not None

    listed = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/progress-notes",
        headers=bearer(personal_token(membership)),
    )
    assert [item["id"] for item in listed.json()] == [body["id"]]

    # A assinatura é ato clínico: entra na trilha de auditoria.
    action = await session.scalar(
        sa.select(sa.text("action"))
        .select_from(sa.table("audit_entries"))
        .where(sa.text("entity_type = 'progress_note'"))
    )
    assert action == "progress_note_signed"


async def test_evolucao_sem_nenhum_texto_e_recusada(client, session):
    clinic, membership = await _vet(session)
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=membership)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/progress-notes",
        json={"subjective": "   ", "plan": None},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert await session.scalar(sa.select(sa.func.count()).select_from(ProgressNote)) == 0


async def test_alerta_lista_internacao_sem_evolucao_ha_24h(client, session):
    clinic, membership = await _vet(session)
    token = bearer(personal_token(membership))

    esquecido = await make_patient(session, clinic=clinic, name="Thor")
    em_dia = await make_patient(session, clinic=clinic, name="Nina")
    recem = await make_patient(session, clinic=clinic, name="Bidu")

    velha = await make_hospitalization(
        session,
        clinic=clinic,
        patient=esquecido,
        membership=membership,
        admitted_at=datetime.now(UTC) - timedelta(days=3),
    )
    evoluida = await make_hospitalization(
        session,
        clinic=clinic,
        patient=em_dia,
        membership=membership,
        admitted_at=datetime.now(UTC) - timedelta(days=3),
    )
    # Admitida há 2h e ainda sem evolução: não está em falta.
    await make_hospitalization(
        session,
        clinic=clinic,
        patient=recem,
        membership=membership,
        admitted_at=datetime.now(UTC) - timedelta(hours=2),
    )

    resp = await client.post(
        f"/api/v1/hospitalizations/{evoluida.id}/progress-notes",
        json={"findings": "Sem alterações."},
        headers=token,
    )
    assert resp.status_code == 201

    alerts = (await client.get("/api/v1/compliance/alerts", headers=token)).json()
    missing = alerts["missing_progress_note"]
    assert [item["hospitalization_id"] for item in missing] == [str(velha.id)]
    assert missing[0]["patient_name"] == "Thor"
    # Nunca teve evolução: horas desde a última é null (entra pelo tempo de admissão).
    assert missing[0]["hours_since"] is None


async def test_alerta_reaparece_quando_a_ultima_evolucao_envelhece(client, session):
    clinic, membership = await _vet(session)
    token = bearer(personal_token(membership))
    hospitalization = await make_hospitalization(
        session,
        clinic=clinic,
        membership=membership,
        admitted_at=datetime.now(UTC) - timedelta(days=5),
    )
    old = ProgressNote(
        clinic_id=clinic.id,
        hospitalization_id=hospitalization.id,
        membership_id=membership.id,
        author_name="Dra. Ana Prado",
        findings="Estável.",
        signed_at=datetime.now(UTC) - timedelta(hours=30),
    )
    session.add(old)
    await session.flush()

    missing = (await client.get("/api/v1/compliance/alerts", headers=token)).json()[
        "missing_progress_note"
    ]
    assert [item["hospitalization_id"] for item in missing] == [str(hospitalization.id)]
    assert 29 < missing[0]["hours_since"] < 31


async def test_adendo_aponta_para_a_anterior_e_as_duas_permanecem(client, session):
    clinic, membership = await _vet(session)
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))

    original = (
        await client.post(
            f"/api/v1/hospitalizations/{hospitalization.id}/progress-notes",
            json={"findings": "FC 120."},
            headers=token,
        )
    ).json()

    adendo = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/progress-notes",
        json={
            "findings": "Correção: FC 160, não 120.",
            "amends_progress_note_id": original["id"],
        },
        headers=token,
    )
    assert adendo.status_code == 201
    assert adendo.json()["amends_progress_note_id"] == original["id"]

    # Sem rasura: a original continua no prontuário, íntegra.
    listed = (
        await client.get(
            f"/api/v1/hospitalizations/{hospitalization.id}/progress-notes", headers=token
        )
    ).json()
    assert [item["id"] for item in listed] == [adendo.json()["id"], original["id"]]
    assert listed[-1]["findings"] == "FC 120."


async def test_adendo_de_outra_internacao_e_recusado(client, session):
    clinic, membership = await _vet(session)
    token = bearer(personal_token(membership))
    uma = await make_hospitalization(session, clinic=clinic, membership=membership)
    outra = await make_hospitalization(session, clinic=clinic, membership=membership)
    nota = (
        await client.post(
            f"/api/v1/hospitalizations/{uma.id}/progress-notes",
            json={"findings": "FC 120."},
            headers=token,
        )
    ).json()

    resp = await client.post(
        f"/api/v1/hospitalizations/{outra.id}/progress-notes",
        json={"findings": "Correção.", "amends_progress_note_id": nota["id"]},
        headers=token,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_prontuario_traz_evolucoes_e_execucoes_com_autor(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic, name="Thor")
    hospitalization = await make_hospitalization(
        session, clinic=clinic, patient=patient, membership=membership
    )
    await make_prescription(session, clinic=clinic, hospitalization=hospitalization)
    await _executed_task(
        session, clinic=clinic, hospitalization=hospitalization, membership=membership
    )
    await _executed_task(
        session,
        clinic=clinic,
        hospitalization=hospitalization,
        membership=membership,
        title="Sondagem",
        status="not_done",
        outcome_reason="refused",
    )
    token = bearer(personal_token(membership))
    await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/progress-notes",
        json={"assessment": "Evolui bem."},
        headers=token,
    )

    resp = await client.get(f"/api/v1/hospitalizations/{hospitalization.id}/record", headers=token)
    assert resp.status_code == 200
    body = resp.json()
    assert body["clinic_name"] == clinic.name
    assert body["patient"]["name"] == "Thor"
    assert body["owner_name"] == "Tutor Teste"
    assert body["vet"] == {
        "name": "Dra. Ana Prado",
        "license_number": "SP-12345",
        "license_authority": "CRMV-SP",
    }
    assert body["generated_at"] is not None
    assert [note["assessment"] for note in body["progress_notes"]] == ["Evolui bem."]
    assert body["progress_notes"][0]["author_license"] == "SP-12345"
    assert len(body["prescriptions"]) == 1
    assert {task["status"] for task in body["tasks"]} == {"done", "not_done"}
    for task in body["tasks"]:
        assert task["author"]["name"] == "Dra. Ana Prado"
        assert task["author"]["license_number"] == "SP-12345"
    # Conta da internação não entra na cópia clínica por default.
    assert body["charges"] is None


async def test_prontuario_respeita_o_include(client, session):
    clinic, membership = await _vet(session)
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))

    body = (
        await client.get(
            f"/api/v1/hospitalizations/{hospitalization.id}/record?include=progress_notes",
            headers=token,
        )
    ).json()
    assert body["progress_notes"] == []
    assert body["tasks"] is None
    assert body["prescriptions"] is None

    com_conta = (
        await client.get(
            f"/api/v1/hospitalizations/{hospitalization.id}/record?include=tasks,charges",
            headers=token,
        )
    ).json()
    assert com_conta["charges"] == []
    assert com_conta["tasks"] == []
    assert com_conta["progress_notes"] is None

    invalido = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/record?include=segredos",
        headers=token,
    )
    assert invalido.status_code == 422
    assert invalido.json()["error"]["code"] == "validation_error"


async def test_isolamento_de_tenant(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b, membership_b = await _vet(session, clinic=await make_clinic(session))
    hospitalization_b = await make_hospitalization(
        session, clinic=clinic_b, membership=membership_b
    )
    token_a = bearer(personal_token(membership_a))
    token_b = bearer(personal_token(membership_b))

    nota_b = (
        await client.post(
            f"/api/v1/hospitalizations/{hospitalization_b.id}/progress-notes",
            json={"findings": "Interno da clínica B."},
            headers=token_b,
        )
    ).json()

    for path in (
        f"/api/v1/hospitalizations/{hospitalization_b.id}/progress-notes",
        f"/api/v1/hospitalizations/{hospitalization_b.id}/record",
    ):
        resp = await client.get(path, headers=token_a)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    escrita = await client.post(
        f"/api/v1/hospitalizations/{hospitalization_b.id}/progress-notes",
        json={"findings": "Invasão."},
        headers=token_a,
    )
    assert escrita.status_code == 404

    # O alerta da clínica A jamais enxerga internação da B.
    alerts = (await client.get("/api/v1/compliance/alerts", headers=token_a)).json()
    assert alerts["missing_progress_note"] == []
    assert nota_b["id"]
