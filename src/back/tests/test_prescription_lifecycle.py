import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import Prescription, Task
from app.workers.scheduler import extend_scheduling_window
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_prescription,
    make_task,
    make_user,
)
from tests.helpers import bearer, personal_token


async def _vet(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    return clinic, membership


async def test_suspender_preserva_o_passado_e_cancela_o_futuro(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    created = (
        await client.post(
            f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
            json={
                "kind": "recurring",
                "category": "medication",
                "name": "Dipirona",
                "frequency_minutes": 480,
                "duration_hours": 72,
                "criticality": "normal",
                "details": {"drug": "dipirona"},
            },
            headers=token,
        )
    ).json()

    tarefas = list(
        (
            await session.execute(
                sa.select(Task)
                .where(Task.prescription_id == uuid.UUID(created["id"]))
                .order_by(Task.scheduled_for)
            )
        ).scalars()
    )
    for tarefa in tarefas[:3]:
        tarefa.status = "done"
        tarefa.executed_at = datetime.now(UTC)
    await session.flush()

    resp = await client.post(f"/api/v1/prescriptions/{created['id']}/suspend", headers=token)
    assert resp.status_code == 200
    assert resp.json()["suspended_at"] is not None

    depois = list(
        (
            await session.execute(
                sa.select(Task)
                .where(Task.prescription_id == uuid.UUID(created["id"]))
                .order_by(Task.scheduled_for)
            )
        ).scalars()
    )
    assert [t.status for t in depois[:3]] == ["done", "done", "done"]
    assert all(t.status == "cancelled" for t in depois[3:])


async def test_job_nao_ressuscita_tarefa_suspensa(client, session, db_session_factory):
    clinic, membership = await _vet(session)
    prescription = await make_prescription(session, clinic=clinic, starts_at=datetime.now(UTC))
    token = bearer(personal_token(membership))
    await client.post(f"/api/v1/prescriptions/{prescription.id}/suspend", headers=token)
    await session.flush()

    criadas = await extend_scheduling_window(db_session_factory, now=datetime.now(UTC))
    assert criadas == 0
    pendentes = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Task)
        .where(Task.prescription_id == prescription.id, Task.status == "pending")
    )
    assert pendentes == 0


async def test_ajustar_taxa_de_fluido_mantem_historico(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    original = (
        await client.post(
            f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
            json={
                "kind": "continuous",
                "category": "fluids",
                "name": "Ringer Lactato",
                "frequency_minutes": 120,
                "criticality": "normal",
                "details": {"rate_ml_h": 60},
            },
            headers=token,
        )
    ).json()

    ajustada = await client.post(
        f"/api/v1/prescriptions/{original['id']}/adjust",
        json={"details": {"rate_ml_h": 40}},
        headers=token,
    )
    assert ajustada.status_code == 201
    nova = ajustada.json()
    assert nova["replaces_prescription_id"] == original["id"]
    assert nova["details"]["rate_ml_h"] == 40
    assert nova["suspended_at"] is None

    antiga = await session.get(Prescription, uuid.UUID(original["id"]))
    await session.refresh(antiga)
    assert antiga.suspended_at is not None

    futuras_antigas = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Task)
        .where(Task.prescription_id == antiga.id, Task.status == "pending")
    )
    assert futuras_antigas == 0
    futuras_novas = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Task)
        .where(Task.prescription_id == uuid.UUID(nova["id"]), Task.status == "pending")
    )
    assert futuras_novas > 0


async def test_alta_com_pendencias_exige_confirmacao(client, session):
    """Pendência AQUI é dose que já venceu, não dose de amanhã.

    Dar alta cancela as futuras: é o que a alta significa. Contá-las faria a
    confirmação aparecer em toda alta, e confirmação que aparece sempre é
    confirmação que ninguém lê."""
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        title="Dose que ninguém deu",
        scheduled_for=datetime.now(UTC) - timedelta(hours=3),
    )
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Dipirona",
            "frequency_minutes": 480,
            "criticality": "normal",
            "details": {},
        },
        headers=token,
    )

    recusa = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        json={"outcome": "discharged"},
        headers=token,
    )
    assert recusa.status_code == 409
    assert recusa.json()["error"]["code"] == "pending_tasks_confirmation_required"
    assert recusa.json()["error"]["params"]["pending"] > 0

    ok = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        json={"outcome": "discharged", "confirm_pending_tasks": True},
        headers=token,
    )
    assert ok.status_code == 200
    restantes = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Task)
        .where(Task.hospitalization_id == hosp.id, Task.status == "pending")
    )
    assert restantes == 0
