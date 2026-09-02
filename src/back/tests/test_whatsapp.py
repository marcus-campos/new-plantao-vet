"""Boletim ao tutor por WhatsApp: envio real, falha honesta e webhook de status.

A regra que estes testes protegem é uma só: o prontuário nunca afirma uma
entrega que não houve. O stub anterior devolvia `stub-<uuid>` e a rota gravava
`sent_at` de qualquer jeito: havia registro auditado de envio que nunca saiu.

Nenhum teste toca a rede: o cliente recebe um `httpx.MockTransport`.
"""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa

from app.api.routes import owner_contacts
from app.core.config import settings
from app.models import AuditEntry
from app.models.owner_contact import ContactChannel, ContactStatus, OwnerContact
from app.services.whatsapp import WhatsAppClient
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_owner,
    make_patient,
    make_user,
)
from tests.helpers import bearer, personal_token

APP_SECRET = "app-secret-de-teste"
VERIFY_TOKEN = "verify-token-de-teste"
WAMID = "wamid.HBgNNTUxMTk5OTk5MDAwMBUCABEYEjAx"


async def _staff(session, clinic=None, role="vet"):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    return clinic, await make_membership(session, clinic=clinic, user=user, role=role)


async def _internacao_com_opt_in(session):
    clinic, vet = await _staff(session)
    owner = await make_owner(session, clinic=clinic, whatsapp_opt_in_at=datetime.now(UTC))
    patient = await make_patient(session, clinic=clinic, owner=owner, name="Thor")
    hospitalization = await make_hospitalization(session, clinic=clinic, patient=patient)
    return clinic, vet, owner, hospitalization


def _install(monkeypatch, handler) -> list[httpx.Request]:
    """Troca o cliente da rota por um que fala com `handler`, não com a Meta."""
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(settings, "whatsapp_token", "EAAG-token")
    monkeypatch.setattr(settings, "whatsapp_template_name", "boletim_internacao")
    monkeypatch.setattr(
        owner_contacts,
        "whatsapp_client",
        WhatsAppClient(transport=httpx.MockTransport(_record)),
    )
    return seen


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": "5511999990000", "wa_id": "5511999990000"}],
            "messages": [{"id": WAMID, "message_status": "accepted"}],
        },
    )


def _status_payload(
    wamid: str, status: str, moment: datetime, errors: list[dict] | None = None
) -> dict:
    entry: dict = {
        "id": wamid,
        "status": status,
        "timestamp": str(int(moment.timestamp())),
        "recipient_id": "5511999990000",
    }
    if errors:
        entry["errors"] = errors
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "102290129340398",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "1234567890"},
                            "statuses": [entry],
                        },
                    }
                ],
            }
        ],
    }


def _signed(payload: dict, secret: str = APP_SECRET) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(payload).encode()
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}


async def _make_contact(session, **overrides) -> OwnerContact:
    clinic, vet, owner, hospitalization = await _internacao_com_opt_in(session)
    campos = {
        "channel": ContactChannel.whatsapp,
        "summary": "Boletim das 18h",
        "status": ContactStatus.sent,
        "sent_at": datetime.now(UTC),
        "external_id": WAMID,
        "author_name": "Dra. Ana",
        **overrides,
    }
    contact = OwnerContact(
        clinic_id=clinic.id,
        hospitalization_id=hospitalization.id,
        owner_id=owner.id,
        **campos,
    )
    session.add(contact)
    await session.flush()
    return contact


# --- envio -----------------------------------------------------------------


