import time

from app.core.security import decode_jwt, hash_password
from tests.factories import make_clinic, make_membership, make_user


async def _personal_setup(session):
    clinic = await make_clinic(session)
    user = await make_user(
        session, email="vet@plantao.vet", password_hash=hash_password("s3nh4-forte")
    )
    membership = await make_membership(
        session,
        clinic=clinic,
        user=user,
        role="vet",
        license_number="12345",
        license_authority="CRMV-SP",
    )
    return clinic, user, membership


async def test_login_ok_devolve_jwt_pessoal_de_12h(client, session):
    clinic, user, membership = await _personal_setup(session)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "vet@plantao.vet", "password": "s3nh4-forte"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    claims = decode_jwt(data["access_token"])
    assert claims["kind"] == "personal"
    assert claims["sub"] == str(user.id)
    assert claims["clinic_id"] == str(clinic.id)
    assert claims["membership_id"] == str(membership.id)
    assert 11 * 3600 < claims["exp"] - time.time() <= 12 * 3600 + 60


async def test_login_senha_errada_devolve_codigo_nao_prosa(client, session):
    await _personal_setup(session)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "vet@plantao.vet", "password": "senha-errada"},
    )

    assert resp.status_code == 401
    assert resp.json() == {"error": {"code": "invalid_credentials", "params": {}}}


async def test_login_email_desconhecido_mesmo_codigo(client, session):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "ninguem@plantao.vet", "password": "qualquer"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_login_de_membership_inativo_e_recusado(client, session):
    clinic = await make_clinic(session)
    user = await make_user(
        session, email="ex@plantao.vet", password_hash=hash_password("s3nh4-forte")
    )
    await make_membership(session, clinic=clinic, user=user, role="tech", is_active=False)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "ex@plantao.vet", "password": "s3nh4-forte"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"
