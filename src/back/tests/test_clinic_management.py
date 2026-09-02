"""Configurações da clínica, gestão de equipe e comunicação com o tutor."""

import uuid
from datetime import UTC, datetime

import httpx
import pytest

from app.api.deps import get_session
from app.api.routes import clinic_settings, owner_contacts
from app.main import create_app
from app.models.membership import Membership
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_owner,
    make_patient,
    make_user,
)
from tests.helpers import bearer, personal_token, station_token


@pytest.fixture
async def client(db_session):
    # Fixture local: registra os routers desta entrega mesmo antes de o
    # integrador colocá-los em app/main.py (que este agente não toca).
    app = create_app()
    registered = {getattr(route, "path", None) for route in app.routes}
    for router in (clinic_settings.router, owner_contacts.router):
        if not any(getattr(r, "path", None) in registered for r in router.routes):
            app.include_router(router)

    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def _staff(session, clinic=None, role="admin"):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role=role)
    return clinic, membership


async def test_get_clinic_traz_configuracoes_e_internacoes_ativas(client, session):
    clinic, admin = await _staff(session)
    await make_hospitalization(session, clinic=clinic)

    resp = await client.get("/api/v1/clinic", headers=bearer(personal_token(admin)))

    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == clinic.slug
    assert body["locale"] == "pt-BR"
    assert body["currency"] == "BRL"
    assert body["unit_system"] == "metric"
    assert body["anchors"]["480"] == ["10:00", "18:00", "02:00"]
    assert body["station_key_version"] == 1
    assert body["active_hospitalizations"] == 1


async def test_patch_clinic_so_admin_vet_recebe_403(client, session):
    clinic, admin = await _staff(session)
    _, vet = await _staff(session, clinic=clinic, role="vet")

    negado = await client.patch(
        "/api/v1/clinic",
        json={"name": "Clínica Nova"},
        headers=bearer(personal_token(vet)),
    )
    assert negado.status_code == 403
    assert negado.json()["error"]["code"] == "forbidden"

    permitido = await client.patch(
        "/api/v1/clinic",
        json={"name": "Clínica Nova", "timezone": "America/Manaus"},
        headers=bearer(personal_token(admin)),
    )
    assert permitido.status_code == 200
    assert permitido.json()["name"] == "Clínica Nova"
    assert permitido.json()["timezone"] == "America/Manaus"


async def test_clinica_nao_muda_o_proprio_limite_de_leitos(client, session):
    """O leito é a unidade de cobrança: quem vende é quem muda.

    Ficava editável pelo administrador da clínica, que podia subir o próprio
    limite. Agora o campo é recusado com 422, e não ignorado em silêncio:
    campo ignorado parece campo aceito."""
    clinic, admin = await _staff(session)
    clinic.bed_limit = 10
    await session.commit()

    for corpo in ({"bed_limit": 60}, {"plan_tier": "enterprise"}):
        resp = await client.patch(
            "/api/v1/clinic", json=corpo, headers=bearer(personal_token(admin))
        )
        assert resp.status_code == 422, corpo
        assert resp.json()["error"]["code"] == "validation_error"

    leitura = await client.get("/api/v1/clinic", headers=bearer(personal_token(admin)))
    assert leitura.json()["bed_limit"] == 10