async def test_sem_credencial_grava_tentativa_falha_e_nao_afirma_envio(
    client, session, monkeypatch
):
    # Sem credencial a integração está ausente, não quebrada: a API responde um
    # código que a interface explica, e o histórico mostra a tentativa.
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", None)
    monkeypatch.setattr(settings, "whatsapp_token", None)

    def _explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError("sem credencial não pode sair chamada nenhuma")

    monkeypatch.setattr(
        owner_contacts,
        "whatsapp_client",
        WhatsAppClient(transport=httpx.MockTransport(_explode)),
    )
    _, vet, _, hospitalization = await _internacao_com_opt_in(session)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts/whatsapp",
        json={"body": "Thor estável, comeu bem."},
        headers=bearer(personal_token(vet)),
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "whatsapp_not_configured"

    historico = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts",
        headers=bearer(personal_token(vet)),
    )
    linhas = historico.json()
    assert len(linhas) == 1
    assert linhas[0]["status"] == "failed"
    assert linhas[0]["failure_reason"] == "not_configured"
    # As três marcas de "isto foi entregue" têm de estar vazias.
    assert linhas[0]["sent_at"] is None
    assert linhas[0]["delivered_at"] is None
    assert linhas[0]["external_id"] is None

    gravadas = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(OwnerContact)
            .where(OwnerContact.sent_at.is_not(None))
        )
    ).scalar_one()
    assert gravadas == 0, "nenhuma linha pode afirmar envio"


async def test_envio_bem_sucedido_manda_template_no_locale_e_guarda_o_wamid(
    client, session, monkeypatch
):
    seen = _install(monkeypatch, _ok)
    _, vet, _, hospitalization = await _internacao_com_opt_in(session)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts/whatsapp",
        json={"body": "Thor  estável.\nComeu bem às 18h.", "summary": "Boletim das 18h"},
        headers=bearer(personal_token(vet)),
    )
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["status"] == "sent"
    assert corpo["external_id"] == WAMID
    assert corpo["sent_at"] is not None
    assert corpo["failure_reason"] is None
    assert corpo["delivered_at"] is None  # só o webhook preenche

    (request,) = seen
    assert request.url.path == "/v21.0/1234567890/messages"
    assert request.headers["Authorization"] == "Bearer EAAG-token"
    enviado = json.loads(request.content)
    assert enviado["type"] == "template"
    assert enviado["to"] == "5511999990000"  # E.164 sem o "+"
    assert enviado["template"]["name"] == "boletim_internacao"
    assert enviado["template"]["language"] == {"code": "pt_BR"}
    parametros = [p["text"] for p in enviado["template"]["components"][0]["parameters"]]
    # Quebra de linha e espaço duplo em variável fazem a Meta recusar (132000).
    assert parametros[0] == "Thor"
    assert parametros[2] == "Thor estável. Comeu bem às 18h."


async def test_erro_da_meta_grava_failed_com_o_motivo(client, session, monkeypatch):
    def _recusa(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Template name does not exist in the translation",
                    "code": 132001,
                }
            },
        )

    _install(monkeypatch, _recusa)
    _, vet, _, hospitalization = await _internacao_com_opt_in(session)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts/whatsapp",
        json={"body": "Thor estável."},
        headers=bearer(personal_token(vet)),
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "whatsapp_send_failed"

    contato = (await session.execute(sa.select(OwnerContact))).scalar_one()
    assert contato.status is ContactStatus.failed
    assert contato.sent_at is None
    assert contato.external_id is None
    assert "132001" in contato.failure_reason
    # A tentativa frustrada é auditada como tentativa, não como envio.
    acoes = (
        (
            await session.execute(
                sa.select(AuditEntry.action).where(AuditEntry.entity_type == "owner_contact")
            )
        )
        .scalars()
        .all()
    )
    assert acoes == ["owner_contact_whatsapp_failed"]


async def test_rede_fora_nao_vira_500(client, session, monkeypatch):
    def _cai(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _install(monkeypatch, _cai)
    _, vet, _, hospitalization = await _internacao_com_opt_in(session)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts/whatsapp",
        json={"body": "Thor estável."},
        headers=bearer(personal_token(vet)),
    )
    assert resp.status_code == 502
    contato = (await session.execute(sa.select(OwnerContact))).scalar_one()
    assert contato.status is ContactStatus.failed
    assert contato.sent_at is None
    assert contato.failure_reason == "network: ConnectError"


