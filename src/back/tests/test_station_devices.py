"""Aparelhos compartilhados: liberação, uso, bloqueio e revogação.

A chave de estação era uma senha única da clínica. O que se testa aqui é
exatamente o que ela não conseguia fazer: revogar um aparelho sem derrubar os
outros, saber quais existem, e travar quem fica tentando PIN até um
administrador liberar.
"""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.core.security import hash_password
from app.models.station_device import StationDevice
from tests.factories import make_clinic, make_membership, make_user
from tests.helpers import bearer, personal_token, station_token


async def _admin(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="admin")
    return clinic, membership


async def test_liberar_aparelho_e_entrar_com_o_codigo(client, session):
    clinic, admin = await _admin(session)
    await session.commit()
    headers = bearer(personal_token(admin))

    aberto = await client.post(
        "/api/v1/station-devices", json={"name": "Tablet da UTI"}, headers=headers
    )
    assert aberto.status_code == 201, aberto.text
    code = aberto.json()["enrollment_code"]
    assert len(code) == 6 and code.isdigit()
    assert aberto.json()["device"]["status"] == "pending"

    entrou = await client.post(
        "/api/v1/auth/station/enroll",
        json={"clinic_slug": clinic.slug, "code": code, "device_name": "Tablet da UTI"},
    )
    assert entrou.status_code == 200, entrou.text
    corpo = entrou.json()
    assert corpo["device_name"] == "Tablet da UTI"

    # O segredo do aparelho vale como credencial de estação.
    login = await client.post(
        "/api/v1/auth/station",
        json={
            "clinic_slug": clinic.slug,
            "device_id": corpo["device_id"],
            "device_secret": corpo["device_secret"],
        },
    )
    assert login.status_code == 200, login.text

    lista = await client.get("/api/v1/station-devices", headers=headers)
    assert lista.status_code == 200
    [row] = lista.json()
    assert row["status"] == "active"
    assert row["last_seen_at"] is not None
    # Nem o segredo nem o código voltam na listagem.
    assert "secret_hash" not in row and "enrollment_code" not in row


async def test_codigo_morre_no_uso(client, session):
    clinic, admin = await _admin(session)
    await session.commit()
    aberto = await client.post(
        "/api/v1/station-devices",
        json={"name": "Balcão"},
        headers=bearer(personal_token(admin)),
    )
    code = aberto.json()["enrollment_code"]

    primeiro = await client.post(
        "/api/v1/auth/station/enroll", json={"clinic_slug": clinic.slug, "code": code}
    )
    assert primeiro.status_code == 200

    # Um código que continua valendo é uma segunda porta para o mesmo aparelho.
    segundo = await client.post(
        "/api/v1/auth/station/enroll", json={"clinic_slug": clinic.slug, "code": code}
    )
    assert segundo.status_code == 401


async def test_codigo_expirado_nao_entra(client, session):
    clinic, admin = await _admin(session)
    await session.commit()
    aberto = await client.post(
        "/api/v1/station-devices",
        json={"name": "Balcão"},
        headers=bearer(personal_token(admin)),
    )
    code = aberto.json()["enrollment_code"]

    device = await session.scalar(sa.select(StationDevice))
    device.enrollment_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    tarde = await client.post(
        "/api/v1/auth/station/enroll", json={"clinic_slug": clinic.slug, "code": code}
    )
    assert tarde.status_code == 401


