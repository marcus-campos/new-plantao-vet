from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Task
from app.services.tasks import TaskService
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_prescription,
    make_task,
    make_user,
)
from tests.helpers import bearer, personal_token

SP = ZoneInfo("America/Sao_Paulo")


def _task(**overrides) -> Task:
    values = {
        "title": "Dipirona",
        "category": "medication",
        "criticality": "normal",
        "tolerance_minutes": 60,
        "status": "pending",
        "scheduled_for": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return Task(**values)


def test_display_state_antes_do_horario():
    assert TaskService.display_state(_task(), datetime(2026, 9, 1, 11, 59, tzinfo=UTC)) == "on_time"


def test_display_state_exatamente_no_horario():
    assert TaskService.display_state(_task(), datetime(2026, 9, 1, 12, 0, tzinfo=UTC)) == "due"


def test_display_state_no_limite_da_tolerancia():
    assert TaskService.display_state(_task(), datetime(2026, 9, 1, 13, 0, tzinfo=UTC)) == "due"


def test_display_state_um_minuto_depois_da_tolerancia():
    assert TaskService.display_state(_task(), datetime(2026, 9, 1, 13, 1, tzinfo=UTC)) == "overdue"


def test_display_state_critica_tem_janela_menor():
    task = _task(criticality="critical", tolerance_minutes=30)
    assert TaskService.display_state(task, datetime(2026, 9, 1, 12, 30, tzinfo=UTC)) == "due"
    assert TaskService.display_state(task, datetime(2026, 9, 1, 12, 31, tzinfo=UTC)) == "overdue"


def test_display_state_de_status_finalizado_e_o_proprio_status():
    agora = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
    for status in ("done", "partial", "not_done", "cancelled"):
        assert TaskService.display_state(_task(status=status), agora) == status


async def test_fila_por_janela_explicita(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    hosp = await make_hospitalization(session, clinic=clinic)
    inicio = datetime.now(UTC)
    prescription = await make_prescription(
        session, clinic=clinic, hospitalization=hosp, starts_at=inicio, frequency_minutes=60
    )
    await TaskService.materialize(
        session, prescription=prescription, clinic=clinic, until=inicio + timedelta(hours=24)
    )
    await session.flush()

    resp = await client.get(
        "/api/v1/tasks",
        params={"from": inicio.isoformat(), "to": (inicio + timedelta(hours=3)).isoformat()},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    itens = resp.json()["items"]
    assert 3 <= len(itens) <= 4
    assert itens == sorted(itens, key=lambda item: item["scheduled_for"])
    assert itens[0]["display_state"] in ("on_time", "due")


async def test_plantao_noturno_ve_as_tarefas_da_madrugada(client, session):
    """Às 22h a janela default (12h) precisa alcançar as âncoras de 02h e 04h."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    hosp = await make_hospitalization(session, clinic=clinic)

    agora_local = datetime.now(SP).replace(hour=22, minute=0, second=0, microsecond=0)
    agora = agora_local.astimezone(UTC)
    for hora in (2, 4):
        alvo = (agora_local + timedelta(days=1)).replace(hour=hora, minute=0)
        session.add(
            Task(
                clinic_id=clinic.id,
                hospitalization_id=hosp.id,
                title=f"Madrugada {hora}h",
                category="monitoring",
                scheduled_for=alvo.astimezone(UTC),
                criticality="normal",
                tolerance_minutes=60,
                status="pending",
            )
        )
    await session.flush()

    resp = await client.get(
        "/api/v1/tasks",
        params={"from": agora.isoformat(), "to": (agora + timedelta(hours=12)).isoformat()},
        headers=bearer(personal_token(membership)),
    )
    titulos = [item["title"] for item in resp.json()["items"]]
    assert "Madrugada 2h" in titulos
    assert "Madrugada 4h" in titulos


async def test_janela_default_sao_12_horas(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    hosp = await make_hospitalization(session, clinic=clinic)
    agora = datetime.now(UTC)
    for titulo, delta in (("Dentro", timedelta(hours=6)), ("Fora", timedelta(hours=30))):
        session.add(
            Task(
                clinic_id=clinic.id,
                hospitalization_id=hosp.id,
                title=titulo,
                category="care",
                scheduled_for=agora + delta,
                criticality="normal",
                tolerance_minutes=60,
                status="pending",
            )
        )
    await session.flush()

    itens = (
        await client.get("/api/v1/tasks", headers=bearer(personal_token(membership)))
    ).json()["items"]
    titulos = [item["title"] for item in itens]
    assert "Dentro" in titulos
    assert "Fora" not in titulos


async def test_detalhe_da_internacao_traz_tarefas(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
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
    detail = (await client.get(f"/api/v1/hospitalizations/{hosp.id}", headers=token)).json()
    assert len(detail["tasks"]) > 0
    assert "display_state" in detail["tasks"][0]


async def test_isolamento_de_tenant_na_fila(client, session):
    clinic_a = await make_clinic(session, slug="a")
    clinic_b = await make_clinic(session, slug="b")
    user = await make_user(session)
    membership_a = await make_membership(session, clinic=clinic_a, user=user, role="tech")
    hosp_b = await make_hospitalization(session, clinic=clinic_b)
    session.add(
        Task(
            clinic_id=clinic_b.id,
            hospitalization_id=hosp_b.id,
            title="Da clínica B",
            category="care",
            scheduled_for=datetime.now(UTC) + timedelta(hours=1),
            criticality="normal",
            tolerance_minutes=60,
            status="pending",
        )
    )
    await session.flush()

    itens = (
        await client.get("/api/v1/tasks", headers=bearer(personal_token(membership_a)))
    ).json()["items"]
    assert itens == []


async def test_dose_vencida_ha_dias_continua_na_fila(client, session):
    """Atrasada NUNCA sai da fila, por mais velha que seja.

    O painel conta toda pendente vencida, sem limite inferior. Se a fila
    esconder a dose de anteontem, a pessoa dá baixa em tudo que vê, o contador
    de atraso não se move e ela conclui que o sistema está quebrado – foi
    exatamente o que aconteceu."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    agora = datetime.now(UTC)
    antiga = await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        title="Dose esquecida de anteontem",
        scheduled_for=agora - timedelta(days=2),
    )
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        title="Dose de agora",
        scheduled_for=agora,
    )
    await session.flush()
    headers = bearer(personal_token(membership))

    fila = (await client.get("/api/v1/tasks", headers=headers)).json()["items"]
    titulos = [item["title"] for item in fila]
    assert "Dose esquecida de anteontem" in titulos, "a fila escondeu uma dose vencida"

    # E o painel tem de contar exatamente as mesmas: uma fonte de verdade só.
    painel = (await client.get("/api/v1/board", headers=headers)).json()
    atrasadas_no_painel = painel["rows"][0]["counters"]["overdue"]
    atrasadas_na_fila = sum(1 for item in fila if item["display_state"] == "overdue")
    # A dose de AGORA está dentro da tolerância: é "due", não atrasada.
    assert atrasadas_na_fila == 1
    assert atrasadas_no_painel == atrasadas_na_fila

    # Já uma janela pedida explicitamente é respeitada: quem pede um intervalo
    # está montando relatório, não tocando o plantão.
    recorte = (
        await client.get(
            "/api/v1/tasks",
            params={
                "from": (agora - timedelta(hours=1)).isoformat(),
                "to": (agora + timedelta(hours=1)).isoformat(),
            },
            headers=headers,
        )
    ).json()["items"]
    assert antiga.title not in [item["title"] for item in recorte]


async def test_ficha_da_internacao_mostra_a_dose_vencida_antiga(client, session):
    """A ficha usa a MESMA regra da fila: nada de segunda fonte de verdade.

    Foi por aqui que o problema apareceu na tela: a ficha cortava em 24h, o
    painel contava sem limite, e as duas telas discordavam."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    prescription = await make_prescription(session, clinic=clinic, hospitalization=hosp)
    agora = datetime.now(UTC)
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        prescription_id=prescription.id,
        title="Dose esquecida de anteontem",
        scheduled_for=agora - timedelta(days=2),
    )
    await session.flush()

    ficha = (
        await client.get(
            f"/api/v1/hospitalizations/{hosp.id}", headers=bearer(personal_token(membership))
        )
    ).json()
    titulos = [item["title"] for item in ficha["tasks"]]
    assert "Dose esquecida de anteontem" in titulos


async def test_fila_pagina_de_verdade_em_vez_de_truncar_calada(client, session):
    """`next_cursor` nulo com a lista cortada faz o app achar que viu tudo.

    Numa clínica de 25 leitos a fila passa de 50 tarefas com folga; sem cursor,
    o plantonista simplesmente não enxerga metade das doses do turno."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    agora = datetime.now(UTC)
    for minuto in range(12):
        await make_task(
            session,
            clinic=clinic,
            hospitalization=hosp,
            title=f"Dose {minuto:02d}",
            scheduled_for=agora + timedelta(minutes=minuto),
        )
    await session.flush()
    headers = bearer(personal_token(membership))

    vistas: list[str] = []
    cursor = None
    paginas = 0
    while True:
        params = {"limit": 5, **({"cursor": cursor} if cursor else {})}
        page = (await client.get("/api/v1/tasks", params=params, headers=headers)).json()
        vistas += [item["title"] for item in page["items"]]
        paginas += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert paginas < 10, "paginação não terminou"

    assert vistas == [f"Dose {n:02d}" for n in range(12)], "ordem ou completude quebrada"
    assert paginas == 3


async def test_cursor_corrompido_nao_derruba_a_fila(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    await session.flush()
    resp = await client.get(
        "/api/v1/tasks", params={"cursor": "lixo"}, headers=bearer(personal_token(membership))
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