async def test_opt_in_continua_barrando_antes_de_qualquer_registro(client, session, monkeypatch):
    _install(monkeypatch, _ok)
    clinic, vet = await _staff(session)
    owner = await make_owner(session, clinic=clinic)  # sem opt-in
    patient = await make_patient(session, clinic=clinic, owner=owner)
    hospitalization = await make_hospitalization(session, clinic=clinic, patient=patient)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts/whatsapp",
        json={"body": "Thor estável."},
        headers=bearer(personal_token(vet)),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "whatsapp_opt_in_required"
    # Sem opt-in não há nem tentativa: a mensagem não podia sequer ser tentada.
    assert (
        await session.execute(sa.select(sa.func.count()).select_from(OwnerContact))
    ).scalar_one() == 0


# --- webhook: handshake ----------------------------------------------------


async def test_handshake_devolve_o_desafio_cru(client, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_verify_token", VERIFY_TOKEN)
    resp = await client.get(
        "/api/v1/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "1158201444"


@pytest.mark.parametrize("token", ["token-errado", VERIFY_TOKEN])
async def test_handshake_recusa_token_errado_ou_verificacao_desligada(
    client, monkeypatch, token
):
    # Token errado, e também token certo com a verificação NÃO configurada:
    # sem segredo do nosso lado não há o que verificar, então não se aceita.
    monkeypatch.setattr(
        settings, "whatsapp_verify_token", None if token == VERIFY_TOKEN else VERIFY_TOKEN
    )
    resp = await client.get(
        "/api/v1/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "x"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "whatsapp_webhook_unverified"


# --- webhook: status -------------------------------------------------------


async def test_assinatura_invalida_e_recusada_e_nao_escreve_nada(client, session, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", APP_SECRET)
    contato = await _make_contact(session)
    raw, headers = _signed(_status_payload(WAMID, "delivered", datetime.now(UTC)), "outro-segredo")

    resp = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers=headers)

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "whatsapp_webhook_unverified"
    assert contato.delivered_at is None
    assert contato.status is ContactStatus.sent


async def test_sem_app_secret_configurado_o_webhook_nao_aceita_nada(client, session, monkeypatch):
    # Endpoint público sem segredo é porta aberta para escrever no prontuário.
    monkeypatch.setattr(settings, "whatsapp_app_secret", None)
    contato = await _make_contact(session)
    raw, headers = _signed(_status_payload(WAMID, "delivered", datetime.now(UTC)))

    resp = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers=headers)

    assert resp.status_code == 403
    assert contato.delivered_at is None


async def test_callback_de_entrega_preenche_delivered_at(client, session, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", APP_SECRET)
    contato = await _make_contact(session)
    momento = datetime.now(UTC).replace(microsecond=0)
    raw, headers = _signed(_status_payload(WAMID, "delivered", momento))

    resp = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"applied": 1}
    assert contato.status is ContactStatus.delivered
    assert contato.delivered_at == momento
    assert contato.read_at is None


async def test_reprocessar_o_mesmo_callback_nao_muda_nada(client, session, monkeypatch):
    # A Meta reenvia o mesmo callback até receber 200; reentrega não pode
    # duplicar auditoria nem remexer no carimbo de entrega.
    monkeypatch.setattr(settings, "whatsapp_app_secret", APP_SECRET)
    contato = await _make_contact(session)
    raw, headers = _signed(_status_payload(WAMID, "delivered", datetime.now(UTC)))

    primeira = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers=headers)
    entregue_em = contato.delivered_at
    segunda = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers=headers)

    assert primeira.json() == {"applied": 1}
    assert segunda.json() == {"applied": 0}
    assert contato.delivered_at == entregue_em
    auditorias = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(AuditEntry)
            .where(AuditEntry.action == "owner_contact_status_updated")
        )
    ).scalar_one()
    assert auditorias == 1


