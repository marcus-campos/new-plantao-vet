from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Task
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_kennel,
    make_membership,
    make_patient,
    make_user,
)
from tests.helpers import bearer, personal_token


async def _clinica_com_tres_estados(session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    kennel = await make_kennel(session, clinic=clinic, name="UTI 03")
    agora = datetime.now(UTC)

    cenarios = [
        ("Thor", agora + timedelta(hours=2), 60, "normal"),  # on_time
        ("Nina", agora - timedelta(minutes=10), 60, "normal"),  # due
        ("Mel", agora - timedelta(hours=3), 30, "critical"),  # overdue + crítica
    ]
    for nome, quando, tolerancia, criticidade in cenarios:
        patient = await make_patient(session, clinic=clinic, name=nome)
        hosp = await make_hospitalization(
            session, clinic=clinic, patient=patient, membership=membership
        )
        hosp.kennel_id = kennel.id
        session.add(
            Task(
                clinic_id=clinic.id,
                hospitalization_id=hosp.id,
                title=f"Tarefa de {nome}",
                category="medication",
                scheduled_for=quando,
                criticality=criticidade,
                tolerance_minutes=tolerancia,
                status="pending",
            )
        )
    await session.flush()
    return clinic, membership


async def test_board_agrupa_por_internacao_com_contadores(client, session):
    clinic, membership = await _clinica_com_tres_estados(session)
    resp = await client.get("/api/v1/board", headers=bearer(personal_token(membership)))
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["patients"] == 3
    assert body["totals"]["due"] == 1
    assert body["totals"]["overdue"] == 1

    por_paciente = {row["patient_name"]: row for row in body["rows"]}
    assert por_paciente["Mel"]["critical_overdue"] is True
    assert por_paciente["Thor"]["critical_overdue"] is False
    assert por_paciente["Nina"]["counters"]["due"] == 1
    assert por_paciente["Thor"]["next_task"]["title"] == "Tarefa de Thor"


async def test_board_e_fila_concordam_sobre_a_mesma_tarefa(client, session):
    """Mesma fonte de verdade: o board não pode dizer 'no prazo' e a ficha 'atrasada'."""
    clinic, membership = await _clinica_com_tres_estados(session)
    headers = bearer(personal_token(membership))
    agora = datetime.now(UTC)
    fila = (
        await client.get(
            "/api/v1/tasks",
            params={
                "from": (agora - timedelta(hours=6)).isoformat(),
                "to": (agora + timedelta(hours=6)).isoformat(),
            },
            headers=headers,
        )
    ).json()["items"]
    board = (await client.get("/api/v1/board", headers=headers)).json()

    estados_fila = {item["title"]: item["display_state"] for item in fila}
    for row in board["rows"]:
        proxima = row["next_task"]
        if proxima is not None:
            assert estados_fila[proxima["title"]] == proxima["display_state"]


async def test_auditoria_paginada_por_cursor(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="admin")
    headers = bearer(personal_token(membership))
    for indice in range(5):
        await client.post(
            "/api/v1/owners",
            json={"name": f"Tutor {indice}", "phone_e164": "+5511999990000"},
            headers=headers,
        )

    vistos: list[int] = []
    cursor = None
    for _ in range(5):
        url = "/api/v1/audit?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(url, headers=headers)).json()
        vistos.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(vistos) == len(set(vistos))
    assert vistos == sorted(vistos, reverse=True)


async def test_auditoria_filtra_por_entidade(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    headers = bearer(personal_token(membership))
    criado = (
        await client.post(
            "/api/v1/owners",
            json={"name": "Marina", "phone_e164": "+5511999990000"},
            headers=headers,
        )
    ).json()

    page = (
        await client.get(
            f"/api/v1/audit?entity_type=owner&entity_id={criado['id']}", headers=headers
        )
    ).json()
    assert len(page["items"]) == 1
    assert page["items"][0]["action"] == "owner_created"


async def test_tecnico_nao_le_auditoria(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    resp = await client.get("/api/v1/audit", headers=bearer(personal_token(membership)))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_isolamento_de_tenant_no_board(client, session):
    clinic_a, membership_a = await _clinica_com_tres_estados(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    await make_hospitalization(session, clinic=clinic_b)
    await session.flush()

    body = (
        await client.get("/api/v1/board", headers=bearer(personal_token(membership_a)))
    ).json()
    assert body["totals"]["patients"] == 3


async def test_board_ordena_pelo_pior_primeiro(client, session):
    """O cartão vermelho não pode ser o último da lista.

    A ordenação era `ORDER BY Patient.name`: numa clínica com 12 internados, o
    paciente com a dose crítica atrasada aparecia onde a letra inicial mandasse.
    Quem abre o sistema tem de ler de cima para baixo e parar quando quiser.
    """
    clinic, membership = await _clinica_com_tres_estados(session)
    body = (
        await client.get("/api/v1/board", headers=bearer(personal_token(membership)))
    ).json()

    nomes = [row["patient_name"] for row in body["rows"]]
    assert nomes == ["Mel", "Nina", "Thor"], "esperado pior → melhor, não alfabético"

    mel, nina, thor = body["rows"]
    assert mel["attention"]["reason"] == "critical_overdue"
    assert mel["attention"]["magnitude"] >= 179, "o atraso vem em minutos, não só o estado"
    assert mel["attention"]["task_title"] == "Tarefa de Mel"
    assert nina["attention"]["reason"] == "due"
    assert thor["attention"] is None, "quem está em dia não tem motivo de atenção"

    assert body["totals"]["attention"] == 2
    assert body["totals"]["now"] == 2, "vencida e dentro da janela são as duas 'agora'"
    assert body["totals"]["later"] == 1


async def test_board_conta_feitas_de_verdade(client, session):
    """"N de M feitas" contava PENDENTES: dizia "1 de 1 feita" com zero feitas."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    patient = await make_patient(session, clinic=clinic, name="Bob")
    hosp = await make_hospitalization(
        session, clinic=clinic, patient=patient, membership=membership
    )
    # Horas fixas DENTRO do dia da clínica. Com offsets a partir de `agora`,
    # uma suíte rodando perto da meia-noite jogava a tarefa futura para o dia
    # seguinte e o "previsto hoje" mudava conforme a hora da execução.
    tz = ZoneInfo(clinic.timezone)
    hoje = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
    for hora, status in ((10, "done"), (11, "not_done"), (13, "pending")):
        quando = hoje.replace(hour=hora).astimezone(UTC)
        session.add(
            Task(
                clinic_id=clinic.id,
                hospitalization_id=hosp.id,
                title="Dose",
                category="medication",
                scheduled_for=quando,
                criticality="normal",
                tolerance_minutes=60,
                status=status,
            )
        )
    await session.flush()

    linha = (
        await client.get("/api/v1/board", headers=bearer(personal_token(membership)))
    ).json()["rows"][0]
    assert linha["counters"]["done_today"] == 1, "só a executada conta como feita"
    assert linha["counters"]["planned_today"] == 3, "e as três estavam previstas hoje"
    # O estado das pendentes (no prazo / vencendo / atrasada) é derivado do
    # relógio real e tem teste próprio; misturá-lo aqui faria este resultado
    # depender da hora em que a suíte roda.


async def test_board_traz_o_fuso_da_clinica_e_o_relogio_do_servidor(client, session):
    """Sem os dois, o cliente formata no relógio do aparelho.

    Todo `scheduled_for` é calculado no fuso da clínica; um quiosque em UTC
    mostrava a dose das 10h como 13h, sem nenhum aviso.
    """
    clinic = await make_clinic(session)
    clinic.timezone = "America/Sao_Paulo"
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    await session.flush()

    body = (
        await client.get("/api/v1/board", headers=bearer(personal_token(membership)))
    ).json()
    assert body["timezone"] == "America/Sao_Paulo"
    assert body["now"] is not None


async def test_board_avisa_internado_sem_evolucao(client, session):
    """A obrigação do CFMV virada em produto: o servidor calculava e nenhuma
    tela mostrava. Agora entra na MESMA fila de atenção, e não num aviso à parte
    que o plantonista precisa lembrar de procurar."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    patient = await make_patient(session, clinic=clinic, name="Sem evolução")
    hosp = await make_hospitalization(
        session, clinic=clinic, patient=patient, membership=membership
    )
    hosp.admitted_at = datetime.now(UTC) - timedelta(hours=30)
    await session.flush()

    linha = (
        await client.get("/api/v1/board", headers=bearer(personal_token(membership)))
    ).json()["rows"][0]
    assert linha["attention"]["reason"] == "no_progress_note"
    assert linha["attention"]["magnitude"] >= 29, "a magnitude aqui é em HORAS"


async def test_board_mostra_o_turno_em_andamento(client, session):
    from app.models.shift import Shift

    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    agora = datetime.now(UTC)
    session.add(
        Shift(
            clinic_id=clinic.id,
            membership_id=membership.id,
            name="Noturno",
            starts_at=agora - timedelta(hours=1),
            ends_at=agora + timedelta(hours=11),
            is_vet_responsible=True,
        )
    )
    await session.flush()

    body = (
        await client.get("/api/v1/board", headers=bearer(personal_token(membership)))
    ).json()
    assert len(body["shifts"]) == 1
    turno = body["shifts"][0]
    assert turno["name"] == "Noturno"
    assert turno["is_mine"] is True, "o painel precisa saber se o plantão é de quem olha"
    assert turno["member_name"] == user.name
