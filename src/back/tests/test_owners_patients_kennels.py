import uuid
from decimal import Decimal

import sqlalchemy as sa

from app.models import AuditEntry
from tests.factories import (
    make_clinic,
    make_kennel,
    make_membership,
    make_owner,
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


async def test_criar_e_listar_kennel(client, session):
    clinic, membership = await _vet(session)
    resp = await client.post(
        "/api/v1/kennels",
        json={"name": "UTI 03", "area": "UTI"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "UTI 03"
    assert resp.json()["is_active"] is True

    listing = await client.get("/api/v1/kennels", headers=bearer(personal_token(membership)))
    assert listing.status_code == 200
    assert [item["name"] for item in listing.json()["items"]] == ["UTI 03"]
    assert listing.json()["next_cursor"] is None


async def test_criar_owner_e_patient_vinculado(client, session):
    clinic, membership = await _vet(session)
    token = bearer(personal_token(membership))
    owner_resp = await client.post(
        "/api/v1/owners",
        json={"name": "Marina Campos", "phone_e164": "+5511999990000", "tax_id": "12345678900"},
        headers=token,
    )
    assert owner_resp.status_code == 201
    owner_id = owner_resp.json()["id"]

    patient_resp = await client.post(
        "/api/v1/patients",
        json={"name": "Thor", "species": "dog", "owner_id": owner_id, "weight_kg": "24.3"},
        headers=token,
    )
    assert patient_resp.status_code == 201
    assert patient_resp.json()["owner_id"] == owner_id
    assert patient_resp.json()["weight_kg"] == "24.3"


async def test_patch_de_peso_e_auditado_com_before_e_after(client, session):
    clinic, membership = await _vet(session)
    owner = await make_owner(session, clinic=clinic)
    patient = await make_patient(session, clinic=clinic, owner=owner, weight_kg=Decimal("24.3"))

    resp = await client.patch(
        f"/api/v1/patients/{patient.id}",
        json={"weight_kg": "25.1"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert resp.json()["weight_kg"] == "25.1"

    entry = (
        (
            await session.execute(
                sa.select(AuditEntry)
                .where(AuditEntry.entity_type == "patient", AuditEntry.action == "patient_updated")
                .order_by(AuditEntry.id.desc())
            )
        )
        .scalars()
        .first()
    )
    assert entry is not None
    assert entry.payload["before"]["weight_kg"] == "24.3"
    assert entry.payload["after"]["weight_kg"] == "25.1"
    assert entry.actor_license == "12345"


async def test_snapshot_do_owner_nao_carrega_contato(client, session):
    clinic, membership = await _vet(session)
    resp = await client.post(
        "/api/v1/owners",
        json={"name": "Marina", "phone_e164": "+5511999990000", "tax_id": "12345678900"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201

    entry = (
        (
            await session.execute(
                sa.select(AuditEntry)
                .where(AuditEntry.entity_type == "owner")
                .order_by(AuditEntry.id.desc())
            )
        )
        .scalars()
        .first()
    )
    assert entry is not None
    assert "phone_e164" not in entry.payload["after"]
    assert "tax_id" not in entry.payload["after"]
    assert entry.payload["after"]["name"] == "Marina"


async def test_paginacao_por_cursor_percorre_tudo_sem_repetir(client, session):
    clinic, membership = await _vet(session)
    for index in range(5):
        await make_owner(session, clinic=clinic, name=f"Tutor {index}")
    token = bearer(personal_token(membership))

    seen: list[str] = []
    cursor = None
    for _ in range(5):
        url = "/api/v1/owners?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(url, headers=token)).json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_limite_maximo_de_paginacao(client, session):
    clinic, membership = await _vet(session)
    resp = await client.get("/api/v1/owners?limit=500", headers=bearer(personal_token(membership)))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_desativacao_em_vez_de_delete(client, session):
    clinic, membership = await _vet(session)
    kennel = await make_kennel(session, clinic=clinic)
    token = bearer(personal_token(membership))

    assert (await client.delete(f"/api/v1/kennels/{kennel.id}", headers=token)).status_code == 405

    resp = await client.patch(
        f"/api/v1/kennels/{kennel.id}", json={"is_active": False}, headers=token
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    listing = (await client.get("/api/v1/kennels", headers=token)).json()
    assert listing["items"] == []
    todos = (await client.get("/api/v1/kennels?include_inactive=true", headers=token)).json()
    assert len(todos["items"]) == 1


async def test_isolamento_de_tenant_na_leitura(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    owner_b = await make_owner(session, clinic=clinic_b)

    resp = await client.get(
        f"/api/v1/owners/{owner_b.id}", headers=bearer(personal_token(membership_a))
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_isolamento_de_tenant_em_fk_de_body(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    owner_b = await make_owner(session, clinic=clinic_b)

    resp = await client.post(
        "/api/v1/patients",
        json={"name": "Invasor", "species": "dog", "owner_id": str(owner_b.id)},
        headers=bearer(personal_token(membership_a)),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_patient_de_owner_inexistente_e_404(client, session):
    clinic, membership = await _vet(session)
    resp = await client.post(
        "/api/v1/patients",
        json={"name": "Fantasma", "species": "cat", "owner_id": str(uuid.uuid4())},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 404
