"""Leitura da tabela de preços: a única leitura sensível do sistema que não
tinha capacidade nenhuma. Qualquer membro autenticado, e qualquer estação sem
ninguém identificado pelo PIN, lia o catálogo inteiro.

O fechamento não pode usar só `price_list.manage`: quem prescreve consulta o
preço do item para preencher o valor da prescrição (`NewPrescription.tsx`), e
o `vet` não tem `price_list.manage`. É a mesma tensão que a posologia já
resolveu em `price_list.py` (`require_any(PRICE_LIST_MANAGE,
PRESCRIPTION_CREATE)`), só que aqui numa LEITURA: exigir operador identificado
via `require_any` obrigaria a digitar PIN só para OLHAR a tela.
"""

from typing import Any

from app.models.price_list_item import PriceListItem
from tests.factories import make_clinic, make_membership, make_user
from tests.helpers import bearer, operator_token, personal_token, station_token


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


async def _membro(session, clinic, role):
    user = await make_user(session)
    return await make_membership(session, clinic=clinic, user=user, role=role)


async def test_admin_le_a_tabela_de_precos(client, session):
    """O administrador curadoria a tabela: continua lendo, como sempre."""
    clinic = await make_clinic(session)
    admin = await _membro(session, clinic, "admin")
    item = await make_price_item(session, clinic=clinic)
    await session.flush()

    listagem = await client.get("/api/v1/price-list", headers=bearer(personal_token(admin)))
    assert listagem.status_code == 200
    assert [row["id"] for row in listagem.json()["items"]] == [str(item.id)]

    detalhe = await client.get(
        f"/api/v1/price-list/{item.id}", headers=bearer(personal_token(admin))
    )
    assert detalhe.status_code == 200


async def test_vet_le_a_tabela_de_precos(client, session):
    """O teste que impede a regressão: fechar com `price_list.manage` sozinho
    quebraria a prescrição, que lê o preço do item para preencher o valor."""
    clinic = await make_clinic(session)
    vet = await _membro(session, clinic, "vet")
    item = await make_price_item(session, clinic=clinic)
    await session.flush()

    listagem = await client.get("/api/v1/price-list", headers=bearer(personal_token(vet)))
    assert listagem.status_code == 200

    detalhe = await client.get(
        f"/api/v1/price-list/{item.id}", headers=bearer(personal_token(vet))
    )
    assert detalhe.status_code == 200


async def test_tecnico_nao_le_a_tabela_de_precos(client, session):
    """O técnico não administra a tabela, não prescreve e não lança conta: não
    tem motivo para ver o preço de cada item do catálogo."""
    clinic = await make_clinic(session)
    tech = await _membro(session, clinic, "tech")
    item = await make_price_item(session, clinic=clinic)
    await session.flush()

    listagem = await client.get("/api/v1/price-list", headers=bearer(personal_token(tech)))
    assert listagem.status_code == 403
    assert listagem.json()["error"]["code"] == "forbidden"

    detalhe = await client.get(
        f"/api/v1/price-list/{item.id}", headers=bearer(personal_token(tech))
    )
    assert detalhe.status_code == 403
    assert detalhe.json()["error"]["code"] == "forbidden"


async def test_estacao_sem_pin_nao_le_a_tabela_de_precos(client, session):
    """O ganho do fechamento: a estação sem ninguém identificado não lê mais
    o catálogo inteiro só por estar logada na clínica."""
    clinic = await make_clinic(session)
    item = await make_price_item(session, clinic=clinic)
    await session.flush()

    listagem = await client.get("/api/v1/price-list", headers=bearer(station_token(clinic)))
    assert listagem.status_code == 403
    assert listagem.json()["error"]["code"] == "operator_required"

    detalhe = await client.get(
        f"/api/v1/price-list/{item.id}", headers=bearer(station_token(clinic))
    )
    assert detalhe.status_code == 403
    assert detalhe.json()["error"]["code"] == "operator_required"


async def test_estacao_com_pin_de_vet_le_a_tabela_de_precos(client, session):
    """Com o PIN do vet identificado na estação, a leitura acontece: o modo
    estação existe para a equipe trabalhar num aparelho compartilhado."""
    clinic = await make_clinic(session)
    vet = await _membro(session, clinic, "vet")
    item = await make_price_item(session, clinic=clinic)
    await session.flush()

    resp = await client.get(
        "/api/v1/price-list",
        headers={**bearer(station_token(clinic)), "X-Operator-Token": operator_token(vet)},
    )
    assert resp.status_code == 200
    assert [row["id"] for row in resp.json()["items"]] == [str(item.id)]
