import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_session,
    get_tenant_obj,
    require,
)
from app.core.errors import AppError
from app.models.clinic import Clinic
from app.models.hospitalization import Hospitalization
from app.models.owner import Owner
from app.models.owner_contact import (
    STATUS_ORDER,
    ContactChannel,
    ContactDirection,
    ContactStatus,
    OwnerContact,
)
from app.models.patient import Patient
from app.permissions import OWNER_CONTACT
from app.schemas.owner_contact import (
    OwnerContactCreate,
    OwnerContactOut,
    WhatsAppBulletinRequest,
)
from app.services.audit import ActorInfo, AuditService
from app.services.whatsapp import (
    StatusUpdate,
    WhatsAppError,
    WhatsAppNotConfigured,
    parse_status_updates,
    verify_handshake,
    verify_signature,
    whatsapp_client,
)

#: Router-pai sem prefixo: `main.py` já inclui `owner_contacts.router` e as
#: duas famílias de rota desta entrega vivem em prefixos diferentes (a do
#: plantão é autenticada e por internação; a do webhook é pública e global).
router = APIRouter()

contacts_router = APIRouter(prefix="/api/v1/hospitalizations", tags=["owner-contacts"])
webhooks_router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


async def _owner_of(session: AsyncSession, hospitalization: Hospitalization) -> Owner:
    """Tutor da internação: internação → paciente → tutor.

    O tutor é entidade própria (spec §2): um tutor com 3 animais internados
    tem UM cadastro e UM opt-in de WhatsApp.
    """
    patient = await session.get(Patient, hospitalization.patient_id)
    owner = await session.get(Owner, patient.owner_id)
    if owner is None or owner.clinic_id != hospitalization.clinic_id:
        raise AppError("not_found", 404)
    return owner


@contacts_router.get("/{hospitalization_id}/owner-contacts", response_model=list[OwnerContactOut])
async def list_owner_contacts(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[OwnerContactOut]:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    rows = (
        (
            await session.execute(
                sa.select(OwnerContact)
                .where(
                    OwnerContact.clinic_id == auth.clinic_id,
                    OwnerContact.hospitalization_id == hospitalization.id,
                )
                # NULLS FIRST explícito: a tentativa que falhou não tem
                # `sent_at` e precisa aparecer no topo do histórico: é
                # justamente a que a equipe tem de ver para telefonar.
                .order_by(OwnerContact.sent_at.desc().nullsfirst())
            )
        )
        .scalars()
        .all()
    )
    return [OwnerContactOut.model_validate(row) for row in rows]


@contacts_router.post(
    "/{hospitalization_id}/owner-contacts", response_model=OwnerContactOut, status_code=201
)
async def create_owner_contact(
    hospitalization_id: uuid.UUID,
    payload: OwnerContactCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(OWNER_CONTACT))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OwnerContactOut:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    owner = await _owner_of(session, hospitalization)
    contact = OwnerContact(
        clinic_id=auth.clinic_id,
        hospitalization_id=hospitalization.id,
        owner_id=owner.id,
        channel=payload.channel,
        direction=payload.direction,
        summary=payload.summary,
        # Registro manual descreve um contato que JÁ aconteceu (telefonema,
        # conversa no balcão): aqui `sent_at` é verdade, não promessa.
        status=ContactStatus.sent,
        sent_at=payload.sent_at or datetime.now(UTC),
        membership_id=actor.membership_id,
        author_name=actor.name,
    )
    session.add(contact)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="owner_contact_recorded",
        entity_type="owner_contact",
        entity_id=contact.id,
        after=AuditService.snapshot(contact),
    )
    await session.commit()
    return OwnerContactOut.model_validate(contact)


@contacts_router.post(
    "/{hospitalization_id}/owner-contacts/whatsapp",
    response_model=OwnerContactOut,
    status_code=201,
)
async def send_whatsapp_bulletin(
    hospitalization_id: uuid.UUID,
    payload: WhatsAppBulletinRequest,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(OWNER_CONTACT))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OwnerContactOut:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    owner = await _owner_of(session, hospitalization)
    if owner.whatsapp_opt_in_at is None:
        # Exigência da Meta e da LGPD: sem opt-in registrado não se envia
        # nada. A clínica coleta o aceite no cadastro do tutor.
        raise AppError("whatsapp_opt_in_required", 409)

    patient = await session.get(Patient, hospitalization.patient_id)
    clinic = await session.get(Clinic, auth.clinic_id)

    # Só depois desta chamada o sistema SABE se houve envio. Antes daqui não se
    # grava nada, e o que se grava depois é o que aconteceu de verdade.
    external_id: str | None = None
    failure: str | None = None
    unconfigured = False
    try:
        external_id = await whatsapp_client.send_bulletin(
            owner.phone_e164,
            patient_name=patient.name,
            clinic_name=clinic.name,
            # O texto sai no locale da CLÍNICA (spec §8.3): o idioma do
            # template é o da instituição, não o do aparelho de quem clicou.
            locale=clinic.locale,
            body=payload.body,
        )
    except WhatsAppNotConfigured as exc:
        unconfigured, failure = True, exc.reason
    except WhatsAppError as exc:
        failure = exc.reason

    sent = failure is None
    contact = OwnerContact(
        clinic_id=auth.clinic_id,
        hospitalization_id=hospitalization.id,
        owner_id=owner.id,
        channel=ContactChannel.whatsapp,
        direction=ContactDirection.outbound,
        summary=payload.summary or payload.body,
        status=ContactStatus.sent if sent else ContactStatus.failed,
        sent_at=datetime.now(UTC) if sent else None,
        failure_reason=failure,
        external_id=external_id,
        membership_id=actor.membership_id,
        author_name=actor.name,
    )
    # A tentativa frustrada É história clínica: prova que a equipe tentou
    # avisar o tutor e mostra por que não conseguiu. Some do prontuário só o
    # que nunca foi tentado.
    session.add(contact)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="owner_contact_whatsapp_sent" if sent else "owner_contact_whatsapp_failed",
        entity_type="owner_contact",
        entity_id=contact.id,
        after=AuditService.snapshot(contact),
        extra={"external_id": external_id, "failure_reason": failure},
    )
    await session.commit()

    if failure is not None:
        # Commit ANTES de levantar: a tentativa fica gravada mesmo com a
        # resposta de erro. `failure_reason` não vai nos params: é prosa do
        # provedor, e resposta de erro não carrega prosa (ADR-0004). Quem
        # quiser o detalhe lê o histórico de contatos, que agora o expõe.
        raise AppError(
            "whatsapp_not_configured" if unconfigured else "whatsapp_send_failed",
            503 if unconfigured else 502,
            contact_id=str(contact.id),
        )
    return OwnerContactOut.model_validate(contact)


