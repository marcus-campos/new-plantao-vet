"""Passagem de plantão: a feature-herói.

Os testes cobrem as duas garantias que a spec trata como inegociáveis:
o esqueleto é determinístico (conta certo, sem IA) e a falta de aprovação
NUNCA bloqueia nem esconde o boletim do plantão seguinte."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa

from app.api.deps import get_session
from app.api.routes import handover as handover_routes
from app.api.routes import shifts as shift_routes
from app.main import create_app
from app.models.audit import AuditEntry
from app.models.handover_ack import HandoverAck
from app.models.handover_report import HandoverReport
from app.models.shift import Shift
from app.models.shift_note import ShiftNote
from app.models.task import Task
from app.services.handover import HandoverService
from app.services.narrative import NarrativeService
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_prescription,
    make_user,
)
from tests.helpers import bearer, personal_token


@pytest.fixture
async def client(db_session):
    """App com os routers desta trilha montados à mão.

    `app/main.py` é do integrador (brief): o teste monta os routers localmente
    em vez de depender do registro já ter acontecido lá."""
    app = create_app()
    app.include_router(shift_routes.router)
    app.include_router(shift_routes.notes_router)
    app.include_router(handover_routes.router)

    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def make_shift(session, *, clinic, membership, **overrides) -> Shift:
    now = datetime.now(UTC)
    values = {
        "name": "Diurno",
        "starts_at": now - timedelta(hours=6),
        "ends_at": now + timedelta(hours=6),
        "is_vet_responsible": True,
    }
    values.update(overrides)
    shift = Shift(clinic_id=clinic.id, membership_id=membership.id, **values)
    session.add(shift)
    await session.flush()
    return shift


async def make_task(session, *, clinic, hospitalization, **overrides) -> Task:
    values = {
        "title": "Dipirona",
        "category": "medication",
        "criticality": "normal",
        "tolerance_minutes": 60,
        "status": "pending",
        "scheduled_for": datetime.now(UTC),
    }
    values.update(overrides)
    task = Task(clinic_id=clinic.id, hospitalization_id=hospitalization.id, **values)
    session.add(task)
    await session.flush()
    return task


async def make_shift_note(session, *, clinic, hospitalization, **overrides) -> ShiftNote:
    values = {
        "author_name": "Dra. Ana",
        "text": "Paciente vomitou às 3h.",
        "source": "typed",
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    note = ShiftNote(clinic_id=clinic.id, hospitalization_id=hospitalization.id, **values)
    session.add(note)
    await session.flush()
    return note


async def _actor_membership(session, clinic, role="vet"):
    user = await make_user(session)
    return await make_membership(session, clinic=clinic, user=user, role=role)


# --- esqueleto determinístico -------------------------------------------------


async def test_esqueleto_conta_tarefas_por_status_no_periodo(session):
    clinic = await make_clinic(session)
    hosp = await make_hospitalization(session, clinic=clinic)
    until = datetime.now(UTC)
    since = until - timedelta(hours=12)

    for _ in range(2):
        await make_task(
            session,
            clinic=clinic,
            hospitalization=hosp,
            status="done",
            scheduled_for=until - timedelta(hours=5),
            executed_at=until - timedelta(hours=5),
        )
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        status="partial",
        scheduled_for=until - timedelta(hours=4),
    )
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        status="not_done",
        scheduled_for=until - timedelta(hours=3),
        outcome_reason="refused",
    )
    # Pendente e MUITO vencida: atrasada é derivada na leitura, nunca persistida.
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        status="pending",
        scheduled_for=until - timedelta(hours=2),
    )
    # Pendente ainda dentro da tolerância: conta como pendente, não como atrasada.
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        status="pending",
        scheduled_for=until - timedelta(minutes=10),
    )
    # Fora da janela: não pode entrar em contador nenhum.
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        status="done",
        scheduled_for=since - timedelta(hours=1),
    )

    skeleton = await HandoverService.build_skeleton(
        session,
        hospitalization_id=hosp.id,
        clinic_id=clinic.id,
        since=since,
        until=until,
    )
    assert skeleton["tasks"] == {
        "done": 2,
        "partial": 1,
        "not_done": 1,
        "pending": 2,
        "overdue": 1,
    }


async def test_esqueleto_traz_eventos_mudancas_de_prescricao_e_notas(session):
    clinic = await make_clinic(session)
    hosp = await make_hospitalization(session, clinic=clinic)
    until = datetime.now(UTC)
    since = until - timedelta(hours=12)

    # Evento avulso: tarefa sem prescrição, o que a grade não explica sozinha.
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        title="Convulsão observada",
        category="care",
        status="done",
        scheduled_for=until - timedelta(hours=1),
        executed_at=until - timedelta(hours=1),
    )
    criada = await make_prescription(
        session, clinic=clinic, hospitalization=hosp, starts_at=until - timedelta(hours=4)
    )
    suspensa = await make_prescription(
        session,
        clinic=clinic,
        hospitalization=hosp,
        name="Tramadol",
        starts_at=since - timedelta(days=1),
        suspended_at=until - timedelta(hours=2),
    )
    await make_prescription(
        session,
        clinic=clinic,
        hospitalization=hosp,
        name="Fentanil 3 mcg/kg/h",
        starts_at=until - timedelta(hours=3),
        replaces_prescription_id=criada.id,
    )
    await make_shift_note(
        session, clinic=clinic, hospitalization=hosp, created_at=until - timedelta(hours=1)
    )

    skeleton = await HandoverService.build_skeleton(
        session,
        hospitalization_id=hosp.id,
        clinic_id=clinic.id,
        since=since,
        until=until,
    )
    assert [event["title"] for event in skeleton["events"]] == ["Convulsão observada"]
    changes = skeleton["prescription_changes"]
    assert [item["id"] for item in changes["created"]] == [str(criada.id)]
    assert [item["id"] for item in changes["suspended"]] == [str(suspensa.id)]
    assert [item["name"] for item in changes["adjusted"]] == ["Fentanil 3 mcg/kg/h"]
    assert [note["text"] for note in skeleton["notes"]] == ["Paciente vomitou às 3h."]


async def test_esqueleto_nao_enxerga_dados_de_outra_clinica(session):
    clinic = await make_clinic(session)
    outra = await make_clinic(session)
    hosp = await make_hospitalization(session, clinic=clinic)
    hosp_outra = await make_hospitalization(session, clinic=outra)
    until = datetime.now(UTC)
    since = until - timedelta(hours=12)

    await make_task(
        session, clinic=clinic, hospitalization=hosp, status="done", scheduled_for=until
    )
    for _ in range(3):
        await make_task(
            session,
            clinic=outra,
            hospitalization=hosp_outra,
            status="done",
            scheduled_for=until,
        )
    await make_shift_note(session, clinic=outra, hospitalization=hosp_outra, text="Nota vizinha")

    skeleton = await HandoverService.build_skeleton(
        session,
        hospitalization_id=hosp.id,
        clinic_id=clinic.id,
        since=since,
        until=until,
    )
    assert skeleton["tasks"]["done"] == 1
    assert skeleton["notes"] == []


# --- geração dos boletins -----------------------------------------------------


async def test_gera_um_boletim_por_internacao_ativa(session):
    clinic = await make_clinic(session)
    membership = await _actor_membership(session, clinic)
    ativas = [await make_hospitalization(session, clinic=clinic) for _ in range(2)]
    await make_hospitalization(session, clinic=clinic, status="discharged")
    shift = await make_shift(session, clinic=clinic, membership=membership)

    reports = await HandoverService.generate(
        session, clinic=clinic, from_shift=shift, to_shift=None, actor=None
    )
    assert len(reports) == 2
    assert {report.hospitalization_id for report in reports} == {h.id for h in ativas}
    # A narrativa é rascunho posterior: o boletim já serve sem ela.
    assert all(report.narrative is None for report in reports)
    assert all(report.skeleton["tasks"]["done"] == 0 for report in reports)


async def test_gerar_de_novo_nao_duplica_nem_apaga_aprovacao(session):
    clinic = await make_clinic(session)
    membership = await _actor_membership(session, clinic)
    await make_hospitalization(session, clinic=clinic)
    shift = await make_shift(session, clinic=clinic, membership=membership)

    primeiro = await HandoverService.generate(
        session, clinic=clinic, from_shift=shift, to_shift=None, actor=None
    )
    primeiro[0].narrative = "rascunho existente"
    primeiro[0].reviewed_at = datetime.now(UTC)
    await session.flush()

    segundo = await HandoverService.generate(
        session, clinic=clinic, from_shift=shift, to_shift=None, actor=None
    )
    assert [r.id for r in segundo] == [r.id for r in primeiro]
    assert segundo[0].narrative == "rascunho existente"
    assert segundo[0].reviewed_at is not None


# --- fechamento do turno e a regra "sem aprovação não bloqueia" ---------------


async def test_fechar_turno_gera_boletins_e_audita_falta_de_revisao(client, session):
    clinic = await make_clinic(session)
    membership = await _actor_membership(session, clinic)
    hosp = await make_hospitalization(session, clinic=clinic)
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        status="pending",
        scheduled_for=datetime.now(UTC) - timedelta(hours=3),
    )
    saindo = await make_shift(session, clinic=clinic, membership=membership)
    entrando = await make_shift(session, clinic=clinic, membership=membership, name="Noturno")

    resp = await client.post(
        f"/api/v1/shifts/{saindo.id}/close",
        json={"to_shift_id": str(entrando.id)},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["shift"]["closed_at"] is not None
    assert len(body["reports"]) == 1
    report = body["reports"][0]
    # A regra dura: sem aprovação o boletim EXISTE, vem inteiro e diz que não foi
    # revisado. Nada de erro, nada de conteúdo escondido.
    assert report["reviewed_at"] is None
    assert report["skeleton"]["tasks"]["overdue"] == 1
    assert body["missing_review"] == [report["id"]]

    omissoes = list(
        (
            await session.execute(
                sa.select(AuditEntry).where(
                    AuditEntry.clinic_id == clinic.id,
                    AuditEntry.action == "handover_missing_review",
                )
            )
        ).scalars()
    )
    assert len(omissoes) == 1
    assert omissoes[0].entity_id == uuid.UUID(report["id"])


async def test_plantao_seguinte_le_boletim_nao_revisado(client, session):
    clinic = await make_clinic(session)
    membership = await _actor_membership(session, clinic)
    await make_hospitalization(session, clinic=clinic)
    saindo = await make_shift(session, clinic=clinic, membership=membership)
    entrando = await make_shift(session, clinic=clinic, membership=membership, name="Noturno")
    headers = bearer(personal_token(membership))

    await client.post(
        f"/api/v1/shifts/{saindo.id}/close",
        json={"to_shift_id": str(entrando.id)},
        headers=headers,
    )
    # A consulta é feita pelo turno que ENTRA: o receptor precisa enxergar tudo.
    resp = await client.get(
        "/api/v1/handover/reports", params={"shift_id": str(entrando.id)}, headers=headers
    )
    assert resp.status_code == 200
    itens = resp.json()["items"]
    assert len(itens) == 1
    assert itens[0]["reviewed_at"] is None
    assert itens[0]["skeleton"]["tasks"] == {
        "done": 0,
        "partial": 0,
        "not_done": 0,
        "pending": 0,
        "overdue": 0,
    }


async def test_aceite_nao_exige_boletim_aprovado(client, session):
    clinic = await make_clinic(session)
    membership = await _actor_membership(session, clinic)
    await make_hospitalization(session, clinic=clinic)
    shift = await make_shift(session, clinic=clinic, membership=membership)
    headers = bearer(personal_token(membership))

    fechado = await client.post(
        f"/api/v1/shifts/{shift.id}/close", json={}, headers=headers
    )
    report_id = fechado.json()["reports"][0]["id"]

    resp = await client.post(
        f"/api/v1/handover/reports/{report_id}/ack",
        json={"seconds_to_ack": 2},
        headers=headers,
    )
    assert resp.status_code == 201


# --- aprovação e aceite gravam autor -----------------------------------------


async def test_aprovacao_e_aceite_gravam_autor(client, session):
    clinic = await make_clinic(session)
    quem_sai = await _actor_membership(session, clinic)
    quem_entra = await _actor_membership(session, clinic, role="tech")
    await make_hospitalization(session, clinic=clinic)
    shift = await make_shift(session, clinic=clinic, membership=quem_sai)

    fechado = await client.post(
        f"/api/v1/shifts/{shift.id}/close", json={}, headers=bearer(personal_token(quem_sai))
    )
    report_id = uuid.UUID(fechado.json()["reports"][0]["id"])

    aprovado = await client.post(
        f"/api/v1/handover/reports/{report_id}/approve",
        headers=bearer(personal_token(quem_sai)),
    )
    assert aprovado.status_code == 200
    assert aprovado.json()["reviewed_at"] is not None
    assert aprovado.json()["reviewed_by"] == str(quem_sai.id)

    aceite = await client.post(
        f"/api/v1/handover/reports/{report_id}/ack",
        json={"seconds_to_ack": 47},
        headers=bearer(personal_token(quem_entra)),
    )
    assert aceite.status_code == 201
    assert aceite.json()["membership_id"] == str(quem_entra.id)
    assert aceite.json()["seconds_to_ack"] == 47

    ack = (
        await session.execute(
            sa.select(HandoverAck).where(HandoverAck.handover_report_id == report_id)
        )
    ).scalar_one()
    assert ack.membership_id == quem_entra.id

    acoes = set(
        (
            await session.execute(
                sa.select(AuditEntry.action).where(AuditEntry.clinic_id == clinic.id)
            )
        )
        .scalars()
        .all()
    )
    assert {"handover_approved", "handover_acknowledged"} <= acoes


# --- notas de plantão ---------------------------------------------------------


async def test_nota_de_audio_grava_so_a_transcricao(client, session):
    clinic = await make_clinic(session)
    membership = await _actor_membership(session, clinic)
    hosp = await make_hospitalization(session, clinic=clinic)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/shift-notes",
        json={"text": "Ele urinou duas vezes na madrugada.", "source": "audio"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "audio"
    assert body["text"] == "Ele urinou duas vezes na madrugada."
    assert body["membership_id"] == str(membership.id)
    assert body["author_name"]

    # LGPD: a tabela não tem onde guardar o áudio, e é assim de propósito.
    colunas = {column.key for column in sa.inspect(ShiftNote).mapper.columns}
    assert colunas == {
        "id",
        "clinic_id",
        "hospitalization_id",
        "shift_id",
        "membership_id",
        "author_name",
        "text",
        "source",
        "created_at",
    }


async def test_notas_de_plantao_sao_listadas_por_internacao(client, session):
    clinic = await make_clinic(session)
    membership = await _actor_membership(session, clinic)
    hosp = await make_hospitalization(session, clinic=clinic)
    outra_hosp = await make_hospitalization(session, clinic=clinic)
    await make_shift_note(session, clinic=clinic, hospitalization=hosp, text="Da minha ficha")
    await make_shift_note(session, clinic=clinic, hospitalization=outra_hosp, text="Da outra")

    resp = await client.get(
        f"/api/v1/hospitalizations/{hosp.id}/shift-notes",
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert [item["text"] for item in resp.json()["items"]] == ["Da minha ficha"]


# --- narrativa no locale da clínica ------------------------------------------


async def test_narrativa_sai_no_locale_da_clinica(client, session):
    esperado = {"pt-BR": "concluída", "en": "completed"}
    for locale, palavra in esperado.items():
        clinic = await make_clinic(session, locale=locale)
        membership = await _actor_membership(session, clinic)
        hosp = await make_hospitalization(session, clinic=clinic)
        await make_shift_note(
            session, clinic=clinic, hospitalization=hosp, text="Comeu metade da ração."
        )
        shift = await make_shift(session, clinic=clinic, membership=membership)
        headers = bearer(personal_token(membership))

        fechado = await client.post(
            f"/api/v1/shifts/{shift.id}/close", json={}, headers=headers
        )
        report_id = fechado.json()["reports"][0]["id"]

        resp = await client.post(
            f"/api/v1/handover/reports/{report_id}/narrative", headers=headers
        )
        assert resp.status_code == 200
        narrativa = resp.json()["narrative"]
        assert palavra in narrativa
        # Conteúdo do cliente não é traduzido: a nota entra como foi escrita.
        assert "Comeu metade da ração." in narrativa


def test_narrativa_deterministica_dispensa_ia():
    skeleton = {
        "tasks": {"done": 3, "partial": 0, "not_done": 1, "pending": 2, "overdue": 1},
        "events": [{"title": "Convulsão"}],
        "prescription_changes": {"created": [{"id": "1"}], "suspended": [], "adjusted": []},
        "notes": [],
    }
    pt = NarrativeService.deterministic(skeleton, "pt-BR")
    en = NarrativeService.deterministic(skeleton, "en")
    assert "3" in pt and "1" in pt
    assert pt != en
    assert "Sem notas de plantão no período." in pt
    assert "No shift notes in the period." in en


# --- isolamento de tenant -----------------------------------------------------


async def test_boletim_de_outra_clinica_da_404(client, session):
    clinic = await make_clinic(session)
    vizinha = await make_clinic(session)
    membership = await _actor_membership(session, clinic)
    intrusa = await _actor_membership(session, vizinha)
    await make_hospitalization(session, clinic=clinic)
    shift = await make_shift(session, clinic=clinic, membership=membership)

    fechado = await client.post(
        f"/api/v1/shifts/{shift.id}/close", json={}, headers=bearer(personal_token(membership))
    )
    report_id = fechado.json()["reports"][0]["id"]

    intruso_headers = bearer(personal_token(intrusa))
    assert (
        await client.post(
            f"/api/v1/handover/reports/{report_id}/approve", headers=intruso_headers
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/handover/reports/{report_id}/ack",
            json={"seconds_to_ack": 1},
            headers=intruso_headers,
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/shifts/{shift.id}/close", json={}, headers=intruso_headers
        )
    ).status_code == 404
    listagem = await client.get("/api/v1/handover/reports", headers=intruso_headers)
    assert listagem.json()["items"] == []

    # E o boletim continua lá, íntegro, para a clínica dona.
    reports = list(
        (
            await session.execute(
                sa.select(HandoverReport).where(HandoverReport.clinic_id == clinic.id)
            )
        ).scalars()
    )
    assert len(reports) == 1


async def test_escala_e_criada_auditada_e_listada(client, session):
    clinic = await make_clinic(session)
    membership = await _actor_membership(session, clinic)
    vizinha = await make_clinic(session)
    intrusa = await _actor_membership(session, vizinha)
    headers = bearer(personal_token(membership))
    now = datetime.now(UTC)

    resp = await client.post(
        "/api/v1/shifts",
        json={
            "name": "Noturno",
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(hours=12)).isoformat(),
            "membership_id": str(membership.id),
            "is_vet_responsible": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["is_vet_responsible"] is True

    acoes = (
        await session.execute(
            sa.select(AuditEntry.action).where(AuditEntry.clinic_id == clinic.id)
        )
    ).scalars()
    assert "shift_created" in set(acoes)

    listagem = await client.get("/api/v1/shifts", headers=headers)
    assert [item["name"] for item in listagem.json()["items"]] == ["Noturno"]
    # A escala da vizinha não vaza.
    assert (await client.get("/api/v1/shifts", headers=bearer(personal_token(intrusa)))).json()[
        "items"
    ] == []


async def test_escalar_membro_de_outra_clinica_da_404(client, session):
    clinic = await make_clinic(session)
    membership = await _actor_membership(session, clinic)
    vizinha = await make_clinic(session)
    forasteiro = await _actor_membership(session, vizinha)
    now = datetime.now(UTC)

    resp = await client.post(
        "/api/v1/shifts",
        json={
            "name": "Noturno",
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(hours=12)).isoformat(),
            "membership_id": str(forasteiro.id),
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 404


async def test_boletim_traz_as_pendencias_para_o_ato_do_aceite(client, session):
    """"3 pendentes" é um número; "Glicemia das 16h" é a coisa a fazer.

    A pesquisa nomeia a síntese do receptor como o elemento mais negligenciado
    do I-PASS, e a spec é literal: pendências e atrasadas visíveis NO PRÓPRIO
    ATO do aceite. O boletim mandava só contadores.
    """
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    agora = datetime.now(UTC)
    session.add(
        Task(
            clinic_id=clinic.id,
            hospitalization_id=hosp.id,
            title="Glicemia capilar",
            category="monitoring",
            scheduled_for=agora - timedelta(hours=3),
            criticality="critical",
            tolerance_minutes=30,
            status="pending",
        )
    )
    await session.flush()

    headers = bearer(personal_token(membership))
    turno = (
        await client.post(
            "/api/v1/shifts",
            json={
                "name": "Diurno",
                "starts_at": (agora - timedelta(hours=12)).isoformat(),
                "ends_at": agora.isoformat(),
                "membership_id": str(membership.id),
                "is_vet_responsible": True,
            },
            headers=headers,
        )
    ).json()
    await client.post(f"/api/v1/shifts/{turno['id']}/close", json={}, headers=headers)

    boletins = (await client.get("/api/v1/handover/reports", headers=headers)).json()["items"]
    assert boletins, "o fechamento do turno precisa gerar boletim"

    boletim = boletins[0]
    abertas = boletim["open_tasks"]
    assert [t["title"] for t in abertas] == ["Glicemia capilar"]
    assert abertas[0]["display_state"] == "overdue"
    # E o boletim continua se identificando sozinho, sem cruzar com o painel.
    assert boletim["patient_name"] is not None


async def test_boletim_nao_lista_o_turno_que_entra(client, session):
    """Quem recebe assume a DÍVIDA, não a agenda dele.

    A lista trazia toda pendente das 12h seguintes: quinze linhas por paciente,
    a maioria delas o trabalho normal do turno que está começando. Aceitar a
    dose de amanhã às 02h não é passagem de plantão. É a fila do plantão, que
    tem tela própria. Fica em aberto só o que era para ter sido feito ATÉ o
    fechamento do turno anterior.
    """
    from app.models import Task

    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    agora = datetime.now(UTC)
    for titulo, quando in (
        ("Ficou para trás", agora - timedelta(hours=2)),
        ("Trabalho do próximo turno", agora + timedelta(hours=6)),
    ):
        session.add(
            Task(
                clinic_id=clinic.id,
                hospitalization_id=hosp.id,
                title=titulo,
                category="medication",
                scheduled_for=quando,
                criticality="normal",
                tolerance_minutes=60,
                status="pending",
            )
        )
    await session.flush()

    headers = bearer(personal_token(membership))
    turno = (
        await client.post(
            "/api/v1/shifts",
            json={
                "name": "Diurno",
                "starts_at": (agora - timedelta(hours=12)).isoformat(),
                "ends_at": agora.isoformat(),
                "membership_id": str(membership.id),
                "is_vet_responsible": True,
            },
            headers=headers,
        )
    ).json()
    await client.post(f"/api/v1/shifts/{turno['id']}/close", json={}, headers=headers)

    boletim = (await client.get("/api/v1/handover/reports", headers=headers)).json()["items"][0]
    assert [t["title"] for t in boletim["open_tasks"]] == ["Ficou para trás"]


async def test_fechar_o_turno_encontra_quem_esta_entrando(client, session):
    """O boletim nascia com remetente e sem destinatário.

    `to_shift_id` só era preenchido se o cliente o mandasse, e nenhum manda.
    Sem ele, quem chega nunca vê a passagem endereçada a si: some a metade do
    I-PASS que a pesquisa nomeia como a mais negligenciada, a síntese de quem
    recebe.
    """
    clinic = await make_clinic(session)
    user_sai = await make_user(session)
    sai = await make_membership(session, clinic=clinic, user=user_sai, role="tech")
    user_entra = await make_user(session)
    entra = await make_membership(session, clinic=clinic, user=user_entra, role="vet")
    await make_hospitalization(session, clinic=clinic, membership=entra)
    agora = datetime.now(UTC)

    turno_que_sai = Shift(
        clinic_id=clinic.id,
        membership_id=sai.id,
        name="Diurno",
        starts_at=agora - timedelta(hours=12),
        ends_at=agora,
    )
    turno_que_entra = Shift(
        clinic_id=clinic.id,
        membership_id=entra.id,
        name="Noturno",
        starts_at=agora - timedelta(minutes=5),
        ends_at=agora + timedelta(hours=12),
        is_vet_responsible=True,
    )
    session.add_all([turno_que_sai, turno_que_entra])
    await session.flush()

    resp = await client.post(
        f"/api/v1/shifts/{turno_que_sai.id}/close",
        json={},
        headers=bearer(personal_token(sai)),
    )
    assert resp.status_code == 200

    boletins = (
        await client.get(
            f"/api/v1/handover/reports?shift_id={turno_que_entra.id}",
            headers=bearer(personal_token(entra)),
        )
    ).json()["items"]
    assert boletins, "quem entra precisa enxergar o boletim endereçado ao turno dele"
    assert all(b["to_shift_id"] == str(turno_que_entra.id) for b in boletins)
