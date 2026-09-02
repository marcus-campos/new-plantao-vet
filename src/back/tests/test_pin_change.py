"""Trocar o próprio PIN, e o tamanho mínimo de seis dígitos.

Existia só o caminho do administrador definir o PIN de alguém. Para trocar um
PIN que a pessoa desconfia que alguém viu por cima do ombro, era preciso pedir
a outra pessoa: o incentivo era não trocar.
"""

from app.core.security import hash_password, verify_password
from tests.factories import make_clinic, make_membership, make_user
from tests.helpers import bearer, personal_token


async def _pessoa(session, *, pin: str | None = "111111", role: str = "tech"):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(
        session,
        clinic=clinic,
        user=user,
        role=role,
        pin_hash=hash_password(pin) if pin else None,
    )
    await session.commit()
    return clinic, membership


async def test_troca_o_proprio_pin_com_o_atual(client, session):
    _, membership = await _pessoa(session)

    resp = await client.put(
        "/api/v1/auth/me/pin",
        json={"current_pin": "111111", "new_pin": "222222"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 204, resp.text

    await session.refresh(membership)
    assert verify_password("222222", membership.pin_hash)
    assert not verify_password("111111", membership.pin_hash)


async def test_pin_atual_errado_nao_troca(client, session):
    _, membership = await _pessoa(session)

    resp = await client.put(
        "/api/v1/auth/me/pin",
        json={"current_pin": "999999", "new_pin": "222222"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 401

    await session.refresh(membership)
    assert verify_password("111111", membership.pin_hash)


async def test_sem_o_pin_atual_nao_troca_quando_ja_existe_um(client, session):
    """Uma sessão esquecida aberta não pode ser uma troca de PIN a um clique."""
    _, membership = await _pessoa(session)

    resp = await client.put(
        "/api/v1/auth/me/pin",
        json={"new_pin": "222222"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 401


async def test_primeiro_pin_dispensa_o_atual(client, session):
    _, membership = await _pessoa(session, pin=None)

    resp = await client.put(
        "/api/v1/auth/me/pin",
        json={"new_pin": "654321"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 204, resp.text
    await session.refresh(membership)
    assert verify_password("654321", membership.pin_hash)


async def test_novo_pin_igual_ao_atual_e_recusado(client, session):
    """Sair daqui com "salvo" faria a pessoa achar que o PIN antigo caiu."""
    _, membership = await _pessoa(session)

    resp = await client.put(
        "/api/v1/auth/me/pin",
        json={"current_pin": "111111", "new_pin": "111111"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "pin_same_as_current"


async def test_pin_novo_precisa_de_seis_digitos(client, session):
    _, membership = await _pessoa(session)
    headers = bearer(personal_token(membership))

    for curto in ("1234", "12345", "1234567"):
        resp = await client.put(
            "/api/v1/auth/me/pin",
            json={"current_pin": "111111", "new_pin": curto},
            headers=headers,
        )
        assert resp.status_code == 422, curto


async def test_administrador_define_pin_de_seis_digitos(client, session):
    clinic = await make_clinic(session)
    admin_user = await make_user(session)
    admin = await make_membership(session, clinic=clinic, user=admin_user, role="admin")
    outro_user = await make_user(session)
    outro = await make_membership(session, clinic=clinic, user=outro_user, role="tech")
    await session.commit()
    headers = bearer(personal_token(admin))

    curto = await client.post(
        f"/api/v1/memberships/{outro.id}/pin", json={"pin": "1234"}, headers=headers
    )
    assert curto.status_code == 422

    ok = await client.post(
        f"/api/v1/memberships/{outro.id}/pin", json={"pin": "424242"}, headers=headers
    )
    assert ok.status_code == 204, ok.text
    await session.refresh(outro)
    assert verify_password("424242", outro.pin_hash)


async def test_pin_de_quatro_digitos_antigo_continua_entrando(client, session):
    """Ninguém fica de fora da clínica por causa da mudança de tamanho.

    Seis dígitos passam a ser exigidos na DEFINIÇÃO; quem já tem um PIN de
    quatro continua digitando o dele até trocar."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    await make_membership(
        session, clinic=clinic, user=user, role="tech", pin_hash=hash_password("1234")
    )
    await session.commit()

    from tests.helpers import station_token

    resp = await client.post(
        "/api/v1/auth/pin",
        json={"pin": "1234"},
        headers=bearer(station_token(clinic)),
    )
    assert resp.status_code == 200, resp.text
