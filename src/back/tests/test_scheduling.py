import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.models import Clinic, Prescription
from app.services.scheduling import SchedulingService

SP = ZoneInfo("America/Sao_Paulo")
ANCHORS = {"1440": ["10:00"], "720": ["10:00", "22:00"], "480": ["10:00", "18:00", "02:00"]}


def _clinic(timezone: str = "America/Sao_Paulo", locale: str = "pt-BR") -> Clinic:
    return Clinic(
        name="Demo",
        slug="demo",
        timezone=timezone,
        locale=locale,
        anchors=dict(ANCHORS),
        default_prescriptions=[],
    )


def _prescription(**overrides) -> Prescription:
    values = {
        "kind": "recurring",
        "category": "medication",
        "name": "Dipirona",
        "details": {},
        "frequency_minutes": 480,
        "criticality": "normal",
        "tolerance_minutes": 60,
        "first_dose_now": False,
        "starts_at": datetime(2026, 9, 1, 17, 0, tzinfo=UTC),  # 14:00 em São Paulo
        "ends_at": None,
    }
    values.update(overrides)
    return Prescription(**values)


def _local_times(tasks, tz=SP):
    return [task.scheduled_for.astimezone(tz).strftime("%d/%m %H:%M") for task in tasks]


def test_ancoras_a_partir_das_14h():
    tasks = SchedulingService.generate(
        _prescription(), _clinic(), until=datetime(2026, 9, 3, 3, 0, tzinfo=UTC)
    )
    assert _local_times(tasks)[:4] == ["01/09 18:00", "02/09 02:00", "02/09 10:00", "02/09 18:00"]


def test_rollover_quando_as_ancoras_do_dia_acabam():
    # Admissão às 23:00: não existe âncora >= 23:00 nesse dia; a próxima é 02:00 do dia seguinte.
    tasks = SchedulingService.generate(
        _prescription(starts_at=datetime(2026, 9, 2, 2, 0, tzinfo=UTC)),  # 23:00 do dia 01 em SP
        _clinic(),
        until=datetime(2026, 9, 2, 22, 0, tzinfo=UTC),
    )
    assert _local_times(tasks)[0] == "02/09 02:00"


def test_frequencia_sem_ancora_usa_offset():
    # q30min: monitoramento de UTI. Não existe âncora para 30, então offset puro.
    tasks = SchedulingService.generate(
        _prescription(frequency_minutes=30),
        _clinic(),
        until=datetime(2026, 9, 1, 19, 0, tzinfo=UTC),
    )
    assert _local_times(tasks) == [
        "01/09 14:00",
        "01/09 14:30",
        "01/09 15:00",
        "01/09 15:30",
        "01/09 16:00",
    ]


def test_primeira_dose_agora_suprime_a_ancora_proxima():
    # 14:00 + q8h com âncoras 10/18/02: a de 18:00 fica a 4h < (480 - 60)min → suprimida.
    tasks = SchedulingService.generate(
        _prescription(first_dose_now=True),
        _clinic(),
        until=datetime(2026, 9, 2, 14, 0, tzinfo=UTC),
    )
    assert _local_times(tasks)[:3] == ["01/09 14:00", "02/09 02:00", "02/09 10:00"]


def test_primeira_dose_agora_sem_supressao_quando_a_ancora_esta_longe():
    # 10:30 local: a âncora das 18:00 está a 7h30 >= (480-60)min → NÃO é suprimida.
    tasks = SchedulingService.generate(
        _prescription(first_dose_now=True, starts_at=datetime(2026, 9, 1, 13, 30, tzinfo=UTC)),
        _clinic(),
        until=datetime(2026, 9, 2, 4, 0, tzinfo=UTC),
    )
    assert _local_times(tasks)[:2] == ["01/09 10:30", "01/09 18:00"]


def test_ends_at_corta_o_horizonte():
    tasks = SchedulingService.generate(
        _prescription(ends_at=datetime(2026, 9, 2, 3, 0, tzinfo=UTC)),  # 02/09 00:00 em SP
        _clinic(),
        until=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
    )
    assert _local_times(tasks) == ["01/09 18:00"]


def test_until_antes_do_inicio_devolve_vazio():
    tasks = SchedulingService.generate(
        _prescription(), _clinic(), until=datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    )
    assert tasks == []


def test_prn_nao_gera_nada():
    tasks = SchedulingService.generate(
        _prescription(kind="prn", frequency_minutes=None),
        _clinic(),
        until=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
    )
    assert tasks == []


def test_continua_gera_tarefa_de_checagem_traduzida():
    tasks = SchedulingService.generate(
        _prescription(
            kind="continuous",
            name="Ringer Lactato",
            frequency_minutes=120,
            details={"rate_ml_h": 60},
        ),
        _clinic(),
        until=datetime(2026, 9, 1, 21, 0, tzinfo=UTC),
    )
    assert tasks[0].title == "Checagem: Ringer Lactato"
    assert tasks[0].category == "medication"


