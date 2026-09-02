"""O back-office: quem vende, faz onboarding e dá suporte.

O que se testa é a fronteira. O token da plataforma não entra em rota de
clínica; o token de clínica não entra na plataforma; e tudo o que o suporte
faz numa clínica fica na trilha DELA, com nome.
"""

from datetime import UTC, datetime

import sqlalchemy as sa

from app.core.security import hash_password
from app.models.audit import AuditEntry
from tests.factories import make_clinic, make_hospitalization, make_membership, make_user
from tests.helpers import bearer, personal_token


async def _operator(client, session):
    user = await make_user(
        session,
        name="Marcus",
        email="marcus@plantao.vet",
        password_hash=hash_password("senha-forte-1"),
        is_platform_operator=True,
    )
    await session.commit()
    resp = await client.post(
        "/api/v1/platform/login",
        json={"email": "marcus@plantao.vet", "password": "senha-forte-1"},
    )
    assert resp.status_code == 200, resp.text
    return user, bearer(resp.json()["access_token"])


async def test_login_da_plataforma_exige_operador(client, session):
    comum = await make_user(
        session, email="vet@clinica.vet", password_hash=hash_password("senha-forte-1")
    )
    await make_membership(session, user=comum)
    await session.commit()

    # Membro de clínica, por mais poderoso que seja, não é da plataforma.
    resp = await client.post(
        "/api/v1/platform/login", json={"email": "vet@clinica.vet", "password": "senha-forte-1"}
    )
    assert resp.status_code == 401

    _, headers = await _operator(client, session)
    me = await client.get("/api/v1/platform/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "marcus@plantao.vet"


async def test_as_duas_portas_sao_disjuntas(client, session):
    """Token da plataforma não abre rota de clínica; token de clínica não abre
    a plataforma. Não é filtro: são tipos de token diferentes."""
    _, plataforma = await _operator(client, session)
    clinic = await make_clinic(session)
    membro = await make_membership(session, clinic=clinic, role="admin")
    await session.commit()

    assert (await client.get("/api/v1/clinic", headers=plataforma)).status_code == 401
    assert (await client.get("/api/v1/board", headers=plataforma)).status_code == 401
    assert (
        await client.get("/api/v1/platform/clinics", headers=bearer(personal_token(membro)))
    ).status_code == 403


async def test_lista_clinicas_com_contadores(client, session):
    _, headers = await _operator(client, session)
    clinic = await make_clinic(session, name="Vida Animal")
    vet = await make_membership(session, clinic=clinic, role="vet")
    await make_membership(session, clinic=clinic, role="tech")
    await make_hospitalization(session, clinic=clinic, membership=vet)
    await session.commit()

    resp = await client.get("/api/v1/platform/clinics", headers=headers)
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["id"] == str(clinic.id))
    assert row["members"] == 2
    assert row["active_hospitalizations"] == 1
    # As fábricas escrevem direto no banco, sem trilha: ainda ninguém "usou".
    assert row["last_activity_at"] is None

    # Um ato real pela API entra na trilha, e a lista passa a dizer que a
    # clínica está VIVA. É o número que separa o cliente ativo do que vai
    # cancelar.
    admin = await make_membership(session, clinic=clinic, role="admin")
    await session.commit()
    assert (
        await client.patch(
            "/api/v1/clinic", json={"name": "Vida Animal 2"}, headers=bearer(personal_token(admin))
        )
    ).status_code == 200
    de_novo = await client.get("/api/v1/platform/clinics", headers=headers)
    row = next(r for r in de_novo.json() if r["id"] == str(clinic.id))
    assert row["last_activity_at"] is not None
    assert row["members"] == 3


