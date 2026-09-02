import asyncio
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import Task
from app.services.tasks import TaskService
from app.workers.scheduler import extend_scheduling_window
from tests.factories import make_clinic, make_hospitalization, make_prescription


async def _count(session, prescription_id) -> int:
    return await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(Task.prescription_id == prescription_id)
    )


async def test_materialize_grava_a_janela(session):
    clinic = await make_clinic(session)
    prescription = await make_prescription(
        session, clinic=clinic, starts_at=datetime.now(UTC), frequency_minutes=480
    )
    criadas = await TaskService.materialize(
        session,
        prescription=prescription,
        clinic=clinic,
        until=datetime.now(UTC) + timedelta(hours=48),
    )
    assert criadas >= 5
    assert await _count(session, prescription.id) == criadas


async def test_rodar_duas_vezes_nao_duplica(session):
    clinic = await make_clinic(session)
    prescription = await make_prescription(session, clinic=clinic, starts_at=datetime.now(UTC))
    until = datetime.now(UTC) + timedelta(hours=48)

    primeira = await TaskService.materialize(
        session, prescription=prescription, clinic=clinic, until=until
    )
    segunda = await TaskService.materialize(
        session, prescription=prescription, clinic=clinic, until=until
    )
    assert segunda == 0
    assert await _count(session, prescription.id) == primeira


async def test_job_estende_a_janela_sem_duplicar(session, db_session_factory):
    clinic = await make_clinic(session)
    await make_prescription(session, clinic=clinic, starts_at=datetime.now(UTC))
    await session.flush()

    primeira = await extend_scheduling_window(db_session_factory, now=datetime.now(UTC))
    segunda = await extend_scheduling_window(db_session_factory, now=datetime.now(UTC))
    assert primeira > 0
    assert segunda == 0


async def test_job_ignora_prescricao_suspensa(session, db_session_factory):
    clinic = await make_clinic(session)
    await make_prescription(
        session, clinic=clinic, starts_at=datetime.now(UTC), suspended_at=datetime.now(UTC)
    )
    await session.flush()
    assert await extend_scheduling_window(db_session_factory, now=datetime.now(UTC)) == 0


async def test_job_ignora_internacao_encerrada(session, db_session_factory):
    clinic = await make_clinic(session)
    hosp = await make_hospitalization(session, clinic=clinic, status="discharged")
    await make_prescription(
        session, clinic=clinic, hospitalization=hosp, starts_at=datetime.now(UTC)
    )
    await session.flush()
    assert await extend_scheduling_window(db_session_factory, now=datetime.now(UTC)) == 0


async def test_prescrever_pela_api_ja_cria_as_tarefas(client, session):
    from tests.factories import make_membership, make_user
    from tests.helpers import bearer, personal_token

    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Dipirona",
            "frequency_minutes": 480,
            "criticality": "normal",
            "details": {"drug": "dipirona"},
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert await _count(session, resp.json()["id"]) > 0



async def test_o_lifespan_da_app_liga_o_agendador():
    """O job existia, completo e testado, e ninguém o chamava.

    A janela de tarefas era gravada só no momento da prescrição, então uma
    internação mais longa que ela esvaziava a ficha sozinha. Este teste guarda o
    fio que faltava: subir a aplicação tem de rodar o catch-up e deixar o job
    agendado; descer tem de encerrá-lo.
    """
    import app.main as main_module
    from app.core.config import settings
    from app.main import lifespan

    app = object()
    assert settings.scheduler_enabled is False, "a suíte roda com o job desligado"

    # Desligado (o modo da suíte): entra e sai sem tocar no agendador.
    chamadas: list[str] = []
    real_build = main_module.build_scheduler
    real_catch_up = main_module.hourly

    async def _catch_up_spy(*args, **kwargs):
        chamadas.append("catch_up")
        return 0

    agendadores: list = []

    def _build_spy(session_factory):
        scheduler = real_build(session_factory)
        agendadores.append(scheduler)
        return scheduler

    main_module.hourly = _catch_up_spy
    main_module.build_scheduler = _build_spy
    try:
        async with lifespan(app):
            pass
        assert chamadas == [] and agendadores == []

        settings.scheduler_enabled = True
        try:
            async with lifespan(app):
                assert chamadas == ["catch_up"], "a subida precisa alcançar o presente"
                assert agendadores, "o lifespan não construiu agendador nenhum"
                agendador = agendadores[0]
                assert agendador.running
                job = agendador.get_job("extend_scheduling_window")
                assert job is not None, "o job de estender a janela não foi agendado"
                assert job.trigger.interval == timedelta(hours=1)
            # `AsyncIOScheduler.shutdown` é `@run_in_event_loop`: o encerramento
            # de verdade acontece na próxima volta do loop.
            await asyncio.sleep(0.05)
            assert agendador.state == 0, "o agendador precisa ser encerrado no shutdown"
        finally:
            settings.scheduler_enabled = False
    finally:
        main_module.build_scheduler = real_build
        main_module.hourly = real_catch_up
