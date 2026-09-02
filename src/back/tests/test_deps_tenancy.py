from datetime import timedelta

import pytest

from app.api.deps import AuthContext, get_operator, get_tenant_obj
from app.core.errors import AppError
from app.models.membership import Membership
from tests.factories import make_clinic, make_membership, make_user
from tests.helpers import bearer, personal_token


async def test_me_com_token_pessoal(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")

    resp = await client.get("/api/v1/auth/me", headers=bearer(personal_token(membership)))

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["kind"] == "personal"
    assert corpo["clinic_id"] == str(clinic.id)
    assert corpo["membership_id"] == str(membership.id)
    # A interface esconde o que a API recusaria: /me diz o que este papel pode.
    assert corpo["role"] == "vet"
    assert "prescription.create" in corpo["capabilities"]


async def test_token_expirado_devolve_token_expired(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    token = personal_token(membership, expires_in=timedelta(seconds=-1))

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "token_expired"


async def test_sem_header_authorization_e_invalid_credentials(client):
    resp = await client.get("/api/v1/auth/me")

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_membership_desativado_nao_autentica_mais(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    token = personal_token(membership)

    membership.is_active = False
    await session.commit()

    resp = await client.get("/api/v1/auth/me", headers=bearer(token))

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_get_operator_pessoal_monta_actor_info_do_membership(session):
    clinic = await make_clinic(session)
    user = await make_user(session, name="Dra. Ana Souza")
    membership = await make_membership(
        session,
        clinic=clinic,
        user=user,
        role="vet",
        license_number="4321",
        license_authority="CRMV-SP",
    )
    auth = AuthContext(kind="personal", clinic_id=clinic.id, membership=membership)

    actor = await get_operator(auth=auth, session=session, x_operator_token=None)

    assert actor.membership_id == membership.id
    assert actor.name == "Dra. Ana Souza"
    assert actor.license_number == "4321"
    assert actor.license_authority == "CRMV-SP"


async def test_get_tenant_obj_cross_tenant_e_404(session):
    clinic_a = await make_clinic(session, slug="clinica-a")
    clinic_b = await make_clinic(session, slug="clinica-b")
    user_b = await make_user(session, email="b@plantao.vet")
    membership_b = await make_membership(session, clinic=clinic_b, user=user_b, role="vet")

    # no tenant certo, devolve o objeto
    obj = await get_tenant_obj(session, Membership, membership_b.id, clinic_b.id)
    assert obj.id == membership_b.id

    # no tenant errado, 404 not_found – nunca 403, para não vazar existência
    with pytest.raises(AppError) as exc:
        await get_tenant_obj(session, Membership, membership_b.id, clinic_a.id)
    assert exc.value.code == "not_found"
    assert exc.value.status_code == 404