@pytest.mark.parametrize(
    "anchors",
    [
        {"480": ["25:00"]},
        {"480": ["8:00"]},
        {"manha": ["08:00"]},
        {"480": "08:00"},
        {"480": []},
        {"0": ["08:00"]},
    ],
)
async def test_ancoras_invalidas_devolvem_422(client, session, anchors):
    _, admin = await _staff(session)
    resp = await client.patch(
        "/api/v1/clinic", json={"anchors": anchors}, headers=bearer(personal_token(admin))
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert resp.json()["error"]["params"]["field"] == "anchors"


async def test_ancoras_validas_sao_gravadas(client, session):
    _, admin = await _staff(session)
    resp = await client.patch(
        "/api/v1/clinic",
        json={"anchors": {"720": ["08:00", "20:00"], "1440": ["09:00"]}},
        headers=bearer(personal_token(admin)),
    )
    assert resp.status_code == 200
    assert resp.json()["anchors"] == {"720": ["08:00", "20:00"], "1440": ["09:00"]}


async def test_rotacao_incrementa_versao_e_devolve_chave_uma_vez(client, session):
    clinic, admin = await _staff(session)
    antigo = station_token(clinic)

    resp = await client.post(
        "/api/v1/clinic/rotate-station-key", headers=bearer(personal_token(admin))
    )
    assert resp.status_code == 200
    chave = resp.json()["station_key"]
    assert chave and resp.json()["station_key_version"] == 2

    # A chave em claro não volta em nenhuma leitura posterior.
    depois = await client.get("/api/v1/clinic", headers=bearer(personal_token(admin)))
    assert depois.json()["station_key_version"] == 2
    assert "station_key" not in depois.json()

    # Versão nova revoga todo token de estação emitido antes.
    revogado = await client.get("/api/v1/clinic", headers=bearer(antigo))
    assert revogado.status_code == 401
    assert revogado.json()["error"]["code"] == "station_key_rotated"

    # E a chave devolvida é a que autentica a estação a partir de agora.
    login = await client.post(
        "/api/v1/auth/station", json={"clinic_slug": clinic.slug, "station_key": chave}
    )
    assert login.status_code == 200


async def test_rotacao_negada_para_vet(client, session):
    _, vet = await _staff(session, role="vet")
    resp = await client.post(
        "/api/v1/clinic/rotate-station-key", headers=bearer(personal_token(vet))
    )
    assert resp.status_code == 403


async def test_criar_membership_e_listar_equipe_sem_pin_hash(client, session):
    clinic, admin = await _staff(session)

    criado = await client.post(
        "/api/v1/memberships",
        json={
            "name": "Dra. Ana",
            "email": "ana@clinica.com",
            "password": "senha-forte-1",
            "role": "vet",
            "license_number": "12345",
            "license_authority": "CRMV-SP",
        },
        headers=bearer(personal_token(admin)),
    )
    assert criado.status_code == 201
    assert criado.json()["email"] == "ana@clinica.com"
    assert criado.json()["has_pin"] is False
    assert "pin_hash" not in criado.json()

    novo_id = criado.json()["id"]
    definido = await client.post(
        f"/api/v1/memberships/{novo_id}/pin",
        json={"pin": "432109"},
        headers=bearer(personal_token(admin)),
    )
    assert definido.status_code == 204
    # O PIN existe no banco, mas nenhuma resposta o expõe.
    membership = await session.get(Membership, uuid.UUID(novo_id))
    await session.refresh(membership)
    assert membership.pin_hash is not None

    listagem = await client.get("/api/v1/memberships", headers=bearer(personal_token(admin)))
    assert listagem.status_code == 200
    items = listagem.json()["items"]
    assert len(items) == 2  # o admin da fixture e a vet recém-criada
    assert all("pin_hash" not in item for item in items)
    assert membership.pin_hash not in listagem.text
    nova = next(item for item in items if item["email"] == "ana@clinica.com")
    assert nova["has_pin"] is True
    assert nova["role"] == "vet"
    assert nova["license_authority"] == "CRMV-SP"


async def test_membership_duplicado_por_email_devolve_422(client, session):
    clinic, admin = await _staff(session)
    payload = {
        "name": "Dra. Ana",
        "email": "ana@clinica.com",
        "password": "senha-forte-1",
        "role": "vet",
    }
    primeiro = await client.post(
        "/api/v1/memberships", json=payload, headers=bearer(personal_token(admin))
    )
    assert primeiro.status_code == 201

    segundo = await client.post(
        "/api/v1/memberships", json=payload, headers=bearer(personal_token(admin))
    )
    assert segundo.status_code == 422
    assert segundo.json()["error"]["code"] == "validation_error"
    assert segundo.json()["error"]["params"]["field"] == "email"


async def test_criar_membership_negado_para_vet(client, session):
    _, vet = await _staff(session, role="vet")
    resp = await client.post(
        "/api/v1/memberships",
        json={"name": "X", "email": "x@y.com", "password": "senha-forte-1", "role": "tech"},
        headers=bearer(personal_token(vet)),
    )
    assert resp.status_code == 403


async def test_patch_membership_desativa_e_muda_role(client, session):
    clinic, admin = await _staff(session)
    _, alvo = await _staff(session, clinic=clinic, role="tech")

    resp = await client.patch(
        f"/api/v1/memberships/{alvo.id}",
        json={"role": "vet", "license_number": "999", "is_active": False},
        headers=bearer(personal_token(admin)),
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "vet"
    assert resp.json()["is_active"] is False
    assert resp.json()["license_number"] == "999"


async def test_membership_de_outra_clinica_nao_e_alcancavel(client, session):
    _, admin_a = await _staff(session)
    _, membro_b = await _staff(session, role="vet")

    resp = await client.patch(
        f"/api/v1/memberships/{membro_b.id}",
        json={"is_active": False},
        headers=bearer(personal_token(admin_a)),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_contato_registrado_aparece_no_historico(client, session):
    clinic, vet = await _staff(session, role="vet")
    hospitalization = await make_hospitalization(session, clinic=clinic)

    criado = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts",
        json={"channel": "phone", "summary": "Tutor avisado da piora noturna."},
        headers=bearer(personal_token(vet)),
    )
    assert criado.status_code == 201
    assert criado.json()["channel"] == "phone"
    assert criado.json()["direction"] == "outbound"
    assert criado.json()["author_name"]
    assert criado.json()["external_id"] is None

    historico = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts",
        headers=bearer(personal_token(vet)),
    )
    assert historico.status_code == 200
    assert [item["summary"] for item in historico.json()] == [
        "Tutor avisado da piora noturna."
    ]


async def test_whatsapp_sem_opt_in_devolve_409(client, session):
    clinic, vet = await _staff(session, role="vet")
    owner = await make_owner(session, clinic=clinic)
    patient = await make_patient(session, clinic=clinic, owner=owner)
    hospitalization = await make_hospitalization(session, clinic=clinic, patient=patient)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts/whatsapp",
        json={"body": "Boletim das 18h: Thor estável."},
        headers=bearer(personal_token(vet)),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "whatsapp_opt_in_required"

    historico = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts",
        headers=bearer(personal_token(vet)),
    )
    assert historico.json() == []


