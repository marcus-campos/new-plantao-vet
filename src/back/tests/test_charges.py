import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from app.api.deps import get_session
from app.api.routes import charges as charge_routes
from app.api.routes import price_list as price_list_routes
from app.main import create_app
from app.models.audit import AuditEntry
from app.models.charge_item import ChargeItem
from app.models.price_list_item import PriceListItem
from app.services.charges import ChargeService
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_kennel,
    make_membership,
    make_prescription,
    make_task,
    make_user,
)
from tests.helpers import bearer, personal_token


@pytest.fixture
async def api(db_session):
    """Cliente com as rotas de preços/conta montadas.

    O registro em app/main.py é do integrador; até lá o teste monta o router à
    mão (e não duplica quando o registro já existir)."""
    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    if "/api/v1/price-list" not in paths:
        app.include_router(price_list_routes.router)
    if "/api/v1/hospitalizations/{hospitalization_id}/charges" not in paths:
        app.include_router(charge_routes.router)

    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def make_price_item(session, *, clinic, **overrides: Any) -> PriceListItem:
    values: dict[str, Any] = {
        "name": "Dipirona 500 mg",
        "category": "medication",
        "unit": "por dose",
        "price_minor": 1800,
        **overrides,
    }
    item = PriceListItem(clinic_id=clinic.id, **values)
    session.add(item)
    await session.flush()
    return item


async def admin_de(session, clinic):
    """Tabela de preços é ato de gestão: quem escreve nela é o administrador."""
    user = await make_user(session)
    return await make_membership(session, clinic=clinic, user=user, role="admin")


async def cenario(session, **hosp_overrides: Any):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hospitalization = await make_hospitalization(
        session, clinic=clinic, membership=membership, **hosp_overrides
    )
    return clinic, membership, hospitalization


async def charges_of(session, hospitalization) -> list[ChargeItem]:
    return list(
        (
            await session.execute(
                sa.select(ChargeItem)
                .where(ChargeItem.hospitalization_id == hospitalization.id)
                .order_by(ChargeItem.charged_at, ChargeItem.id)
            )
        ).scalars()
    )