def test_continua_em_ingles():
    tasks = SchedulingService.generate(
        _prescription(
            kind="continuous",
            name="Lactated Ringer",
            frequency_minutes=120,
            details={"rate_ml_h": 60},
        ),
        _clinic(locale="en"),
        until=datetime(2026, 9, 1, 21, 0, tzinfo=UTC),
    )
    assert tasks[0].title == "Check: Lactated Ringer"


def test_horario_de_verao_nao_estoura_nem_duplica():
    # America/Santiago adianta o relógio em 06/09/2026: 00:00 não existe nesse dia.
    clinic = _clinic(timezone="America/Santiago")
    clinic.anchors = {"1440": ["00:00"]}
    tasks = SchedulingService.generate(
        _prescription(frequency_minutes=1440, starts_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC)),
        clinic,
        until=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    )
    assert len(tasks) == len({task.scheduled_for for task in tasks})
    assert all(task.scheduled_for.tzinfo is not None for task in tasks)
    assert [t.scheduled_for for t in tasks] == sorted(t.scheduled_for for t in tasks)


def test_ordem_e_sempre_crescente():
    tasks = SchedulingService.generate(
        _prescription(), _clinic(), until=datetime(2026, 9, 5, 0, 0, tzinfo=UTC)
    )
    horarios = [task.scheduled_for for task in tasks]
    assert horarios == sorted(horarios)


async def test_preview_usa_as_ancoras_da_clinica(client, session):
    """O cliente tinha a SEGUNDA cópia do aprazamento, com as âncoras cravadas
    no código, e a tela de configurações deixava a clínica editar as dela.
    O preview prometia horários que o servidor não ia criar."""
    from tests.factories import make_clinic, make_membership, make_user
    from tests.helpers import bearer, personal_token

    clinic = await make_clinic(session)
    clinic.timezone = "UTC"
    clinic.anchors = {"480": ["06:00", "14:00", "22:00"]}
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    await session.flush()

    resp = await client.post(
        "/api/v1/prescriptions/preview",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Dipirona",
            "frequency_minutes": 480,
            "criticality": "normal",
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["anchors"] == ["06:00", "14:00", "22:00"]
    assert corpo["tolerance_minutes"] == 60
    horas = {item[11:16] for item in corpo["times"]}
    assert horas <= {"06:00", "14:00", "22:00"}, "o preview tem de seguir a âncora da clínica"
    assert horas, "e tem de devolver algum horário"


async def test_preview_nao_grava_nada(client, session):
    import sqlalchemy as sa

    from app.models import Prescription, Task
    from tests.factories import make_clinic, make_membership, make_user
    from tests.helpers import bearer, personal_token

    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    await session.flush()

    antes = await session.scalar(sa.select(sa.func.count()).select_from(Prescription))
    tarefas_antes = await session.scalar(sa.select(sa.func.count()).select_from(Task))
    await client.post(
        "/api/v1/prescriptions/preview",
        json={
            "kind": "recurring",
            "category": "medication",
            "name": "Dipirona",
            "frequency_minutes": 480,
            "criticality": "critical",
        },
        headers=bearer(personal_token(membership)),
    )
    assert await session.scalar(sa.select(sa.func.count()).select_from(Prescription)) == antes
    assert await session.scalar(sa.select(sa.func.count()).select_from(Task)) == tarefas_antes


def test_ancora_da_prescricao_vence_a_da_clinica():
    """As cerimônias do dia nasciam com a hora certa e caíam na hora errada.

    `details["anchor"]` era gravado (16:00 para o contato com o tutor, 08:00
    para a evolução, as horas da rotina real do HV-UFMS) e nunca lido. As duas
    caíam em `clinic.anchors["1440"]` = 10:00, no MESMO minuto, enquanto a tela
    de admissão anunciava "todo dia às 16:00". O sistema prometia uma hora e
    agendava outra.
    """
    clinic = Clinic(
        name="Demo",
        slug="demo",
        timezone="UTC",
        locale="pt-BR",
        anchors={"1440": ["10:00"]},
    )
    prescription = Prescription(
        clinic_id=uuid.uuid4(),
        hospitalization_id=uuid.uuid4(),
        kind="recurring",
        category="care",
        name="Contato com o tutor",
        details={"anchor": "16:00"},
        frequency_minutes=1440,
        criticality="normal",
        tolerance_minutes=120,
        starts_at=datetime(2026, 8, 31, 9, 0, tzinfo=UTC),
    )
    tasks = SchedulingService.generate(
        prescription, clinic, datetime(2026, 9, 2, 0, 0, tzinfo=UTC)
    )
    assert [task.scheduled_for.hour for task in tasks] == [16, 16]

    # Sem âncora própria, segue a da clínica: nada muda para o resto.
    prescription.details = {}
    padrao = SchedulingService.generate(
        prescription, clinic, datetime(2026, 9, 2, 0, 0, tzinfo=UTC)
    )
    assert [task.scheduled_for.hour for task in padrao] == [10, 10]