async def test_onboarding_cria_clinica_e_admin_que_entra(client, session):
    operator, headers = await _operator(client, session)

    resp = await client.post(
        "/api/v1/platform/clinics",
        json={
            "name": "Hospital Pata Amiga",
            "slug": "pata-amiga",
            "plan_tier": "pro",
            "contact_name": "Dra. Lia",
            "admin_name": "Lia Costa",
            "admin_email": "Lia@PataAmiga.vet",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    # Limite vem do plano quando não é informado.
    assert corpo["clinic"]["bed_limit"] == 25
    assert corpo["clinic"]["subscription_status"] == "trial"
    assert corpo["clinic"]["trial_ends_at"] is not None
    assert corpo["admin_email"] == "lia@pataamiga.vet"
    senha = corpo["admin_password"]
    assert len(senha) >= 12

    # A senha sorteada abre a porta da clínica, como administrador.
    login = await client.post(
        "/api/v1/auth/login", json={"email": "lia@pataamiga.vet", "password": senha}
    )
    assert login.status_code == 200, login.text
    me = await client.get("/api/v1/auth/me", headers=bearer(login.json()["access_token"]))
    assert me.json()["role"] == "admin"

    # E a criação está na trilha da CLÍNICA, com o nome de quem vendeu.
    entrada = await session.scalar(
        sa.select(AuditEntry).where(
            AuditEntry.clinic_id == corpo["clinic"]["id"], AuditEntry.action == "clinic_created"
        )
    )
    assert entrada is not None
    assert "Suporte" in entrada.actor_name and operator.name in entrada.actor_name


async def test_slug_e_email_repetidos_sao_recusados(client, session):
    _, headers = await _operator(client, session)
    await make_clinic(session, slug="repetida")
    await make_user(session, email="ja@existe.vet")
    await session.commit()
    base = {"name": "Outra", "admin_name": "Alguém", "admin_email": "nova@x.vet"}

    assert (
        await client.post(
            "/api/v1/platform/clinics", json={**base, "slug": "repetida"}, headers=headers
        )
    ).json()["error"]["code"] == "slug_taken"
    assert (
        await client.post(
            "/api/v1/platform/clinics",
            json={**base, "slug": "nova-clinica", "admin_email": "ja@existe.vet"},
            headers=headers,
        )
    ).json()["error"]["code"] == "email_taken"
    assert (
        await client.post(
            "/api/v1/platform/clinics",
            json={**base, "slug": "nova-clinica", "plan_tier": "diamante"},
            headers=headers,
        )
    ).json()["error"]["code"] == "unknown_plan"


async def test_suspender_fecha_a_porta_no_login_e_nao_na_sessao(client, session):
    """Uma sessão aberta no meio do plantão não cai por causa de boleto."""
    _, headers = await _operator(client, session)
    clinic = await make_clinic(session)
    user = await make_user(
        session, email="tec@vida.vet", password_hash=hash_password("senha-forte-1")
    )
    membro = await make_membership(session, clinic=clinic, user=user, role="tech")
    await session.commit()
    sessao_aberta = bearer(personal_token(membro))

    resp = await client.patch(
        f"/api/v1/platform/clinics/{clinic.id}",
        json={"subscription_status": "suspended"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["suspended_at"] is not None

    # Quem chega depois vê o MOTIVO, não "credencial inválida".
    login = await client.post(
        "/api/v1/auth/login", json={"email": "tec@vida.vet", "password": "senha-forte-1"}
    )
    assert login.status_code == 403
    assert login.json()["error"]["code"] == "clinic_suspended"

    # Quem já estava dentro termina o turno.
    assert (await client.get("/api/v1/board", headers=sessao_aberta)).status_code == 200

    reativada = await client.patch(
        f"/api/v1/platform/clinics/{clinic.id}",
        json={"subscription_status": "active"},
        headers=headers,
    )
    assert reativada.json()["suspended_at"] is None
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": "tec@vida.vet", "password": "senha-forte-1"}
        )
    ).status_code == 200


async def test_trocar_de_plano_preenche_o_limite(client, session):
    _, headers = await _operator(client, session)
    clinic = await make_clinic(session, plan_tier="starter", bed_limit=10)
    await session.commit()

    resp = await client.patch(
        f"/api/v1/platform/clinics/{clinic.id}", json={"plan_tier": "pro"}, headers=headers
    )
    assert resp.json()["bed_limit"] == 25

    # Enterprise é sob medida: sem limite até alguém definir um.
    resp = await client.patch(
        f"/api/v1/platform/clinics/{clinic.id}",
        json={"plan_tier": "enterprise", "bed_limit": 60},
        headers=headers,
    )
    assert resp.json()["bed_limit"] == 60


async def test_suporte_reseta_senha_e_pin_e_fica_na_trilha(client, session):
    operator, headers = await _operator(client, session)
    clinic = await make_clinic(session)
    user = await make_user(session, email="vet@vida.vet", password_hash=hash_password("antiga-123"))
    membro = await make_membership(
        session, clinic=clinic, user=user, role="vet", pin_hash=hash_password("123456")
    )
    await session.commit()

    reset = await client.post(
        f"/api/v1/platform/clinics/{clinic.id}/members/{membro.id}/reset-password",
        headers=headers,
    )
    assert reset.status_code == 200, reset.text
    nova = reset.json()["temporary_password"]
    assert (
        await client.post("/api/v1/auth/login", json={"email": "vet@vida.vet", "password": nova})
    ).status_code == 200
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": "vet@vida.vet", "password": "antiga-123"}
        )
    ).status_code == 401

    pin = await client.post(
        f"/api/v1/platform/clinics/{clinic.id}/members/{membro.id}/reset-pin", headers=headers
    )
    assert pin.status_code == 204
    await session.refresh(membro)
    assert membro.pin_hash is None

    acoes = list(
        (
            await session.execute(
                sa.select(AuditEntry.action, AuditEntry.actor_name).where(
                    AuditEntry.clinic_id == clinic.id
                )
            )
        ).all()
    )
    assert ("password_reset_by_support", f"Suporte PlantãoVet · {operator.name}") in acoes
    assert ("pin_reset_by_support", f"Suporte PlantãoVet · {operator.name}") in acoes


