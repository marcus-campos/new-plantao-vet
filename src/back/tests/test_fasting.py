"""Jejum bloqueando nutrição: avisando, nunca barrando.

O contrato aqui é o mesmo dos guardrails de PRN, e é deliberado: 409 com código
e params, e a MESMA requisição com `override=true` passa e fica auditada. A
pesquisa (§4) é explícita: fricção sem valor clínico percebido produz
workaround, e o sistema passa a mentir. O codebase já escolheu esse padrão uma
vez; um bloqueio duro aqui seria a segunda regra do jogo.
"""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import AuditEntry
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_task,
    make_user,
)
from tests.helpers import bearer, personal_token


async def _cenario(session):
    clinic = await make_clinic(session)
    vet = await make_membership(
        session,
        clinic=clinic,
        user=await make_user(session),
        role="vet",
        license_number="12345",
        license_authority="CRMV-SP",
    )
    tech = await make_membership(
        session, clinic=clinic, user=await make_user(session), role="tech"
    )
    hosp = await make_hospitalization(session, clinic=clinic, membership=vet)
    return clinic, vet, tech, hosp


async def _ultima_entrada(session, action: str) -> AuditEntry | None:
    return (
        (
            await session.execute(
                sa.select(AuditEntry)
                .where(AuditEntry.action == action)
                .order_by(AuditEntry.id.desc())
            )
        )
        .scalars()
        .first()
    )


# ---- o estado de jejum ----------------------------------------------------


async def test_iniciar_jejum_e_auditado_com_antes_e_depois(client, session):
    clinic, vet, tech, hosp = await _cenario(session)
    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/fasting",
        json={"reason": "pré-anestésico"},
        headers=bearer(personal_token(vet)),
    )
    assert resp.status_code == 200
    assert resp.json()["fasting_since"] is not None
    assert resp.json()["fasting_reason"] == "pré-anestésico"

    entry = await _ultima_entrada(session, "fasting_started")
    assert entry is not None
    # Jejum é decisão clínica: sem o "antes", "sem rasuras" não se sustenta.
    assert entry.payload["before"]["fasting_since"] is None
    assert entry.payload["after"]["fasting_since"] is not None
    assert entry.actor_license == "12345"


async def test_jejum_aparece_no_cabecalho_da_ficha(client, session):
    clinic, vet, tech, hosp = await _cenario(session)
    inicio = datetime.now(UTC) - timedelta(hours=3)
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/fasting",
        json={"since": inicio.isoformat(), "reason": "vômito"},
        headers=bearer(personal_token(vet)),
    )
    ficha = await client.get(
        f"/api/v1/hospitalizations/{hosp.id}", headers=bearer(personal_token(tech))
    )
    assert ficha.json()["hospitalization"]["fasting_since"] is not None
    assert ficha.json()["hospitalization"]["fasting_reason"] == "vômito"


async def test_jejum_com_inicio_no_futuro_e_recusado(client, session):
    clinic, vet, tech, hosp = await _cenario(session)
    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/fasting",
        json={"since": (datetime.now(UTC) + timedelta(hours=2)).isoformat()},
        headers=bearer(personal_token(vet)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["params"]["field"] == "since"


async def test_tecnico_nao_coloca_paciente_em_jejum(client, session):
    clinic, vet, tech, hosp = await _cenario(session)
    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/fasting",
        json={},
        headers=bearer(personal_token(tech)),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_encerrar_jejum_libera_a_alimentacao(client, session):
    clinic, vet, tech, hosp = await _cenario(session)
    headers = bearer(personal_token(vet))
    await client.post(f"/api/v1/hospitalizations/{hosp.id}/fasting", json={}, headers=headers)
    resp = await client.delete(f"/api/v1/hospitalizations/{hosp.id}/fasting", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["fasting_since"] is None
    assert await _ultima_entrada(session, "fasting_ended") is not None


async def test_encerrar_jejum_que_nao_existia_nao_inventa_rastro(client, session):
    clinic, vet, tech, hosp = await _cenario(session)
    resp = await client.delete(
        f"/api/v1/hospitalizations/{hosp.id}/fasting", headers=bearer(personal_token(vet))
    )
    assert resp.status_code == 200
    # Gravar "jejum encerrado" sem jejum seria registrar ato que não aconteceu.
    assert await _ultima_entrada(session, "fasting_ended") is None


# ---- o efeito na fila do plantão ------------------------------------------


async def test_alimentar_em_jejum_avisa_e_passa_com_override(client, session):
    clinic, vet, tech, hosp = await _cenario(session)
    inicio = datetime.now(UTC) - timedelta(hours=2)
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/fasting",
        json={"since": inicio.isoformat(), "reason": "cirurgia às 8h"},
        headers=bearer(personal_token(vet)),
    )
    task = await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        title="Alimentação úmida",
        category="nutrition",
        price_minor=None,
    )
    headers = bearer(personal_token(tech))

    avisado = await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    assert avisado.status_code == 409
    assert avisado.json()["error"]["code"] == "fasting_active"
    assert avisado.json()["error"]["params"]["reason"] == "cirurgia às 8h"
    assert avisado.json()["error"]["params"]["since"] is not None

    # A MESMA requisição com override passa, e a tarefa continuava pendente,
    # ou seja: o aviso não consumiu a execução.
    com_override = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"override": True}, headers=headers
    )
    assert com_override.status_code == 200
    assert com_override.json()["status"] == "done"

    entry = await _ultima_entrada(session, "task_executed")
    assert entry.payload["extra"]["override"] is True
    assert entry.payload["extra"]["fasting"]["reason"] == "cirurgia às 8h"


async def test_medicacao_durante_o_jejum_segue_normal(client, session):
    clinic, vet, tech, hosp = await _cenario(session)
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/fasting",
        json={"reason": "pré-anestésico"},
        headers=bearer(personal_token(vet)),
    )
    task = await make_task(
        session, clinic=clinic, hospitalization=hosp, title="Dipirona", category="medication"
    )
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={}, headers=bearer(personal_token(tech))
    )
    # Jejum é de comida. Suspender a analgesia junto seria dano.
    assert resp.status_code == 200
    entry = await _ultima_entrada(session, "task_executed")
    assert entry.payload["extra"]["fasting"] is None


async def test_alimentacao_sem_jejum_nao_pede_override(client, session):
    clinic, vet, tech, hosp = await _cenario(session)
    task = await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        title="Alimentação úmida",
        category="nutrition",
        price_minor=None,
    )
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={}, headers=bearer(personal_token(tech))
    )
    assert resp.status_code == 200


async def test_nao_realizada_por_jejum_continua_livre(client, session):
    """O caminho honesto do técnico não pode ser o mais difícil: registrar que a
    refeição não foi dada POR CAUSA do jejum é exatamente o que se quer."""
    clinic, vet, tech, hosp = await _cenario(session)
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/fasting", json={}, headers=bearer(personal_token(vet))
    )
    task = await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        title="Alimentação úmida",
        category="nutrition",
        price_minor=None,
    )
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/not-done",
        json={"reason": "fasting"},
        headers=bearer(personal_token(tech)),
    )
    assert resp.status_code == 200
    assert resp.json()["outcome_reason"] == "fasting"
