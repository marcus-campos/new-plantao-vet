import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api.deps import AuthContext, get_operator
from app.core.errors import AppError
from app.core.security import create_jwt, decode_jwt, hash_password
from app.models.audit import AuditEntry
from tests.factories import make_clinic, make_membership, make_user
from tests.helpers import bearer, personal_token, station_token

STATION_KEY = "chave-da-estacao"


async def _station_setup(session, *, pin="1234"):
    clinic = await make_clinic(
        session, station_key_hash=hash_password(STATION_KEY), station_key_version=1
    )
    user = await make_user(session, email="tech@plantao.vet", name="Tec. Joao")
    membership = await make_membership(
        session, clinic=clinic, user=user, role="tech", pin_hash=hash_password(pin)
    )
    return clinic, membership


async def _station_login(client, clinic) -> str:
    resp = await client.post(
        "/api/v1/auth/station",
        json={"clinic_slug": clinic.slug, "station_key": STATION_KEY},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


async def test_login_de_estacao_emite_token_com_versao_e_station_id(client, session):
    clinic, _ = await _station_setup(session)

    token = await _station_login(client, clinic)

    claims = decode_jwt(token)
    assert claims["kind"] == "station"
    assert claims["clinic_id"] == str(clinic.id)
    assert claims["station_key_version"] == 1
    assert claims["station_id"]
    assert 11 * 3600 < claims["exp"] - time.time() <= 12 * 3600 + 60

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["kind"] == "station"
    assert corpo["clinic_id"] == str(clinic.id)
    assert corpo["membership_id"] is None
    # A estação não tem papel: quem responde pelo ato é o dono do PIN, e a
    # capacidade é conferida no momento da baixa.
    assert corpo["role"] is None
    assert corpo["capabilities"] == []


async def test_station_key_errada_e_invalid_credentials(client, session):
    clinic, _ = await _station_setup(session)

    resp = await client.post(
        "/api/v1/auth/station",
        json={"clinic_slug": clinic.slug, "station_key": "chave-errada"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_rotacao_da_station_key_revoga_tokens_emitidos(client, session):
    clinic, _ = await _station_setup(session)
    token = station_token(clinic)  # carrega station_key_version=1

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert resp.status_code == 200

    clinic.station_key_version = 2
    await session.commit()

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "station_key_rotated"


async def test_fluxo_completo_estacao_pin_operator_token(client, session):
    clinic, membership = await _station_setup(session, pin="1234")
    station_jwt = await _station_login(client, clinic)

    resp = await client.post("/api/v1/auth/pin", json={"pin": "1234"}, headers=bearer(station_jwt))

    assert resp.status_code == 200
    operator_jwt = resp.json()["operator_token"]
    claims = decode_jwt(operator_jwt)
    assert claims["kind"] == "operator"
    assert claims["clinic_id"] == str(clinic.id)
    assert claims["membership_id"] == str(membership.id)
    assert claims["exp"] - time.time() <= 5 * 60 + 5  # operator token vive 5 min

    # o operator token vira ActorInfo no get_operator
    auth = AuthContext(kind="station", clinic_id=clinic.id, membership=None)
    actor = await get_operator(auth=auth, session=session, x_operator_token=operator_jwt)
    assert actor.membership_id == membership.id
    assert actor.name == "Tec. Joao"


async def test_pin_errado_e_401_e_audita_pin_failed(client, session):
    clinic, _ = await _station_setup(session, pin="1234")
    station_jwt = await _station_login(client, clinic)

    resp = await client.post("/api/v1/auth/pin", json={"pin": "9999"}, headers=bearer(station_jwt))

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"

    entry = (
        (
            await session.execute(
                select(AuditEntry)
                .where(AuditEntry.clinic_id == clinic.id, AuditEntry.action == "pin_failed")
                .order_by(AuditEntry.id.desc())
            )
        )
        .scalars()
        .first()
    )
    assert entry is not None
    assert entry.payload["extra"]["station_id"] == decode_jwt(station_jwt)["station_id"]


async def test_lockout_apos_5_falhas_no_endpoint(client, session):
    clinic, _ = await _station_setup(session, pin="1234")
    station_jwt = await _station_login(client, clinic)

    for _ in range(5):
        resp = await client.post(
            "/api/v1/auth/pin", json={"pin": "0000"}, headers=bearer(station_jwt)
        )
        assert resp.status_code == 401

    # até o PIN CERTO é recusado durante o lockout
    resp = await client.post("/api/v1/auth/pin", json={"pin": "1234"}, headers=bearer(station_jwt))
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "pin_locked_out"
    assert body["error"]["params"]["retry_after_seconds"] > 0
    # a liberação após 15 min é coberta em tests/test_pin_throttle.py com clock injetado


async def test_pin_com_token_pessoal_e_forbidden(client, session):
    clinic, _ = await _station_setup(session)
    user = await make_user(session, email="vet2@plantao.vet")
    vet = await make_membership(session, clinic=clinic, user=user, role="vet")

    resp = await client.post(
        "/api/v1/auth/pin", json={"pin": "1234"}, headers=bearer(personal_token(vet))
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_estacao_sem_operator_token_e_operator_required(session):
    clinic, _ = await _station_setup(session)
    auth = AuthContext(kind="station", clinic_id=clinic.id, membership=None)

    with pytest.raises(AppError) as exc:
        await get_operator(auth=auth, session=session, x_operator_token=None)

    assert exc.value.code == "operator_required"
    assert exc.value.status_code == 403


async def test_operator_token_expirado_e_operator_required(session):
    clinic, membership = await _station_setup(session)
    expired = create_jwt(
        {
            "kind": "operator",
            "clinic_id": str(clinic.id),
            "membership_id": str(membership.id),
        },
        expires_in=timedelta(seconds=-1),
    )
    auth = AuthContext(kind="station", clinic_id=clinic.id, membership=None)

    with pytest.raises(AppError) as exc:
        await get_operator(auth=auth, session=session, x_operator_token=expired)

    assert exc.value.code == "operator_required"


async def test_operator_token_de_outra_clinica_e_operator_required(client, session):
    clinic_a, _ = await _station_setup(session)
    clinic_b = await make_clinic(
        session, slug="clinica-b", station_key_hash=hash_password(STATION_KEY)
    )
    user_b = await make_user(session, email="tech-b@plantao.vet")
    membership_b = await make_membership(
        session, clinic=clinic_b, user=user_b, role="tech", pin_hash=hash_password("4321")
    )
    operator_b = create_jwt(
        {
            "kind": "operator",
            "clinic_id": str(clinic_b.id),
            "membership_id": str(membership_b.id),
        },
        expires_in=timedelta(minutes=5),
    )
    auth_a = AuthContext(kind="station", clinic_id=clinic_a.id, membership=None)

    with pytest.raises(AppError) as exc:
        await get_operator(auth=auth_a, session=session, x_operator_token=operator_b)

    assert exc.value.code == "operator_required"


async def _admin_headers(session, clinic):
    admin_user = await make_user(session, email="admin@plantao.vet")
    admin = await make_membership(session, clinic=clinic, user=admin_user, role="admin")
    return bearer(personal_token(admin))


async def test_pin_duplicado_na_clinica_e_recusado(client, session):
    """Dois PINs iguais atribuiriam o ato clínico à pessoa errada.

    Com seis dígitos a colisão passa a ser rara em vez de inevitável, mas
    continua possível, e continua recusada."""
    clinic, _tech_com_pin = await _station_setup(session, pin="123456")
    headers = await _admin_headers(session, clinic)
    other_user = await make_user(session, email="vet3@plantao.vet")
    other = await make_membership(session, clinic=clinic, user=other_user, role="vet")

    resp = await client.post(
        f"/api/v1/memberships/{other.id}/pin", json={"pin": "123456"}, headers=headers
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "pin_duplicate"

    resp = await client.post(
        f"/api/v1/memberships/{other.id}/pin", json={"pin": "567890"}, headers=headers
    )
    assert resp.status_code == 204


async def test_definir_pin_exige_admin(client, session):
    clinic, tech = await _station_setup(session, pin="1234")
    vet_user = await make_user(session, email="vet4@plantao.vet")
    vet = await make_membership(session, clinic=clinic, user=vet_user, role="vet")

    resp = await client.post(
        f"/api/v1/memberships/{tech.id}/pin",
        json={"pin": "222222"},
        headers=bearer(personal_token(vet)),
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_definir_pin_de_membership_de_outra_clinica_e_404(client, session):
    clinic_a, _ = await _station_setup(session)
    headers_a = await _admin_headers(session, clinic_a)
    clinic_b = await make_clinic(session, slug="clinica-b-pin")
    user_b = await make_user(session, email="alvo-b@plantao.vet")
    membership_b = await make_membership(session, clinic=clinic_b, user=user_b, role="tech")

    resp = await client.post(
        f"/api/v1/memberships/{membership_b.id}/pin",
        json={"pin": "333333"},
        headers=headers_a,
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
