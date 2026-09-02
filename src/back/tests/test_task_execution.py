import asyncio
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import AuditEntry, Task
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_prescription,
    make_user,
)
from tests.helpers import bearer, personal_token


async def _cenario(session, *, scheduled_for=None, tolerance_minutes=60, criticality="normal"):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(
        session,
        clinic=clinic,
        user=user,
        role="tech",
        license_number="9876",
        license_authority="CRMV-SP",
    )
    hosp = await make_hospitalization(session, clinic=clinic)
    task = Task(
        clinic_id=clinic.id,
        hospitalization_id=hosp.id,
        title="Dipirona",
        category="medication",
        scheduled_for=scheduled_for or datetime.now(UTC),
        criticality=criticality,
        tolerance_minutes=tolerance_minutes,
        status="pending",
        price_minor=1800,
    )
    session.add(task)
    await session.flush()
    return clinic, membership, hosp, task


async def test_executar_registra_autor_e_registro_profissional(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"values": {"note": "sem intercorrência"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    assert resp.json()["executed_at"] is not None

    entry = (
        (
            await session.execute(
                sa.select(AuditEntry)
                .where(AuditEntry.action == "task_executed")
                .order_by(AuditEntry.id.desc())
            )
        )
        .scalars()
        .first()
    )
    assert entry.actor_license == "9876"
    assert entry.actor_license_authority == "CRMV-SP"


async def test_executar_tarefa_ja_finalizada_e_409(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))
    await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    de_novo = await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    assert de_novo.status_code == 409
    assert de_novo.json()["error"]["code"] == "task_already_processed"


async def test_execucao_concorrente_uma_ganha_outra_recebe_409(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))

    primeira, segunda = await asyncio.gather(
        client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers),
        client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers),
    )
    codigos = sorted([primeira.status_code, segunda.status_code])
    assert codigos == [200, 409]
    perdedora = primeira if primeira.status_code == 409 else segunda
    assert perdedora.json()["error"]["code"] == "task_already_processed"


async def test_retroativo_exige_hora_real_do_procedimento(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))

    sem_hora = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"retroactive": True}, headers=headers
    )
    assert sem_hora.status_code == 422

    realizada_em = datetime.now(UTC) - timedelta(minutes=40)
    com_hora = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"retroactive": True, "performed_at": realizada_em.isoformat()},
        headers=headers,
    )
    assert com_hora.status_code == 200
    assert com_hora.json()["retroactive"] is True

    entry = (
        (
            await session.execute(
                sa.select(AuditEntry)
                .where(AuditEntry.action == "task_executed")
                .order_by(AuditEntry.id.desc())
            )
        )
        .scalars()
        .first()
    )
    # Os DOIS instantes ficam registrados: quando foi feito e quando foi apontado.
    assert entry.payload["extra"]["performed_at"] is not None
    assert entry.payload["extra"]["recorded_at"] is not None
    assert entry.payload["extra"]["performed_at"] != entry.payload["extra"]["recorded_at"]


async def test_execucao_precoce_exige_confirmacao(client, session):
    daqui_a_muito = datetime.now(UTC) + timedelta(hours=5)
    clinic, membership, hosp, task = await _cenario(session, scheduled_for=daqui_a_muito)
    headers = bearer(personal_token(membership))

    sem_confirmar = await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    assert sem_confirmar.status_code == 409
    assert sem_confirmar.json()["error"]["code"] == "early_confirmation_required"

    confirmando = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"confirm_early": True}, headers=headers
    )
    assert confirmando.status_code == 200
    assert confirmando.json()["early"] is True


async def test_parcial_exige_dose_administrada(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))

    sem_dose = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"partial": True}, headers=headers
    )
    assert sem_dose.status_code == 422

    com_dose = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"partial": True, "values": {"dose_given": "metade"}},
        headers=headers,
    )
    assert com_dose.status_code == 200
    assert com_dose.json()["status"] == "partial"


async def test_nao_realizada_com_motivo(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/not-done", json={"reason": "fasting"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_done"
    assert resp.json()["outcome_reason"] == "fasting"


async def test_motivo_outro_exige_detalhe(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))
    sem_detalhe = await client.post(
        f"/api/v1/tasks/{task.id}/not-done", json={"reason": "other"}, headers=headers
    )
    assert sem_detalhe.status_code == 422

    com_detalhe = await client.post(
        f"/api/v1/tasks/{task.id}/not-done",
        json={"reason": "other", "values": {"outcome_detail": "acesso venoso perdido"}},
        headers=headers,
    )
    assert com_detalhe.status_code == 200


async def test_prn_respeita_intervalo_minimo_com_override(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    prn = await make_prescription(
        session,
        clinic=clinic,
        hospitalization=hosp,
        kind="prn",
        frequency_minutes=None,
        name="Metadona",
        max_doses_24h=4,
        min_interval_minutes=240,
    )
    headers = bearer(personal_token(membership))

    primeira = await client.post(
        "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
    )
    assert primeira.status_code == 201

    cedo_demais = await client.post(
        "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
    )
    assert cedo_demais.status_code == 409
    assert cedo_demais.json()["error"]["code"] == "prn_guardrail"
    assert cedo_demais.json()["error"]["params"]["rule"] == "min_interval_minutes"

    # Aviso, nunca bloqueio duro: o vet decide e o override fica auditado.
    com_override = await client.post(
        "/api/v1/tasks/ad-hoc",
        json={"prescription_id": str(prn.id), "override": True},
        headers=headers,
    )
    assert com_override.status_code == 201
    entry = (
        (
            await session.execute(
                sa.select(AuditEntry)
                .where(AuditEntry.action == "task_executed")
                .order_by(AuditEntry.id.desc())
            )
        )
        .scalars()
        .first()
    )
    assert entry.payload["extra"]["override"] is True


async def test_prn_respeita_maximo_em_24h(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    prn = await make_prescription(
        session,
        clinic=clinic,
        hospitalization=hosp,
        kind="prn",
        frequency_minutes=None,
        name="Metadona",
        max_doses_24h=2,
        min_interval_minutes=None,
    )
    headers = bearer(personal_token(membership))

    for _ in range(2):
        assert (
            await client.post(
                "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
            )
        ).status_code == 201

    terceira = await client.post(
        "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
    )
    assert terceira.status_code == 409
    assert terceira.json()["error"]["params"]["rule"] == "max_doses_24h"


async def test_evento_avulso_com_titulo_livre(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    resp = await client.post(
        "/api/v1/tasks/ad-hoc",
        json={
            "hospitalization_id": str(hosp.id),
            "title": "Episódio de vômito",
            "category": "care",
            "values": {"note": "bilioso, pequeno volume"},
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "done"
    assert resp.json()["title"] == "Episódio de vômito"


async def test_modo_estacao_sem_operator_token_e_403(client, session):
    from tests.helpers import station_token

    clinic, membership, hosp, task = await _cenario(session)
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={}, headers=bearer(station_token(clinic))
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "operator_required"
