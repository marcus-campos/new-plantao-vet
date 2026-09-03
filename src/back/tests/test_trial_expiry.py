"""O dia 15: a escrita para, a leitura continua, e a alta sobrevive."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_owner,
    make_patient,
)
from tests.helpers import bearer, operator_token, personal_token, station_token

ONTEM = datetime.now(UTC) - timedelta(days=1)
DAQUI_UMA_SEMANA = datetime.now(UTC) + timedelta(days=7)


async def _clinica_vencida(session):
    return await make_clinic(
        session, subscription_status="trial", trial_ends_at=ONTEM, plan_tier=None
    )


@pytest.mark.asyncio
async def test_prescrever_com_teste_vencido_e_recusado(client, session):
    clinic = await _clinica_vencida(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    patient = await make_patient(session, clinic=clinic)
    hosp = await make_hospitalization(session, clinic=clinic, patient=patient, membership=vet)
    await session.flush()

    resposta = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        headers=bearer(personal_token(vet)),
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Dipirona 25 mg/kg IV",
            "frequency_minutes": 480,
            "criticality": "normal",
            "details": {"drug": "dipirona"},
        },
    )
    assert resposta.status_code == 403
    assert resposta.json()["error"]["code"] == "trial_expired"


@pytest.mark.asyncio
async def test_ler_a_ficha_com_teste_vencido_continua_funcionando(client, session):
    clinic = await _clinica_vencida(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await session.flush()

    resposta = await client.get("/api/v1/board", headers=bearer(personal_token(vet)))
    assert resposta.status_code == 200


@pytest.mark.asyncio
async def test_dar_alta_com_teste_vencido_continua_funcionando(client, session):
    # A exceção que importa: ninguém fica com paciente internado dentro de um
    # sistema congelado.
    clinic = await _clinica_vencida(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    patient = await make_patient(session, clinic=clinic)
    hosp = await make_hospitalization(session, clinic=clinic, patient=patient, membership=vet)
    await session.flush()

    resposta = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        headers=bearer(personal_token(vet)),
        json={"outcome": "discharged", "confirm_pending_tasks": True},
    )
    assert resposta.status_code == 200, resposta.text


@pytest.mark.asyncio
async def test_teste_vigente_nao_bloqueia_nada(client, session):
    clinic = await make_clinic(session, subscription_status="trial", trial_ends_at=DAQUI_UMA_SEMANA)
    vet = await make_membership(session, clinic=clinic, role="vet")
    owner = await make_owner(session, clinic=clinic)
    await session.flush()

    resposta = await client.post(
        "/api/v1/patients",
        headers=bearer(personal_token(vet)),
        json={"name": "Thor", "species": "dog", "owner_id": str(owner.id)},
    )
    assert resposta.status_code == 201, resposta.text


@pytest.mark.asyncio
async def test_me_anuncia_o_estado_e_encolhe_as_capacidades(client, session):
    clinic = await _clinica_vencida(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await session.flush()

    corpo = (await client.get("/api/v1/auth/me", headers=bearer(personal_token(vet)))).json()
    assert corpo["read_only"] is True
    assert "prescription.create" not in corpo["capabilities"]
    assert "record.read" in corpo["capabilities"]
    assert "hospitalization.discharge" in corpo["capabilities"]


@pytest.mark.asyncio
async def test_me_com_teste_vigente_traz_tudo(client, session):
    clinic = await make_clinic(session, subscription_status="trial", trial_ends_at=DAQUI_UMA_SEMANA)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await session.flush()

    corpo = (await client.get("/api/v1/auth/me", headers=bearer(personal_token(vet)))).json()
    assert corpo["read_only"] is False
    assert "prescription.create" in corpo["capabilities"]


@pytest.mark.asyncio
async def test_operator_encolhe_as_capacidades_com_teste_vencido(client, session):
    """`/auth/operator`, não `/auth/me`, é o que o tablet da estação lê
    (`useSession`: `station ? operator?.capabilities : me?.capabilities`).
    Sem o mesmo filtro aqui, o aparelho compartilhado seguia oferecendo
    prescrição depois do teste vencer — um botão que devolve 403 no toque."""
    clinic = await _clinica_vencida(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await session.flush()

    resposta = await client.get(
        "/api/v1/auth/operator",
        headers={**bearer(station_token(clinic)), "X-Operator-Token": operator_token(vet)},
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert "prescription.create" not in corpo["capabilities"]
    assert "record.read" in corpo["capabilities"]
    assert "hospitalization.discharge" in corpo["capabilities"]


@pytest.mark.asyncio
async def test_require_any_com_lista_mista_nao_libera_a_escrita(session, monkeypatch):
    """A capacidade que AUTORIZA é que manda, não a mais permissiva da lista.

    Com o `any()` antigo, bastava `hospitalization.discharge` aparecer na
    lista de capacidades da ROTA para o gate inteiro ser pulado — inclusive
    para quem só tinha a capacidade de escrita, porque `_ensure_writable`
    recebia a lista da rota, não a capacidade que de fato autorizou o ator.

    Hoje nenhum papel real tem `prescription.create` sem também ter
    `hospitalization.discharge` (os dois vêm juntos em `LICENSED_ONLY`), então
    para reproduzir o cenário este teste chama o `dependency()` de
    `require_any` diretamente — sem inventar rota nova em produção — e finge,
    via monkeypatch de `can`, um papel que só tem a capacidade de escrita."""
    import app.api.deps as deps_module
    from app.core.errors import AppError
    from app.permissions import HOSPITALIZATION_DISCHARGE, PRESCRIPTION_CREATE
    from app.services.audit import ActorInfo

    clinic = await _clinica_vencida(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await session.flush()

    actor = ActorInfo(
        membership_id=vet.id,
        name="Dra. Teste",
        license_number=None,
        license_authority=None,
        role="vet",
    )
    auth = deps_module.AuthContext(kind="personal", clinic_id=clinic.id, membership=vet)

    # Só a capacidade de ESCREVER autoriza; a de dar alta nunca autoriza aqui.
    monkeypatch.setattr(
        deps_module,
        "can",
        lambda role, capability: capability == PRESCRIPTION_CREATE,
    )

    dependency = deps_module.require_any(PRESCRIPTION_CREATE, HOSPITALIZATION_DISCHARGE)
    with pytest.raises(AppError) as exc:
        await dependency(actor=actor, auth=auth, session=session)
    assert exc.value.code == "trial_expired"
