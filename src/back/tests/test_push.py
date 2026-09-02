"""Push: registro do aparelho, orçamento de alertas e os DOIS motivos de alerta.

Nenhum teste toca a rede: o transporte do `httpx` é um `MockTransport`, e o
emissor de token da service account é substituído.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.config import settings
from app.models import Device, Shift
from app.services import push as push_module
from app.services.push import Alert, PushClient, PushService
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_task,
    make_user,
)
from tests.helpers import bearer, personal_token, station_token


class FakeFcm:
    """O FCM que a suíte usa. Guarda o que foi enviado e responde o combinado."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.authorizations: list[str | None] = []
        #: token -> (status_code, payload) para simular token morto e erro 5xx.
        self.responses: dict[str, tuple[int, dict]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)["message"]
        self.messages.append(message)
        self.authorizations.append(request.headers.get("authorization"))
        status, payload = self.responses.get(
            message["token"], (200, {"name": "projects/test/messages/1"})
        )
        return httpx.Response(status, json=payload)

    @property
    def tokens(self) -> list[str]:
        return [message["token"] for message in self.messages]


class ExplodingTransport(httpx.AsyncBaseTransport):
    """Prova que o caminho desligado não chega a abrir conexão nenhuma."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError("push desligado não pode falar com o provedor")


@pytest.fixture(autouse=True)
def clean_budget():
    # O orçamento é estado de processo: sem zerar, um teste herdaria o teto
    # gasto pelo anterior.
    push_module.budget.reset()
    yield
    push_module.budget.reset()


@pytest.fixture
def fcm(monkeypatch) -> FakeFcm:
    recorder = FakeFcm()
    monkeypatch.setattr(settings, "fcm_project", "plantaovet-test")

    async def fake_access_token(scope: str) -> str:
        assert scope == "https://www.googleapis.com/auth/firebase.messaging"
        return "fake-bearer"

    monkeypatch.setattr(push_module, "access_token", fake_access_token)
    monkeypatch.setattr(
        push_module, "push_client", PushClient(transport=httpx.MockTransport(recorder))
    )
    return recorder


@pytest.fixture
def push_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "fcm_project", None)
    monkeypatch.setattr(
        push_module, "push_client", PushClient(transport=ExplodingTransport())
    )


async def add_device(session, *, clinic, membership, token: str = "") -> Device:
    device = Device(
        clinic_id=clinic.id,
        membership_id=membership.id,
        token=token or f"fcm-{uuid.uuid4().hex}",
        platform="android",
        created_at=datetime.now(UTC),
    )
    session.add(device)
    await session.flush()
    return device


async def put_on_duty(session, *, clinic, membership, now, is_vet_responsible=True) -> Shift:
    shift = Shift(
        clinic_id=clinic.id,
        name="Noturno",
        starts_at=now - timedelta(hours=2),
        ends_at=now + timedelta(hours=6),
        membership_id=membership.id,
        is_vet_responsible=is_vet_responsible,
    )
    session.add(shift)
    await session.flush()
    return shift


# ---- registro do aparelho -------------------------------------------------


async def test_register_is_idempotent_on_the_token(client, session):
    clinic = await make_clinic(session)
    membership = await make_membership(session, clinic=clinic, role="tech")

    payload = {"token": "fcm-token-abc12345", "platform": "android"}
    headers = bearer(personal_token(membership))
    first = await client.post("/api/v1/devices", json=payload, headers=headers)
    second = await client.post("/api/v1/devices", json=payload, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    rows = list((await session.execute(Device.__table__.select())).all())
    assert len(rows) == 1


async def test_reregistering_moves_the_token_to_whoever_holds_the_phone(client, session, fcm):
    """O celular da enfermaria troca de mão na virada do turno."""
    clinic = await make_clinic(session)
    saindo = await make_membership(session, clinic=clinic, role="tech")
    entrando = await make_membership(
        session, clinic=clinic, user=await make_user(session), role="tech"
    )

    payload = {"token": "fcm-ward-phone-1", "platform": "android"}
    await client.post("/api/v1/devices", json=payload, headers=bearer(personal_token(saindo)))
    await client.post("/api/v1/devices", json=payload, headers=bearer(personal_token(entrando)))

    devices = list((await session.execute(Device.__table__.select())).all())
    assert len(devices) == 1
    device = await session.get(Device, devices[0].id)
    await session.refresh(device)
    assert device.membership_id == entrando.id

    alert = Alert(title="t", body="b", data={})
    # Quem saiu não recebe mais nada por este aparelho: é o vazamento que a
    # troca de dono existe para fechar.
    assert (
        await PushService.notify(
            session, clinic_id=clinic.id, membership_ids=[saindo.id], alert=alert
        )
        == 0
    )
    assert (
        await PushService.notify(
            session, clinic_id=clinic.id, membership_ids=[entrando.id], alert=alert
        )
        == 1
    )


async def test_delete_removes_the_token_and_is_idempotent(client, session):
    clinic = await make_clinic(session)
    membership = await make_membership(session, clinic=clinic, role="tech")
    headers = bearer(personal_token(membership))
    await client.post(
        "/api/v1/devices", json={"token": "fcm-token-xyz98765"}, headers=headers
    )

    first = await client.delete("/api/v1/devices/fcm-token-xyz98765", headers=headers)
    second = await client.delete("/api/v1/devices/fcm-token-xyz98765", headers=headers)

    assert first.status_code == 204
    assert second.status_code == 204
    assert list((await session.execute(Device.__table__.select())).all()) == []


async def test_station_without_pin_cannot_register_a_device(client, session):
    """Tablet compartilhado sem ninguém identificado não tem a quem notificar."""
    clinic = await make_clinic(session)
    response = await client.post(
        "/api/v1/devices",
        json={"token": "fcm-token-station1"},
        headers=bearer(station_token(clinic)),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "operator_required"


async def test_expo_token_is_registered_but_declared_undeliverable(client, session):
    """O app registra token do Expo, que o FCM não entrega, e a resposta diz."""
    clinic = await make_clinic(session)
    membership = await make_membership(session, clinic=clinic, role="tech")
    response = await client.post(
        "/api/v1/devices",
        json={"token": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]", "platform": "android"},
        headers=bearer(personal_token(membership)),
    )
    assert response.status_code == 200
    assert response.json()["deliverable"] is False


# ---- sem credencial o sistema segue inteiro -------------------------------


async def test_push_without_credentials_is_a_silent_no_op(session, push_off):
    clinic = await make_clinic(session)
    membership = await make_membership(session, clinic=clinic, role="vet")
    await add_device(session, clinic=clinic, membership=membership)

    entregues = await PushService.notify(
        session,
        clinic_id=clinic.id,
        membership_ids=[membership.id],
        alert=Alert(title="t", body="b", data={}),
    )
    assert entregues == 0


async def test_ad_hoc_with_notify_vet_still_succeeds_without_push(client, session, push_off):
    """A execução clínica não pode depender de uma integração opcional."""
    clinic = await make_clinic(session)
    tech = await make_membership(session, clinic=clinic, role="tech")
    hospitalization = await make_hospitalization(session, clinic=clinic)

    response = await client.post(
        "/api/v1/tasks/ad-hoc",
        json={
            "hospitalization_id": str(hospitalization.id),
            "title": "Convulsão",
            "category": "care",
            "values": {"note": "2 minutos, autolimitada", "notify_vet": True},
        },
        headers=bearer(personal_token(tech)),
    )
    assert response.status_code == 201
    assert response.json()["status"] == "done"


# ---- "avisar o veterinário" cumprindo o que promete ------------------------


async def test_notify_vet_reaches_the_vet_on_duty(client, session, fcm):
    now = datetime.now(UTC)
    clinic = await make_clinic(session)
    tech = await make_membership(session, clinic=clinic, role="tech")
    vet = await make_membership(
        session, clinic=clinic, user=await make_user(session), role="vet"
    )
    await put_on_duty(session, clinic=clinic, membership=vet, now=now)
    await add_device(session, clinic=clinic, membership=vet, token="fcm-vet-de-plantao")
    hospitalization = await make_hospitalization(session, clinic=clinic)

    response = await client.post(
        "/api/v1/tasks/ad-hoc",
        json={
            "hospitalization_id": str(hospitalization.id),
            "title": "Convulsão",
            "category": "care",
            "values": {"note": "2 minutos, autolimitada", "notify_vet": True},
        },
        headers=bearer(personal_token(tech)),
    )

    assert response.status_code == 201
    assert fcm.tokens == ["fcm-vet-de-plantao"]
    message = fcm.messages[0]
    assert fcm.authorizations[0] == "Bearer fake-bearer"
    # Sem o nome do paciente e o que houve, o alerta obriga a abrir o app no
    # meio da noite para saber de quem se trata.
    assert "Thor" in message["notification"]["title"]
    assert "Convulsão" in message["notification"]["body"]
    assert message["data"]["kind"] == "intercurrence"
    assert message["android"]["notification"]["channel_id"] == "critical"


async def test_notify_vet_without_a_rota_reaches_every_active_vet(client, session, fcm):
    """Escala em branco é falha de cadastro, não ausência de responsável."""
    clinic = await make_clinic(session)
    tech = await make_membership(session, clinic=clinic, role="tech")
    vet = await make_membership(
        session, clinic=clinic, user=await make_user(session), role="vet"
    )
    await add_device(session, clinic=clinic, membership=vet, token="fcm-vet-em-casa")
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=vet)

    response = await client.post(
        "/api/v1/tasks/ad-hoc",
        json={
            "hospitalization_id": str(hospitalization.id),
            "title": "Vômito",
            "category": "care",
            "values": {"notify_vet": True},
        },
        headers=bearer(personal_token(tech)),
    )

    assert response.status_code == 201
    assert fcm.tokens == ["fcm-vet-em-casa"]


async def test_ad_hoc_without_notify_vet_notifies_nobody(client, session, fcm):
    clinic = await make_clinic(session)
    tech = await make_membership(session, clinic=clinic, role="tech")
    vet = await make_membership(
        session, clinic=clinic, user=await make_user(session), role="vet"
    )
    await add_device(session, clinic=clinic, membership=vet)
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=vet)

    response = await client.post(
        "/api/v1/tasks/ad-hoc",
        json={
            "hospitalization_id": str(hospitalization.id),
            "title": "Micção",
            "category": "care",
            "values": {"notify_vet": False},
        },
        headers=bearer(personal_token(tech)),
    )
    assert response.status_code == 201
    assert fcm.messages == []


async def test_the_author_is_not_notified_about_their_own_event(client, session, fcm):
    now = datetime.now(UTC)
    clinic = await make_clinic(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await put_on_duty(session, clinic=clinic, membership=vet, now=now)
    await add_device(session, clinic=clinic, membership=vet)
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=vet)

    response = await client.post(
        "/api/v1/tasks/ad-hoc",
        json={
            "hospitalization_id": str(hospitalization.id),
            "title": "Diarreia",
            "category": "care",
            "values": {"notify_vet": True},
        },
        headers=bearer(personal_token(vet)),
    )
    assert response.status_code == 201
    assert fcm.messages == []


# ---- orçamento de alertas -------------------------------------------------


async def test_the_hourly_cap_holds(session, fcm, monkeypatch):
    monkeypatch.setattr(settings, "push_max_per_hour", 2)
    clinic = await make_clinic(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await add_device(session, clinic=clinic, membership=vet)

    entregues = 0
    for indice in range(5):
        entregues += await PushService.notify(
            session,
            clinic_id=clinic.id,
            membership_ids=[vet.id],
            alert=Alert(title="t", body="b", data={}),
            event_key=f"evento:{indice}",
        )

    assert entregues == 2
    assert len(fcm.messages) == 2


async def test_the_same_event_never_notifies_twice(session, fcm):
    clinic = await make_clinic(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await add_device(session, clinic=clinic, membership=vet)
    alert = Alert(title="t", body="b", data={})

    primeiro = await PushService.notify(
        session,
        clinic_id=clinic.id,
        membership_ids=[vet.id],
        alert=alert,
        event_key="critical_overdue:1",
    )
    segundo = await PushService.notify(
        session,
        clinic_id=clinic.id,
        membership_ids=[vet.id],
        alert=alert,
        event_key="critical_overdue:1",
    )

    assert (primeiro, segundo) == (1, 0)
    assert len(fcm.messages) == 1


async def test_a_failed_send_spends_no_budget_and_can_be_retried(session, fcm):
    """Provedor fora do ar não pode gastar o alerta de ninguém."""
    clinic = await make_clinic(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    device = await add_device(session, clinic=clinic, membership=vet)
    fcm.responses[device.token] = (503, {"error": {"status": "UNAVAILABLE"}})
    alert = Alert(title="t", body="b", data={})

    falhou = await PushService.notify(
        session, clinic_id=clinic.id, membership_ids=[vet.id], alert=alert, event_key="e:1"
    )
    fcm.responses.clear()
    depois = await PushService.notify(
        session, clinic_id=clinic.id, membership_ids=[vet.id], alert=alert, event_key="e:1"
    )

    assert (falhou, depois) == (0, 1)


async def test_a_dead_token_is_deactivated(session, fcm):
    clinic = await make_clinic(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    device = await add_device(session, clinic=clinic, membership=vet)
    fcm.responses[device.token] = (404, {"error": {"status": "NOT_FOUND"}})

    entregues = await PushService.notify(
        session,
        clinic_id=clinic.id,
        membership_ids=[vet.id],
        alert=Alert(title="t", body="b", data={}),
    )

    assert entregues == 0
    await session.refresh(device)
    assert device.is_active is False


async def test_an_expo_token_is_never_counted_as_delivered(session, fcm):
    clinic = await make_clinic(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await add_device(
        session, clinic=clinic, membership=vet, token="ExponentPushToken[abcdefghij]"
    )

    entregues = await PushService.notify(
        session,
        clinic_id=clinic.id,
        membership_ids=[vet.id],
        alert=Alert(title="t", body="b", data={}),
    )

    assert entregues == 0
    assert fcm.messages == []


# ---- dose crítica fora da janela ------------------------------------------


async def test_a_critical_overdue_dose_wakes_the_shift(session, fcm):
    now = datetime.now(UTC)
    clinic = await make_clinic(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await put_on_duty(session, clinic=clinic, membership=vet, now=now)
    await add_device(session, clinic=clinic, membership=vet, token="fcm-plantao")
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=vet)
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hospitalization,
        title="Noradrenalina",
        criticality="critical",
        tolerance_minutes=30,
        scheduled_for=now - timedelta(hours=2),
    )
    # Atrasada, porém NORMAL: escalona no painel e não vibra em bolso nenhum.
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hospitalization,
        title="Dipirona",
        criticality="normal",
        tolerance_minutes=30,
        scheduled_for=now - timedelta(hours=3),
    )

    entregues = await PushService.sweep_critical_overdue(session, clinic=clinic, now=now)

    assert entregues == 1
    assert fcm.tokens == ["fcm-plantao"]
    message = fcm.messages[0]
    assert "Noradrenalina" in message["notification"]["body"]
    assert "Dipirona" not in message["notification"]["body"]
    assert message["data"]["kind"] == "critical_overdue"


async def test_a_critical_dose_inside_the_window_is_not_an_alert(session, fcm):
    """A janela ISMP é o que separa alerta de ruído: dentro dela, nada vibra."""
    now = datetime.now(UTC)
    clinic = await make_clinic(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await put_on_duty(session, clinic=clinic, membership=vet, now=now)
    await add_device(session, clinic=clinic, membership=vet)
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=vet)
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hospitalization,
        title="Noradrenalina",
        criticality="critical",
        tolerance_minutes=30,
        scheduled_for=now - timedelta(minutes=10),
    )

    assert await PushService.sweep_critical_overdue(session, clinic=clinic, now=now) == 0
    assert fcm.messages == []


async def test_the_sweep_does_not_repeat_the_same_dose(session, fcm):
    now = datetime.now(UTC)
    clinic = await make_clinic(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await put_on_duty(session, clinic=clinic, membership=vet, now=now)
    await add_device(session, clinic=clinic, membership=vet)
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=vet)
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hospitalization,
        title="Noradrenalina",
        criticality="critical",
        tolerance_minutes=30,
        scheduled_for=now - timedelta(hours=2),
    )

    primeiro = await PushService.sweep_critical_overdue(session, clinic=clinic, now=now)
    segundo = await PushService.sweep_critical_overdue(
        session, clinic=clinic, now=now + timedelta(minutes=30)
    )

    assert (primeiro, segundo) == (1, 0)


async def test_a_done_task_is_not_swept(session, fcm):
    now = datetime.now(UTC)
    clinic = await make_clinic(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await put_on_duty(session, clinic=clinic, membership=vet, now=now)
    await add_device(session, clinic=clinic, membership=vet)
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=vet)
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hospitalization,
        criticality="critical",
        tolerance_minutes=30,
        scheduled_for=now - timedelta(hours=2),
        status="done",
        executed_at=now - timedelta(hours=2),
    )

    assert await PushService.sweep_critical_overdue(session, clinic=clinic, now=now) == 0


async def test_the_worker_shaped_sweep_covers_every_clinic(session, db_session_factory, fcm):
    """Formato de job: falta UMA linha em `app/workers/scheduler.py` para ligar."""
    now = datetime.now(UTC)
    clinic = await make_clinic(session)
    vet = await make_membership(session, clinic=clinic, role="vet")
    await put_on_duty(session, clinic=clinic, membership=vet, now=now)
    await add_device(session, clinic=clinic, membership=vet, token="fcm-varredura")
    hospitalization = await make_hospitalization(session, clinic=clinic, membership=vet)
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hospitalization,
        title="Noradrenalina",
        criticality="critical",
        tolerance_minutes=30,
        scheduled_for=now - timedelta(hours=2),
    )

    assert await PushService.sweep_all_clinics(db_session_factory, now=now) == 1
    assert fcm.tokens == ["fcm-varredura"]
