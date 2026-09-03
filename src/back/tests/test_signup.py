"""A porta pública: uma clínica nasce sem passar por ninguém."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.models.clinic import Clinic
from tests.factories import make_user
from tests.helpers import bearer

CORPO = {
    "clinic_name": "Clínica Vida Animal",
    "admin_name": "Paula Martins",
    "email": "paula@vida.vet",
    "password": "senha-boa-123",
    "phone": "61999998888",
}


@pytest.fixture(autouse=True)
def _throttle_limpo():
    # O limite é de PROCESSO: sem zerar, o sexto teste do arquivo levaria 429.
    from app.api.routes.signup import signup_throttle

    signup_throttle.reset_all()
    yield
    signup_throttle.reset_all()


@pytest.mark.asyncio
async def test_cadastro_cria_clinica_e_ja_entra(client, session):
    resposta = await client.post("/api/v1/signup", json=CORPO)
    assert resposta.status_code == 201, resposta.text
    token = resposta.json()["access_token"]

    # O token vale de verdade: entra em /auth/me como administrador.
    me = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_a_clinica_nasce_em_teste_de_14_dias(client, session):
    await client.post("/api/v1/signup", json=CORPO)
    clinic = await session.scalar(sa.select(Clinic).where(Clinic.name == "Clínica Vida Animal"))
    assert clinic.plan_tier == "trial"
    assert clinic.subscription_status == "trial"
    assert clinic.bed_limit == 10
    faltam = clinic.trial_ends_at - datetime.now(UTC)
    assert timedelta(days=13, hours=23) < faltam <= timedelta(days=14)


@pytest.mark.asyncio
async def test_slug_sai_do_nome_sem_perguntar(client, session):
    await client.post("/api/v1/signup", json=CORPO)
    clinic = await session.scalar(sa.select(Clinic).where(Clinic.name == "Clínica Vida Animal"))
    assert clinic.slug == "clinica-vida-animal"


@pytest.mark.asyncio
async def test_duas_clinicas_com_o_mesmo_nome_convivem(client, session):
    primeira = await client.post("/api/v1/signup", json=CORPO)
    segunda = await client.post("/api/v1/signup", json={**CORPO, "email": "outra@vida.vet"})
    assert primeira.status_code == 201
    assert segunda.status_code == 201
    slugs = list(
        (
            await session.execute(
                sa.select(Clinic.slug).where(Clinic.name == "Clínica Vida Animal")
            )
        ).scalars()
    )
    assert len(slugs) == 2
    assert len(set(slugs)) == 2


@pytest.mark.asyncio
async def test_email_ja_cadastrado_recusa_com_o_codigo_certo(client, session):
    await make_user(session, email="paula@vida.vet")
    resposta = await client.post("/api/v1/signup", json=CORPO)
    assert resposta.status_code == 409
    assert resposta.json()["error"]["code"] == "email_taken"


@pytest.mark.asyncio
async def test_corrida_de_email_duplicado_vira_409_nao_500(client, monkeypatch):
    """O SELECT prévio em `OnboardingService.create_clinic` cobre o caso
    comum (email já existe ANTES do cadastro rodar); isto simula a corrida:
    duas requisições simultâneas passam as duas pela checagem antes de
    qualquer uma comitar, e a perdedora esbarra na constraint única do banco
    como `IntegrityError`, que sem tradução vira 500."""
    import app.api.routes.signup as signup_module

    async def _esbarra_na_constraint(*args, **kwargs):
        raise sa.exc.IntegrityError("insert", {}, Exception("duplicate key value"))

    monkeypatch.setattr(signup_module.OnboardingService, "create_clinic", _esbarra_na_constraint)

    resposta = await client.post("/api/v1/signup", json=CORPO)
    assert resposta.status_code == 409
    assert resposta.json()["error"]["code"] == "email_taken"


@pytest.mark.asyncio
async def test_cadastro_que_falha_devolve_a_vaga_reservada(client, session):
    """A vaga é reservada ANTES do cadastro rodar, para valer contra rajada
    concorrente (item crítico da revisão); se o cadastro falha, `refund`
    devolve a vaga — cinco tentativas com e-mail repetido não podem gastar a
    cota de quem vai corrigir e tentar de novo."""
    await make_user(session, email="paula@vida.vet")
    for _ in range(5):
        resposta = await client.post("/api/v1/signup", json=CORPO)
        assert resposta.status_code == 409, resposta.text

    ok = await client.post("/api/v1/signup", json={**CORPO, "email": "corrigido@vida.vet"})
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_senha_curta_e_recusada(client):
    resposta = await client.post("/api/v1/signup", json={**CORPO, "password": "1234"})
    assert resposta.status_code == 422


@pytest.mark.asyncio
async def test_senha_de_73_bytes_e_422_nao_500(client):
    """`bcrypt.hashpw` LEVANTA acima de 72 bytes em vez de truncar (bcrypt
    5.0.0); sem o teto no schema isto era 500 sem handler, no único formulário
    público do lançamento — exatamente onde um gerenciador de senhas manda uma
    senha longa."""
    resposta = await client.post("/api/v1/signup", json={**CORPO, "password": "a" * 73})
    assert resposta.status_code == 422


@pytest.mark.asyncio
async def test_senha_de_72_caracteres_com_acento_e_422_nao_500(client):
    """72 caracteres, 73 bytes: o bcrypt conta bytes, e o produto é pt-BR.

    O limite em caracteres deixava passar exatamente a senha que um brasileiro
    escreve, e o ValueError do bcrypt virava 500 sem handler."""
    senha = "a" * 71 + "ç"
    assert len(senha) == 72
    assert len(senha.encode("utf-8")) == 73

    resposta = await client.post("/api/v1/signup", json={**CORPO, "password": senha})
    assert resposta.status_code == 422, resposta.text


@pytest.mark.asyncio
async def test_senha_de_72_bytes_com_acento_e_aceita(client):
    """O limite não pode ficar mais restritivo do que o bcrypt exige: 71
    caracteres com um acento são 72 bytes, e isto precisa continuar passando."""
    senha = "a" * 70 + "ç"
    assert len(senha) == 71
    assert len(senha.encode("utf-8")) == 72

    resposta = await client.post("/api/v1/signup", json={**CORPO, "password": senha})
    assert resposta.status_code == 201, resposta.text


@pytest.mark.asyncio
async def test_o_sexto_cadastro_do_mesmo_ip_na_mesma_hora_e_barrado(client):
    for i in range(5):
        resposta = await client.post("/api/v1/signup", json={**CORPO, "email": f"vet{i}@vida.vet"})
        assert resposta.status_code == 201, resposta.text
    barrado = await client.post("/api/v1/signup", json={**CORPO, "email": "sexto@vida.vet"})
    assert barrado.status_code == 429
    assert barrado.json()["error"]["code"] == "signup_rate_limited"


@pytest.mark.asyncio
async def test_ips_diferentes_nao_compartilham_o_limite(client):
    for i in range(5):
        await client.post("/api/v1/signup", json={**CORPO, "email": f"vet{i}@vida.vet"})
    outro = await client.post(
        "/api/v1/signup",
        json={**CORPO, "email": "outro-ip@vida.vet"},
        headers={"X-Forwarded-For": "203.0.113.7"},
    )
    assert outro.status_code == 201, outro.text


@pytest.mark.asyncio
async def test_hop_forjado_nao_escapa_do_limite(client):
    """O primeiro elemento de X-Forwarded-For é do cliente; o último é do proxy.

    Sem isto, sortear um valor novo no header a cada chamada daria cadastros
    ilimitados na única rota que cria clínica sem credencial."""
    # Cinco cadastros "do mesmo proxy", cada um alegando vir de um IP diferente.
    for i in range(5):
        resposta = await client.post(
            "/api/v1/signup",
            json={**CORPO, "email": f"vet{i}@vida.vet"},
            headers={"X-Forwarded-For": f"10.0.0.{i}, 198.51.100.9"},
        )
        assert resposta.status_code == 201, resposta.text

    barrado = await client.post(
        "/api/v1/signup",
        json={**CORPO, "email": "sexto@vida.vet"},
        headers={"X-Forwarded-For": "10.0.0.99, 198.51.100.9"},
    )
    assert barrado.status_code == 429
    assert barrado.json()["error"]["code"] == "signup_rate_limited"