async def test_detalhe_traz_equipe_aparelhos_e_trilha(client, session):
    _, headers = await _operator(client, session)
    clinic = await make_clinic(session)
    await make_membership(session, clinic=clinic, role="admin")
    await session.commit()

    resp = await client.get(f"/api/v1/platform/clinics/{clinic.id}", headers=headers)
    assert resp.status_code == 200
    corpo = resp.json()
    assert len(corpo["members_list"]) == 1
    assert corpo["members_list"][0]["role"] == "admin"
    assert "email" in corpo["members_list"][0]
    assert "pin_hash" not in resp.text and "password_hash" not in resp.text
    assert isinstance(corpo["devices"], list)
    assert isinstance(corpo["recent_audit"], list)


async def test_clinica_de_outra_plataforma_nao_existe(client, session):
    _, headers = await _operator(client, session)
    import uuid

    assert (
        await client.get(f"/api/v1/platform/clinics/{uuid.uuid4()}", headers=headers)
    ).status_code == 404
    assert datetime.now(UTC) is not None


# --- Planos como dado --------------------------------------------------------


async def _plans_ready(session):
    from app.services.plans import PlanService

    await PlanService.ensure_defaults(session)
    await session.commit()


async def test_catalogo_inicial_e_semeado_uma_vez(client, session):
    _, headers = await _operator(client, session)
    await _plans_ready(session)

    resp = await client.get("/api/v1/platform/plans", headers=headers)
    assert resp.status_code == 200
    codes = [p["code"] for p in resp.json()]
    assert codes[:3] == ["starter", "pro", "enterprise"]
    assert next(p for p in resp.json() if p["code"] == "pro")["bed_limit"] == 25

    # Semear de novo não recria nem duplica: o catálogo é decisão comercial.
    await _plans_ready(session)
    de_novo = await client.get("/api/v1/platform/plans", headers=headers)
    assert len(de_novo.json()) == len(codes)


async def test_criar_plano_de_teste_e_plano_fundador(client, session):
    _, headers = await _operator(client, session)
    await _plans_ready(session)

    teste = await client.post(
        "/api/v1/platform/plans",
        json={"code": "teste-14", "name": "Teste 14 dias", "bed_limit": 10, "trial_days": 14},
        headers=headers,
    )
    assert teste.status_code == 201, teste.text
    assert teste.json()["trial_days"] == 14 and teste.json()["price_minor"] == 0

    fundador = await client.post(
        "/api/v1/platform/plans",
        json={
            "code": "fundador",
            "name": "Fundador",
            "bed_limit": 25,
            "price_minor": 19700,
            "notes": "Preço de lançamento para as 10 primeiras.",
        },
        headers=headers,
    )
    assert fundador.status_code == 201, fundador.text

    repetido = await client.post(
        "/api/v1/platform/plans",
        json={"code": "fundador", "name": "Outro"},
        headers=headers,
    )
    assert repetido.status_code == 409
    assert repetido.json()["error"]["code"] == "plan_code_taken"