async def test_sent_atrasado_nao_desfaz_read(client, session, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", APP_SECRET)
    contato = await _make_contact(session)
    lido_em = datetime.now(UTC).replace(microsecond=0)
    raw_read, headers_read = _signed(_status_payload(WAMID, "read", lido_em))
    await client.post("/api/v1/webhooks/whatsapp", content=raw_read, headers=headers_read)
    assert contato.status is ContactStatus.read

    atrasado = lido_em - timedelta(minutes=5)
    raw_sent, headers_sent = _signed(_status_payload(WAMID, "sent", atrasado))
    resp = await client.post("/api/v1/webhooks/whatsapp", content=raw_sent, headers=headers_sent)

    assert resp.json() == {"applied": 0}
    assert contato.status is ContactStatus.read
    assert contato.read_at == lido_em


async def test_failed_depois_de_read_nao_apaga_a_leitura(client, session, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", APP_SECRET)
    contato = await _make_contact(session, status=ContactStatus.read, read_at=datetime.now(UTC))
    erro = [{"code": 131026, "title": "Message undeliverable"}]
    raw, headers = _signed(_status_payload(WAMID, "failed", datetime.now(UTC), errors=erro))

    resp = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers=headers)

    assert resp.json() == {"applied": 0}
    assert contato.status is ContactStatus.read
    assert contato.failure_reason is None


async def test_failed_depois_de_sent_registra_o_motivo(client, session, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", APP_SECRET)
    contato = await _make_contact(session)
    erro = [{"code": 131026, "title": "Message undeliverable"}]
    raw, headers = _signed(_status_payload(WAMID, "failed", datetime.now(UTC), errors=erro))

    resp = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers=headers)

    assert resp.json() == {"applied": 1}
    assert contato.status is ContactStatus.failed
    assert contato.failure_reason == "meta_131026: Message undeliverable"


async def test_wamid_desconhecido_e_ignorado_sem_erro(client, session, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", APP_SECRET)
    await _make_contact(session)
    raw, headers = _signed(_status_payload("wamid.DE_OUTRO_AMBIENTE", "read", datetime.now(UTC)))

    resp = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers=headers)

    # 200 de propósito: não-200 faz a Meta reenviar em loop e desinscrever.
    assert resp.status_code == 200
    assert resp.json() == {"applied": 0}
    assert (
        await session.execute(sa.select(sa.func.count()).select_from(OwnerContact))
    ).scalar_one() == 1


async def test_payload_autentico_com_forma_inesperada_nao_quebra(client, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", APP_SECRET)
    raw, headers = _signed({"object": "whatsapp_business_account", "entry": [{"id": "1"}]})

    resp = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"applied": 0}


async def test_fluxo_completo_envio_entrega_leitura(client, session, monkeypatch):
    """Do clique ao "lido": é o caminho que faz delivered_at/read_at existirem."""
    _install(monkeypatch, _ok)
    monkeypatch.setattr(settings, "whatsapp_app_secret", APP_SECRET)
    _, vet, _, hospitalization = await _internacao_com_opt_in(session)

    enviado = await client.post(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts/whatsapp",
        json={"body": "Thor estável."},
        headers=bearer(personal_token(vet)),
    )
    assert enviado.status_code == 201
    contato_id = uuid.UUID(enviado.json()["id"])

    for status in ("delivered", "read"):
        raw, headers = _signed(_status_payload(WAMID, status, datetime.now(UTC)))
        assert (
            await client.post("/api/v1/webhooks/whatsapp", content=raw, headers=headers)
        ).json() == {"applied": 1}

    historico = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/owner-contacts",
        headers=bearer(personal_token(vet)),
    )
    (linha,) = historico.json()
    assert uuid.UUID(linha["id"]) == contato_id
    assert linha["status"] == "read"
    assert linha["sent_at"] and linha["delivered_at"] and linha["read_at"]