async def test_revogar_um_aparelho_nao_derruba_o_outro(client, session):
    clinic, admin = await _admin(session)
    await session.commit()
    headers = bearer(personal_token(admin))

    credenciais = []
    for nome in ("Tablet da UTI", "Balcão"):
        aberto = await client.post("/api/v1/station-devices", json={"name": nome}, headers=headers)
        entrou = await client.post(
            "/api/v1/auth/station/enroll",
            json={"clinic_slug": clinic.slug, "code": aberto.json()["enrollment_code"]},
        )
        credenciais.append(entrou.json())

    revogado, mantido = credenciais
    resp = await client.post(
        f"/api/v1/station-devices/{revogado['device_id']}/revoke", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"

    def corpo(cred):
        return {
            "clinic_slug": clinic.slug,
            "device_id": cred["device_id"],
            "device_secret": cred["device_secret"],
        }

    assert (await client.post("/api/v1/auth/station", json=corpo(revogado))).status_code == 401
    # A diferença central para a chave compartilhada: o outro continua de pé.
    assert (await client.post("/api/v1/auth/station", json=corpo(mantido))).status_code == 200


async def test_cinco_pins_errados_travam_o_aparelho_ate_o_administrador_liberar(client, session):
    clinic, admin = await _admin(session)
    tecnico_user = await make_user(session)
    tecnico = await make_membership(
        session, clinic=clinic, user=tecnico_user, role="tech", pin_hash=hash_password("246810")
    )
    await session.commit()
    headers = bearer(personal_token(admin))

    aberto = await client.post(
        "/api/v1/station-devices", json={"name": "Tablet da UTI"}, headers=headers
    )
    entrou = await client.post(
        "/api/v1/auth/station/enroll",
        json={"clinic_slug": clinic.slug, "code": aberto.json()["enrollment_code"]},
    )
    device_id = entrou.json()["device_id"]
    estacao = bearer(
        station_token(clinic, station_id=device_id, station_key_version=clinic.station_key_version)
    )

    for _ in range(4):
        errado = await client.post("/api/v1/auth/pin", json={"pin": "000000"}, headers=estacao)
        assert errado.status_code == 401

    quinto = await client.post("/api/v1/auth/pin", json={"pin": "000000"}, headers=estacao)
    assert quinto.status_code == 423
    assert quinto.json()["error"]["code"] == "device_locked"

    # Travado, nem o PIN CERTO passa: esperar não resolve, e a interface
    # precisa mandar chamar alguém em vez de sugerir nova tentativa.
    certo = await client.post("/api/v1/auth/pin", json={"pin": "246810"}, headers=estacao)
    assert certo.status_code == 423

    liberado = await client.post(f"/api/v1/station-devices/{device_id}/unlock", headers=headers)
    assert liberado.status_code == 200
    assert liberado.json()["pin_locked_at"] is None

    depois = await client.post("/api/v1/auth/pin", json={"pin": "246810"}, headers=estacao)
    assert depois.status_code == 200
    assert "operator_token" in depois.json()
    assert tecnico.id is not None


async def test_acerto_zera_a_contagem_de_erros(client, session):
    clinic, admin = await _admin(session)
    user = await make_user(session)
    await make_membership(
        session, clinic=clinic, user=user, role="tech", pin_hash=hash_password("135791")
    )
    await session.commit()
    headers = bearer(personal_token(admin))

    aberto = await client.post("/api/v1/station-devices", json={"name": "Balcão"}, headers=headers)
    entrou = await client.post(
        "/api/v1/auth/station/enroll",
        json={"clinic_slug": clinic.slug, "code": aberto.json()["enrollment_code"]},
    )
    device_id = entrou.json()["device_id"]
    estacao = bearer(station_token(clinic, station_id=device_id))

    for _ in range(4):
        await client.post("/api/v1/auth/pin", json={"pin": "000000"}, headers=estacao)
    assert (
        await client.post("/api/v1/auth/pin", json={"pin": "135791"}, headers=estacao)
    ).status_code == 200

    # Zerou: dá para errar quatro de novo sem travar.
    for _ in range(4):
        assert (
            await client.post("/api/v1/auth/pin", json={"pin": "000000"}, headers=estacao)
        ).status_code == 401


async def test_tecnico_nao_libera_nem_revoga_aparelho(client, session):
    clinic, admin = await _admin(session)
    user = await make_user(session)
    tecnico = await make_membership(session, clinic=clinic, user=user, role="tech")
    await session.commit()

    negado = await client.post(
        "/api/v1/station-devices",
        json={"name": "Tablet do técnico"},
        headers=bearer(personal_token(tecnico)),
    )
    assert negado.status_code == 403

    aberto = await client.post(
        "/api/v1/station-devices",
        json={"name": "Balcão"},
        headers=bearer(personal_token(admin)),
    )
    device_id = aberto.json()["device"]["id"]
    assert (
        await client.post(
            f"/api/v1/station-devices/{device_id}/revoke",
            headers=bearer(personal_token(tecnico)),
        )
    ).status_code == 403
    # Ler a lista de aparelhos também é do administrador.
    assert (
        await client.get("/api/v1/station-devices", headers=bearer(personal_token(tecnico)))
    ).status_code == 403


async def test_aparelho_de_outra_clinica_nao_entra(client, session):
    clinic_a, admin_a = await _admin(session)
    clinic_b = await make_clinic(session)
    await session.commit()

    aberto = await client.post(
        "/api/v1/station-devices",
        json={"name": "Tablet da UTI"},
        headers=bearer(personal_token(admin_a)),
    )
    code = aberto.json()["enrollment_code"]

    # O mesmo código, no slug da outra clínica, não vale nada.
    outra = await client.post(
        "/api/v1/auth/station/enroll", json={"clinic_slug": clinic_b.slug, "code": code}
    )
    assert outra.status_code == 401