# ---------------------------------------------------------------------------
# Webhook da Meta: é ele que torna delivered_at/read_at reais
#
# As duas colunas já eram desenhadas na tela e ninguém nunca as escrevia,
# porque não havia quem recebesse o callback de status. O endpoint é PÚBLICO
# por definição (quem chama é a Meta, sem token nosso): a assinatura
# X-Hub-Signature-256 É a autenticação, e sem ela qualquer um na internet
# escreveria "entregue e lido" no prontuário de uma clínica.
# ---------------------------------------------------------------------------


@webhooks_router.get("/whatsapp")
async def verify_whatsapp_webhook(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    """Handshake que a Meta faz ao cadastrar/renovar a inscrição do webhook."""
    if not verify_handshake(hub_mode, hub_verify_token) or hub_challenge is None:
        raise AppError("whatsapp_webhook_unverified", 403)
    # Devolver o desafio CRU é o protocolo da Meta, não prosa da API: é o nonce
    # dela de volta. Envelopado em JSON, a inscrição do webhook é recusada.
    return PlainTextResponse(hub_challenge)


_TIMESTAMP_FIELD = {
    ContactStatus.sent: "sent_at",
    ContactStatus.delivered: "delivered_at",
    ContactStatus.read: "read_at",
}


async def _apply_status(session: AsyncSession, update: StatusUpdate) -> bool:
    """Aplica um callback de status. Devolve se algo realmente mudou."""
    contact = (
        await session.execute(
            sa.select(OwnerContact).where(OwnerContact.external_id == update.external_id)
        )
    ).scalar_one_or_none()
    if contact is None:
        # wamid que não é nosso (outro ambiente apontado para o mesmo número,
        # ou linha já removida). Ignorar é a resposta certa: inventar a linha
        # seria criar contato que nunca existiu.
        return False
    if STATUS_ORDER.get(update.status, -1) <= STATUS_ORDER.get(contact.status, -1):
        # Monotônico e idempotente de uma vez só: o reenvio da Meta reprocessa
        # o mesmo callback sem efeito, e um `sent` atrasado não desfaz o `read`.
        return False

    before = AuditService.snapshot(contact)
    contact.status = ContactStatus(update.status)
    field = _TIMESTAMP_FIELD.get(contact.status)
    if field is not None and getattr(contact, field) is None:
        setattr(contact, field, update.at)
    if contact.status is ContactStatus.failed:
        contact.failure_reason = update.reason
    # `read` sem `delivered` anterior não preenche `delivered_at`: a leitura
    # prova que chegou, mas não diz QUANDO chegou, e carimbar a hora da leitura
    # ali seria registrar um instante que ninguém observou.
    await AuditService.record(
        session,
        clinic_id=contact.clinic_id,
        actor=None,  # quem age é a Meta, não uma pessoa da clínica
        action="owner_contact_status_updated",
        entity_type="owner_contact",
        entity_id=contact.id,
        before=before,
        after=AuditService.snapshot(contact),
        extra={"external_id": update.external_id, "status": update.status},
    )
    return True


@webhooks_router.post("/whatsapp")
async def receive_whatsapp_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> dict[str, int]:
    raw = await request.body()
    if not verify_signature(raw, x_hub_signature_256):
        raise AppError("whatsapp_webhook_unverified", 403)
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    applied = 0
    for update in parse_status_updates(payload):
        if await _apply_status(session, update):
            applied += 1
    await session.commit()
    # 200 mesmo com `applied=0`, inclusive para payload autêntico que não
    # soubemos aproveitar: a Meta reenvia tudo que não devolve 200 e, depois de
    # muitas falhas seguidas, desinscreve o webhook da clínica, e aí delivered_at
    # e read_at param de existir de novo, calados.
    return {"applied": applied}


router.include_router(contacts_router)
router.include_router(webhooks_router)