async def test_whatsapp_sem_provedor_nao_afirma_envio(client, session):
    """Este teste era a garantia da MENTIRA.

    Ele exigia 201 e um `external_id` não vazio sem nenhum provedor
    configurado, o que só passava porque o cliente devolvia `stub-<uuid>` e a
    rota gravava `sent_at` de qualquer jeito. O prontuário passava a conter
    registro auditado de uma entrega que nunca aconteceu, e um teste verde
    protegia isso.

    Agora: sem credencial, a API recusa, a tentativa fica registrada como
    tentativa (o log de contato é história clínica: mostra que a clínica tentou
    e não conseguiu) e `sent_at` continua nulo.
    """
    clinic, vet = await _staff(session, role="vet")
    owner = await make_owner(session, clinic=clinic, whatsapp_opt_in_at=datetime.now(UTC))
    patient = await make_patient(session, clinic=clinic, owner=owner)
    hospitalization = await make_hospitalization(session, clinic=clinic, patient=patient)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts/whatsapp",
        json={"body": "Boletim das 18h: Thor estável."},
        headers=bearer(personal_token(vet)),
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "whatsapp_not_configured"

    historico = (
        await client.get(
            f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts",
            headers=bearer(personal_token(vet)),
        )
    ).json()
    assert len(historico) == 1
    assert historico[0]["status"] == "failed"
    assert historico[0]["sent_at"] is None
    assert historico[0]["owner_id"] == str(owner.id)


async def test_historico_de_tutor_e_isolado_por_clinica(client, session):
    clinic_a, vet_a = await _staff(session, role="vet")
    hospitalization_a = await make_hospitalization(session, clinic=clinic_a)
    await client.post(
        f"/api/v1/hospitalizations/{hospitalization_a.id}/owner-contacts",
        json={"channel": "in_person", "summary": "Tutor esteve na clínica."},
        headers=bearer(personal_token(vet_a)),
    )

    _, vet_b = await _staff(session, role="vet")
    vazado = await client.get(
        f"/api/v1/hospitalizations/{hospitalization_a.id}/owner-contacts",
        headers=bearer(personal_token(vet_b)),
    )
    assert vazado.status_code == 404
    assert vazado.json()["error"]["code"] == "not_found"

    negado = await client.post(
        f"/api/v1/hospitalizations/{hospitalization_a.id}/owner-contacts",
        json={"channel": "phone", "summary": "não deveria entrar"},
        headers=bearer(personal_token(vet_b)),
    )
    assert negado.status_code == 404


async def test_clinica_so_enxerga_a_propria_configuracao(client, session):
    clinic_a, admin_a = await _staff(session)
    clinic_b, _ = await _staff(session)

    resp = await client.get("/api/v1/clinic", headers=bearer(personal_token(admin_a)))
    assert resp.json()["slug"] == clinic_a.slug
    assert resp.json()["slug"] != clinic_b.slug


