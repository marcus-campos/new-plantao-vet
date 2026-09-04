"""O tour de boas-vindas: quem ainda não viu, e como se marca que viu."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.core.security import create_jwt
from app.models.membership import Membership
from tests.factories import make_clinic, make_membership, make_user
from tests.helpers import bearer, operator_token, personal_token, station_token


@pytest.mark.asyncio
async def test_vinculo_novo_ainda_nao_viu_o_tour(client, session):
    membership = await make_membership(session, role="vet")
    await session.flush()

    corpo = (
        await client.get("/api/v1/auth/me", headers=bearer(personal_token(membership)))
    ).json()
    assert corpo["tour_done"] is False


@pytest.mark.asyncio
async def test_marcar_o_tour_como_visto(client, session):
    membership = await make_membership(session, role="vet")
    await session.flush()
    headers = bearer(personal_token(membership))

    resposta = await client.put("/api/v1/auth/me/tour", headers=headers)
    assert resposta.status_code == 204

    corpo = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert corpo["tour_done"] is True


@pytest.mark.asyncio
async def test_marcar_duas_vezes_preserva_o_instante(client, session):
    """Idempotente: a interface pode chamar sem saber o estado, e duas abas
    abertas não reescrevem o registro uma da outra."""
    membership = await make_membership(session, role="vet")
    await session.flush()
    headers = bearer(personal_token(membership))

    await client.put("/api/v1/auth/me/tour", headers=headers)
    primeiro = await session.scalar(
        sa.select(Membership.tour_done_at).where(Membership.id == membership.id)
    )

    segunda = await client.put("/api/v1/auth/me/tour", headers=headers)
    assert segunda.status_code == 204
    depois = await session.scalar(
        sa.select(Membership.tour_done_at).where(Membership.id == membership.id)
    )
    assert depois == primeiro


@pytest.mark.asyncio
async def test_a_estacao_nao_recebe_tour(client, session):
    """O aparelho do corredor é compartilhado e não é de ninguém: não há a quem
    apresentar a casa, e o tour não pode interromper o plantão de quem passa."""
    clinic = await make_clinic(session)
    membership = await make_membership(session, clinic=clinic, role="vet")
    await session.flush()

    corpo = (
        await client.get(
            "/api/v1/auth/me",
            headers={
                **bearer(station_token(clinic)),
                "X-Operator-Token": operator_token(membership),
            },
        )
    ).json()
    assert corpo["tour_done"] is True


@pytest.mark.asyncio
async def test_a_estacao_nao_marca_o_tour_de_ninguem(client, session):
    clinic = await make_clinic(session)
    await session.flush()

    resposta = await client.put(
        "/api/v1/auth/me/tour", headers=bearer(station_token(clinic))
    )
    assert resposta.status_code == 403


@pytest.mark.asyncio
async def test_quem_se_cadastra_pelo_site_ve_o_tour(client, session):
    """O caminho que mais importa: a clínica nasce e o administrador dela é a
    primeira pessoa a entrar no produto."""
    from app.api.routes.signup import signup_throttle

    signup_throttle.reset_all()
    resposta = await client.post(
        "/api/v1/signup",
        json={
            "clinic_name": "Clínica do Tour",
            "admin_name": "Paula Martins",
            "email": "tour@vida.vet",
            "password": "senha-boa-123",
        },
    )
    assert resposta.status_code == 201
    token = resposta.json()["access_token"]

    corpo = (await client.get("/api/v1/auth/me", headers=bearer(token))).json()
    assert corpo["role"] == "admin"
    assert corpo["tour_done"] is False


@pytest.mark.asyncio
async def test_suporte_reativa_o_tour_de_alguem(client, session):
    """O caminho do back-office: mostrar o produto a quem pulou o tour e
    depois se perdeu."""
    operador = await make_user(session, is_platform_operator=True)
    membership = await make_membership(session, role="vet")
    membership.tour_done_at = datetime.now(UTC)
    await session.flush()

    token = create_jwt({"kind": "platform", "sub": str(operador.id)}, expires_in=timedelta(hours=1))

    resposta = await client.post(
        f"/api/v1/platform/clinics/{membership.clinic_id}"
        f"/members/{membership.id}/reset-tour",
        headers=bearer(token),
    )
    assert resposta.status_code == 204

    zerado = await session.scalar(
        sa.select(Membership.tour_done_at).where(Membership.id == membership.id)
    )
    assert zerado is None

    # E a pessoa volta a ver o tour na próxima entrada.
    corpo = (
        await client.get("/api/v1/auth/me", headers=bearer(personal_token(membership)))
    ).json()
    assert corpo["tour_done"] is False
