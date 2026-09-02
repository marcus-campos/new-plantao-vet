import hashlib
import json

import sqlalchemy as sa

from app.core.security import hash_password
from app.models import AuditEntry
from tests.factories import (
    make_clinic,
    make_kennel,
    make_membership,
    make_owner,
    make_patient,
    make_user,
)
from tests.helpers import bearer


async def test_fluxo_completo_da_internacao(client, session):
    clinic = await make_clinic(session, slug="demo", station_key_hash=hash_password("estacao-123"))
    vet_user = await make_user(
        session, email="paula@demo.vet", password_hash=hash_password("senha-123")
    )
    vet = await make_membership(
        session,
        clinic=clinic,
        user=vet_user,
        role="vet",
        license_number="12345",
        license_authority="CRMV-SP",
        pin_hash=hash_password("4321"),
    )
    kennel = await make_kennel(session, clinic=clinic, name="UTI 03")
    owner = await make_owner(session, clinic=clinic, name="Marina Campos")
    patient = await make_patient(session, clinic=clinic, owner=owner, name="Thor")
    await session.flush()

    # 1. Login pessoal
    login = await client.post(
        "/api/v1/auth/login", json={"email": "paula@demo.vet", "password": "senha-123"}
    )
    assert login.status_code == 200
    token = bearer(login.json()["access_token"])

    # 2. Admitir — as cerimônias do dia nascem junto
    admissao = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(patient.id),
            "kennel_id": str(kennel.id),
            "vet_membership_id": str(vet.id),
            "consent_status": "consent_recorded",
        },
        headers=token,
    )
    assert admissao.status_code == 201
    hospitalization_id = admissao.json()["hospitalization"]["id"]

    detalhe = (
        await client.get(f"/api/v1/hospitalizations/{hospitalization_id}", headers=token)
    ).json()
    assert sorted(p["name"] for p in detalhe["prescriptions"]) == [
        "Contato com o tutor",
        "Evolução diária",
    ]

    # 3. Prescrever
    prescricao = await client.post(
        f"/api/v1/hospitalizations/{hospitalization_id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Dipirona 25 mg/kg IV",
            "frequency_minutes": 480,
            "duration_hours": 72,
            "criticality": "normal",
            "first_dose_now": True,
            "price_minor": 1800,
            "details": {"drug": "dipirona", "dose": "25 mg/kg", "route": "IV"},
        },
        headers=token,
    )
    assert prescricao.status_code == 201

    # 4. A fila da janela já mostra a primeira dose
    fila = (await client.get("/api/v1/tasks", headers=token)).json()["items"]
    primeira = next(item for item in fila if item["title"].startswith("Dipirona"))
    assert primeira["display_state"] in ("on_time", "due")

    # 5. Executar em modo estação, identificando o operador por PIN
    estacao = await client.post(
        "/api/v1/auth/station", json={"clinic_slug": "demo", "station_key": "estacao-123"}
    )
    assert estacao.status_code == 200
    station_headers = bearer(estacao.json()["access_token"])

    pin = await client.post("/api/v1/auth/pin", json={"pin": "4321"}, headers=station_headers)
    assert pin.status_code == 200
    operador = {**station_headers, "X-Operator-Token": pin.json()["operator_token"]}

    execucao = await client.post(
        f"/api/v1/tasks/{primeira['id']}/execute",
        json={"values": {"note": "sem intercorrência"}},
        headers=operador,
    )
    assert execucao.status_code == 200
    assert execucao.json()["status"] == "done"

    # 6. O board reflete na hora, com a mesma fonte da fila
    board = (await client.get("/api/v1/board", headers=token)).json()
    linha = next(row for row in board["rows"] if row["patient_name"] == "Thor")
    assert board["totals"]["patients"] == 1
    assert linha["critical_overdue"] is False

    # 7. A trilha tem nome, registro profissional e a cadeia de hash íntegra
    auditoria = (await client.get("/api/v1/audit", headers=token)).json()["items"]
    execucoes = [item for item in auditoria if item["action"] == "task_executed"]
    assert execucoes[0]["actor_license"] == "12345"
    assert execucoes[0]["actor_license_authority"] == "CRMV-SP"

    entradas = list(
        (
            await session.execute(
                sa.select(AuditEntry)
                .where(AuditEntry.clinic_id == clinic.id)
                .order_by(AuditEntry.id)
            )
        ).scalars()
    )
    anterior = ""
    for entrada in entradas:
        assert entrada.prev_hash == anterior
        canonical = json.dumps(
            entrada.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        esperado = hashlib.sha256(
            f"{anterior}|{entrada.clinic_id}|{entrada.action}|{entrada.entity_type}"
            f"|{entrada.entity_id}|{canonical}|{entrada.created_at.isoformat()}".encode()
        ).hexdigest()
        assert entrada.entry_hash == esperado
        anterior = entrada.entry_hash