async def test_roster_e_legivel_por_qualquer_membro(client, session):
    """A escala precisa mostrar quem está de plantão para todo mundo, não só admin."""
    from tests.factories import make_clinic, make_membership, make_user
    from tests.helpers import bearer, personal_token

    clinic = await make_clinic(session)
    vet_user = await make_user(session, name="Dra. Paula Martins")
    vet = await make_membership(
        session,
        clinic=clinic,
        user=vet_user,
        role="vet",
        license_number="12345",
        license_authority="CRMV-SP",
    )
    tech_user = await make_user(session, name="Marina Coelho")
    tech = await make_membership(session, clinic=clinic, user=tech_user, role="tech")

    # técnico (não admin) consegue ler o roster
    resp = await client.get("/api/v1/memberships/roster", headers=bearer(personal_token(tech)))
    assert resp.status_code == 200
    nomes = {row["name"] for row in resp.json()}
    assert {"Dra. Paula Martins", "Marina Coelho"} <= nomes

    paula = next(row for row in resp.json() if row["id"] == str(vet.id))
    assert paula["license_number"] == "12345"
    # o roster não expõe e-mail nem estado do PIN
    assert "email" not in paula
    assert "has_pin" not in paula


async def test_roster_nao_vaza_outra_clinica(client, session):
    from tests.factories import make_clinic, make_membership, make_user
    from tests.helpers import bearer, personal_token

    clinic_a = await make_clinic(session, slug="roster-a")
    user_a = await make_user(session)
    membership_a = await make_membership(session, clinic=clinic_a, user=user_a, role="tech")

    clinic_b = await make_clinic(session, slug="roster-b")
    user_b = await make_user(session, name="De outra clínica")
    await make_membership(session, clinic=clinic_b, user=user_b, role="vet")

    resp = await client.get(
        "/api/v1/memberships/roster", headers=bearer(personal_token(membership_a))
    )
    assert resp.status_code == 200
    assert "De outra clínica" not in {row["name"] for row in resp.json()}


async def test_fuso_invalido_e_recusado_na_entrada(client, session):
    """Um fuso inválido não falha aqui: falhava depois, em toda prescrição.

    `ZoneInfo(clinic.timezone)` roda dentro do aprazamento, do extrato e da
    diária: gravar "Sao Paulo" no lugar de "America/Sao_Paulo" transformava a
    próxima prescrição num 500, longe da tela que causou o problema.
    """
    _, admin = await _staff(session)
    headers = bearer(personal_token(admin))

    ruim = await client.patch("/api/v1/clinic", json={"timezone": "Sao Paulo"}, headers=headers)
    assert ruim.status_code == 422
    assert ruim.json()["error"]["params"]["field"] == "timezone"

    bom = await client.patch(
        "/api/v1/clinic", json={"timezone": "America/Recife"}, headers=headers
    )
    assert bom.status_code == 200


async def test_configuracao_da_clinica_e_do_admin(client, session):
    """Plano, limite de leitos e versão da chave de estação eram leitura aberta:
    a interface escondia o item de menu e a API respondia a qualquer token."""
    clinic, admin = await _staff(session)
    vet_user = await make_user(session)
    vet = await make_membership(session, clinic=clinic, user=vet_user, role="vet")
    await session.flush()

    negado = await client.get("/api/v1/clinic", headers=bearer(personal_token(vet)))
    assert negado.status_code == 403
    assert negado.json()["error"]["params"]["capability"] == "clinic.configure"

    assert (
        await client.get("/api/v1/clinic", headers=bearer(personal_token(admin)))
    ).status_code == 200


async def test_regionalizacao_fica_aberta_a_todo_membro(client, session):
    """Fuso, moeda e locale são de TODA tela: sem eles o cliente formata no
    relógio do aparelho. Vivem em `/clinic/profile`, que não carrega plano."""
    clinic, admin = await _staff(session)
    tech_user = await make_user(session)
    tech = await make_membership(session, clinic=clinic, user=tech_user, role="tech")
    await session.flush()

    perfil = await client.get("/api/v1/clinic/profile", headers=bearer(personal_token(tech)))
    assert perfil.status_code == 200
    corpo = perfil.json()
    assert corpo["timezone"] == clinic.timezone
    assert corpo["currency"] == clinic.currency
    assert "plan_tier" not in corpo and "bed_limit" not in corpo