async def test_execucao_gera_item_com_o_valor_da_prescricao(client, session):
    clinic, membership, hosp = await cenario(session)
    prescription = await make_prescription(
        session, clinic=clinic, hospitalization=hosp, price_minor=1800
    )
    task = await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        prescription_id=prescription.id,
        price_minor=prescription.price_minor,
    )

    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"values": {"note": "ok"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200

    itens = await charges_of(session, hosp)
    assert len(itens) == 1
    assert itens[0].total_minor == 1800
    assert itens[0].unit_price_minor == 1800
    assert itens[0].quantity == Decimal("1.00")
    assert itens[0].task_id == task.id
    assert str(itens[0].source) == "task_execution"


async def test_execucao_parcial_gera_item_proporcional(client, session):
    clinic, membership, hosp = await cenario(session)
    task = await make_task(session, clinic=clinic, hospitalization=hosp, price_minor=1000)

    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"partial": True, "values": {"dose_given": 0.4}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "partial"

    itens = await charges_of(session, hosp)
    assert len(itens) == 1
    assert itens[0].total_minor == 400
    assert itens[0].quantity == Decimal("0.40")


async def test_parcial_sem_numero_utilizavel_cobra_metade(client, session):
    clinic, membership, hosp = await cenario(session)
    task = await make_task(session, clinic=clinic, hospitalization=hosp, price_minor=1000)

    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"partial": True, "values": {"dose_given": "cão cuspiu metade"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200

    itens = await charges_of(session, hosp)
    assert len(itens) == 1
    assert itens[0].total_minor == 500


async def test_not_done_nao_gera_item(client, session):
    clinic, membership, hosp = await cenario(session)
    task = await make_task(session, clinic=clinic, hospitalization=hosp, price_minor=1800)

    resp = await client.post(
        f"/api/v1/tasks/{task.id}/not-done",
        json={"reason": "refused"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert await charges_of(session, hosp) == []


async def test_tarefa_sem_preco_nao_gera_item(client, session):
    clinic, membership, hosp = await cenario(session)
    task = await make_task(
        session, clinic=clinic, hospitalization=hosp, price_minor=None, title="Evolução do dia"
    )

    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={}, headers=bearer(personal_token(membership))
    )
    assert resp.status_code == 200
    assert await charges_of(session, hosp) == []


async def test_reajustar_a_tabela_nao_muda_conta_ja_lancada(api, client, session):
    """Regra crítica da spec: o preço é COPIADO, nunca referenciado."""
    clinic, membership, hosp = await cenario(session)
    item = await make_price_item(session, clinic=clinic, price_minor=1000)
    prescription = await make_prescription(
        session,
        clinic=clinic,
        hospitalization=hosp,
        price_minor=item.price_minor,
        price_list_item_id=item.id,
    )
    task = await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        prescription_id=prescription.id,
        price_minor=prescription.price_minor,
    )
    headers = bearer(personal_token(membership))
    assert (
        await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    ).status_code == 200

    gestor = bearer(personal_token(await admin_de(session, clinic)))
    reajuste = await api.patch(
        f"/api/v1/price-list/{item.id}", json={"price_minor": 5000}, headers=gestor
    )
    assert reajuste.status_code == 200
    assert reajuste.json()["price_minor"] == 5000

    extrato = await api.get(f"/api/v1/hospitalizations/{hosp.id}/charges", headers=headers)
    assert extrato.status_code == 200
    assert extrato.json()["total_minor"] == 1000

    await session.refresh(prescription)
    assert prescription.price_minor == 1000
    assert prescription.price_list_item_id == item.id
    itens = await charges_of(session, hosp)
    assert [i.unit_price_minor for i in itens] == [1000]


async def test_diaria_e_lancada_uma_vez_por_dia(session):
    clinic, membership, _ = await cenario(session)
    kennel = await make_kennel(session, clinic=clinic, name="UTI 3", area="uti")
    now = datetime.now(UTC)
    hosp = await make_hospitalization(
        session,
        clinic=clinic,
        membership=membership,
        kennel_id=kennel.id,
        admitted_at=now - timedelta(days=2),
    )
    await make_price_item(
        session,
        clinic=clinic,
        name="Diária UTI",
        category="care",
        unit="por dia",
        price_minor=15000,
        is_daily_rate=True,
        kennel_area="uti",
    )

    criados = await ChargeService.accrue_daily_rates(
        session, hospitalization=hosp, clinic=clinic, now=now
    )
    assert criados == 3  # dia da admissão, o seguinte e hoje

    de_novo = await ChargeService.accrue_daily_rates(
        session, hospitalization=hosp, clinic=clinic, now=now
    )
    assert de_novo == 0
    itens = await charges_of(session, hosp)
    assert len(itens) == 3
    assert {str(i.source) for i in itens} == {"daily_rate"}
    assert len({i.accrual_date for i in itens}) == 3


async def test_diaria_usa_a_area_do_box(session):
    clinic, membership, _ = await cenario(session)
    kennel = await make_kennel(session, clinic=clinic, name="Iso 1", area="isolation")
    now = datetime.now(UTC)
    hosp = await make_hospitalization(
        session, clinic=clinic, membership=membership, kennel_id=kennel.id, admitted_at=now
    )
    await make_price_item(
        session,
        clinic=clinic,
        name="Diária UTI",
        category="care",
        unit="por dia",
        price_minor=15000,
        is_daily_rate=True,
        kennel_area="uti",
    )
    await make_price_item(
        session,
        clinic=clinic,
        name="Diária isolamento",
        category="care",
        unit="por dia",
        price_minor=22000,
        is_daily_rate=True,
        kennel_area="isolation",
    )

    assert (
        await ChargeService.accrue_daily_rates(
            session, hospitalization=hosp, clinic=clinic, now=now
        )
        == 1
    )
    itens = await charges_of(session, hosp)
    assert itens[0].total_minor == 22000
    assert itens[0].description == "Diária isolamento"


async def test_clinica_sem_diaria_cadastrada_nao_lanca_nada(session):
    clinic, membership, hosp = await cenario(session)
    now = datetime.now(UTC)
    assert (
        await ChargeService.accrue_daily_rates(
            session, hospitalization=hosp, clinic=clinic, now=now
        )
        == 0
    )
    assert await charges_of(session, hosp) == []


async def test_extrato_soma_e_agrupa_por_dia(api, client, session):
    clinic, membership, hosp = await cenario(session)
    kennel = await make_kennel(session, clinic=clinic, name="Box 2", area="general")
    hosp.kennel_id = kennel.id
    # A execução é cobrada no relógio REAL, então a âncora do teste tem de ser
    # o relógio real também. Com `now` fixado às 12:00, a suíte rodando de
    # madrugada punha a diária num dia da clínica e a execução no anterior: o
    # teste passava o dia inteiro e quebrava depois da meia-noite UTC.
    now = datetime.now(UTC)
    hosp.admitted_at = now - timedelta(days=1)
    await session.flush()
    await make_price_item(
        session,
        clinic=clinic,
        name="Diária internação geral",
        category="care",
        unit="por dia",
        price_minor=10000,
        is_daily_rate=True,
        kennel_area="general",
    )
    await ChargeService.accrue_daily_rates(session, hospitalization=hosp, clinic=clinic, now=now)

    task = await make_task(session, clinic=clinic, hospitalization=hosp, price_minor=1800)
    headers = bearer(personal_token(membership))
    assert (
        await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    ).status_code == 200

    resp = await api.get(f"/api/v1/hospitalizations/{hosp.id}/charges", headers=headers)
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["currency"] == clinic.currency
    assert corpo["total_minor"] == 10000 + 10000 + 1800
    # Dois dias de internação = duas diárias, uma por dia LOCAL da clínica.
    assert len(corpo["days"]) == 2
    assert [day["total_minor"] for day in corpo["days"]] == [10000, 11800]
    # E a execução caiu no dia em que aconteceu, junto da diária daquele dia.
    hoje = corpo["days"][-1]
    assert sorted(item["total_minor"] for item in hoje["items"]) == [1800, 10000]
    assert sum(item["total_minor"] for day in corpo["days"] for item in day["items"]) == 21800


async def test_lancamento_manual_e_auditado(api, session):
    clinic, membership, hosp = await cenario(session)
    item = await make_price_item(
        session, clinic=clinic, name="Cateter 22G", unit="por unidade", price_minor=2500
    )
    headers = bearer(personal_token(membership))

    resp = await api.post(
        f"/api/v1/hospitalizations/{hosp.id}/charges",
        json={"price_list_item_id": str(item.id), "quantity": "2"},
        headers=headers,
    )
    assert resp.status_code == 201
    corpo = resp.json()
    assert corpo["description"] == "Cateter 22G"
    assert corpo["unit_price_minor"] == 2500
    assert corpo["total_minor"] == 5000
    assert corpo["source"] == "manual"

    entrada = (
        (
            await session.execute(
                sa.select(AuditEntry)
                .where(AuditEntry.clinic_id == clinic.id, AuditEntry.action == "charge_recorded")
                .order_by(AuditEntry.id.desc())
            )
        )
        .scalars()
        .first()
    )
    assert entrada is not None
    assert entrada.entity_type == "charge_item"

    extrato = await api.get(f"/api/v1/hospitalizations/{hosp.id}/charges", headers=headers)
    assert extrato.json()["total_minor"] == 5000


async def test_manual_sem_item_do_catalogo_exige_descricao_e_preco(api, session):
    clinic, membership, hosp = await cenario(session)
    headers = bearer(personal_token(membership))
    resp = await api.post(
        f"/api/v1/hospitalizations/{hosp.id}/charges", json={"quantity": "1"}, headers=headers
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_price_list_lista_so_ativos_por_padrao(api, session):
    clinic, membership, _ = await cenario(session)
    ativo = await make_price_item(session, clinic=clinic, name="Fluidoterapia")
    inativo = await make_price_item(session, clinic=clinic, name="Item velho", is_active=False)
    headers = bearer(personal_token(membership))

    ativos = await api.get("/api/v1/price-list", headers=headers)
    assert [i["id"] for i in ativos.json()["items"]] == [str(ativo.id)]

    todos = await api.get("/api/v1/price-list?include_inactive=true", headers=headers)
    assert {i["id"] for i in todos.json()["items"]} == {str(ativo.id), str(inativo.id)}


async def test_isolamento_de_tenant(api, session):
    clinic_a, membership_a, hosp_a = await cenario(session)
    clinic_b, membership_b, _ = await cenario(session)
    item_a = await make_price_item(session, clinic=clinic_a, name="Só da clínica A")
    await api.post(
        f"/api/v1/hospitalizations/{hosp_a.id}/charges",
        json={"description": "Curativo", "unit_price_minor": 900},
        headers=bearer(personal_token(membership_a)),
    )
    headers_b = bearer(personal_token(membership_b))

    # A conta da internação alheia não existe para o outro tenant.
    alheio = await api.get(f"/api/v1/hospitalizations/{hosp_a.id}/charges", headers=headers_b)
    assert alheio.status_code == 404
    assert alheio.json()["error"]["code"] == "not_found"

    lancar = await api.post(
        f"/api/v1/hospitalizations/{hosp_a.id}/charges",
        json={"description": "Curativo", "unit_price_minor": 900},
        headers=headers_b,
    )
    assert lancar.status_code == 404

    # Nem o catálogo vaza.
    catalogo = await api.get("/api/v1/price-list", headers=headers_b)
    assert catalogo.json()["items"] == []
    assert (await api.get(f"/api/v1/price-list/{item_a.id}", headers=headers_b)).status_code == 404
    # Com o admin da B: nem quem PODE mexer em preço alcança o catálogo alheio.
    gestor_b = bearer(personal_token(await admin_de(session, clinic_b)))
    assert (
        await api.patch(
            f"/api/v1/price-list/{item_a.id}", json={"price_minor": 1}, headers=gestor_b
        )
    ).status_code == 404
    # E um item do catálogo alheio não pode ser usado num lançamento manual.
    _, membership_b2, hosp_b = await cenario(session)
    proibido = await api.post(
        f"/api/v1/hospitalizations/{hosp_b.id}/charges",
        json={"price_list_item_id": str(item_a.id), "quantity": "1"},
        headers=bearer(personal_token(membership_b2)),
    )
    assert proibido.status_code == 404


async def test_criar_item_de_preco_pela_api(api, session):
    clinic, _, _ = await cenario(session)
    headers = bearer(personal_token(await admin_de(session, clinic)))
    resp = await api.post(
        "/api/v1/price-list",
        json={
            "code": "DIP500",
            "name": "Dipirona 500 mg",
            "category": "medication",
            "unit": "por dose",
            "price_minor": 1800,
            "is_controlled": False,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    criado = resp.json()
    assert criado["is_active"] is True
    assert criado["price_minor"] == 1800

    persistido = await session.get(PriceListItem, uuid.UUID(criado["id"]))
    assert persistido.clinic_id == clinic.id


async def test_o_job_de_hora_em_hora_lanca_a_diaria(session, db_session_factory):
    """A diária é a maior linha da conta e nunca era lançada.

    `accrue_daily_rates` estava completo, idempotente e testado, e só os testes
    o chamavam. Nenhuma rota, nenhum worker, nenhum gancho de alta. O item
    marcado como diária, a área do box e a migração 0008 existiam para ninguém.
    """
    from datetime import UTC, datetime

    from app.models.charge_item import ChargeItem
    from app.workers.scheduler import hourly

    clinic = await make_clinic(session)
    kennel = await make_kennel(session, clinic=clinic, name="UTI 1", area="UTI")
    hosp = await make_hospitalization(session, clinic=clinic)
    hosp.kennel_id = kennel.id
    await make_price_item(
        session,
        clinic=clinic,
        name="Diária UTI",
        unit="dia",
        price_minor=28000,
        is_daily_rate=True,
        kennel_area="UTI",
    )

    agora = datetime.now(UTC)
    resultado = await hourly(db_session_factory, now=agora)
    assert resultado["daily_rates"] >= 1

    diarias = list(
        (
            await session.execute(
                sa.select(ChargeItem).where(
                    ChargeItem.hospitalization_id == hosp.id,
                    ChargeItem.source == "daily_rate",
                )
            )
        ).scalars()
    )
    assert len(diarias) == 1
    assert diarias[0].total_minor == 28000

    # Idempotente: rodar de novo na mesma hora não duplica a diária do dia.
    await hourly(db_session_factory, now=agora)
    de_novo = await session.scalar(
        sa.select(sa.func.count())
        .select_from(ChargeItem)
        .where(ChargeItem.hospitalization_id == hosp.id, ChargeItem.source == "daily_rate")
    )
    assert de_novo == 1