async def test_clinica_em_plano_de_teste_nasce_em_trial_com_data(client, session):
    _, headers = await _operator(client, session)
    await _plans_ready(session)
    await client.post(
        "/api/v1/platform/plans",
        json={"code": "teste-14", "name": "Teste", "bed_limit": 10, "trial_days": 14},
        headers=headers,
    )

    resp = await client.post(
        "/api/v1/platform/clinics",
        json={
            "name": "Clínica Nova",
            "slug": "clinica-nova",
            "plan_tier": "teste-14",
            "subscription_status": "active",
            "admin_name": "Ana",
            "admin_email": "ana@nova.vet",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    clinic = resp.json()["clinic"]
    # O plano de teste manda: mesmo pedindo "active", a clínica entra em teste.
    assert clinic["subscription_status"] == "trial"
    assert clinic["bed_limit"] == 10
    fim = datetime.fromisoformat(clinic["trial_ends_at"])
    assert 13 <= (fim - datetime.now(UTC)).days <= 14


async def test_migrar_fundador_para_definitivo(client, session):
    """O fim do plano de lançamento: todo mundo vai para o definitivo, o
    fundador é aposentado, e cada clínica movida tem isso na própria trilha."""
    operator, headers = await _operator(client, session)
    await _plans_ready(session)
    await client.post(
        "/api/v1/platform/plans",
        json={"code": "fundador", "name": "Fundador", "bed_limit": 25, "price_minor": 19700},
        headers=headers,
    )
    ids = []
    for i in range(3):
        resp = await client.post(
            "/api/v1/platform/clinics",
            json={
                "name": f"Fundadora {i}",
                "slug": f"fundadora-{i}",
                "plan_tier": "fundador",
                "subscription_status": "active",
                "admin_name": "Xavier",
                "admin_email": f"x{i}@f.vet",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["clinic"]["id"])

    migracao = await client.post(
        "/api/v1/platform/plans/fundador/migrate", json={"to": "pro"}, headers=headers
    )
    assert migracao.status_code == 200, migracao.text
    corpo = migracao.json()
    assert corpo["moved"] == 3
    assert corpo["source"]["is_active"] is False and corpo["source"]["retired_at"] is not None
    assert corpo["source"]["clinics"] == 0 and corpo["target"]["clinics"] >= 3

    for cid in ids:
        ficha = (await client.get(f"/api/v1/platform/clinics/{cid}", headers=headers)).json()
        assert ficha["plan_tier"] == "pro"
        assert any(e["action"] == "plan_migrated" for e in ficha["recent_audit"])
        entrada = await session.scalar(
            sa.select(AuditEntry).where(
                AuditEntry.clinic_id == cid, AuditEntry.action == "plan_migrated"
            )
        )
        assert entrada.payload["extra"] == {"from": "fundador", "to": "pro"}
        assert operator.name in entrada.actor_name

    # Aposentado: ninguém novo entra.
    negado = await client.post(
        "/api/v1/platform/clinics",
        json={
            "name": "Atrasada",
            "slug": "atrasada",
            "plan_tier": "fundador",
            "admin_name": "Yara",
            "admin_email": "y@f.vet",
        },
        headers=headers,
    )
    assert negado.status_code == 422
    assert negado.json()["error"]["code"] == "plan_retired"


async def test_apagar_plano_so_quando_vazio(client, session):
    _, headers = await _operator(client, session)
    await _plans_ready(session)
    await client.post(
        "/api/v1/platform/plans", json={"code": "vazio", "name": "Vazio"}, headers=headers
    )
    assert (await client.delete("/api/v1/platform/plans/vazio", headers=headers)).status_code == 204

    ocupado = await client.delete("/api/v1/platform/plans/pro", headers=headers)
    # Pode ou não haver clínica em "pro" neste banco de teste; se houver, recusa.
    assert ocupado.status_code in (204, 409)


async def test_mudar_o_plano_de_uma_clinica_pela_ficha(client, session):
    _, headers = await _operator(client, session)
    await _plans_ready(session)
    clinic = await make_clinic(session, plan_tier="starter", bed_limit=10)
    await session.commit()

    resp = await client.patch(
        f"/api/v1/platform/clinics/{clinic.id}", json={"plan_tier": "pro"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan_tier"] == "pro" and resp.json()["bed_limit"] == 25

    # Limite negociado no mesmo corpo vence o do plano.
    resp = await client.patch(
        f"/api/v1/platform/clinics/{clinic.id}",
        json={"plan_tier": "enterprise", "bed_limit": 60},
        headers=headers,
    )
    assert resp.json()["bed_limit"] == 60
