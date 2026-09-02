"""O prontuário em PDF, gerado no servidor.

O que estes testes seguram: que o arquivo é um PDF de verdade (o botão dizia
"Baixar PDF" e chamava `window.print()`), que o timbre da clínica aparece nele
(endereço, telefone e CNPJ eram lidos de campos que não existiam), que as
travas são as MESMAS da rota JSON (porque é o mesmo documento) e que a
leitura fica na trilha em qualquer formato.
"""

import base64
import binascii
import re
import zlib
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import AuditEntry
from app.models.progress_note import ProgressNote
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_patient,
    make_task,
    make_user,
)
from tests.helpers import bearer, personal_token, station_token

_LITERAL = re.compile(rb"\((?:\\.|[^\\()])*\)", re.S)
_OCTAL = re.compile(rb"\\([0-7]{1,3})")
_ESCAPED = {b"\\(": b"(", b"\\)": b")", b"\\\\": b"\\", b"\\n": b"\n", b"\\r": b"\r"}


def _plain(raw: bytes) -> bytes | None:
    """O fluxo de conteúdo legível: o reportlab o escreve em ASCII85+Flate."""
    for decode in (
        lambda data: zlib.decompress(base64.a85decode(data.strip(), adobe=True)),
        zlib.decompress,
    ):
        try:
            return decode(raw)
        except (zlib.error, binascii.Error, ValueError):
            continue
    return None


def pdf_text(payload: bytes) -> str:
    """O texto visível do PDF, sem depender de biblioteca de leitura.

    Junta os literais de string dos fluxos de conteúdo (descomprimindo quando
    o reportlab comprimir). Serve para provar que o timbre e as assinaturas
    chegaram ao papel, não para reconstituir o layout.
    """
    streams = [payload]
    for raw in re.findall(rb"stream\r?\n(.*?)\r?\n?endstream", payload, re.S):
        plain = _plain(raw)
        if plain is not None:
            streams.append(plain)
    parts: list[str] = []
    for stream in streams:
        for literal in _LITERAL.findall(stream):
            text = literal[1:-1]
            text = _OCTAL.sub(lambda match: bytes([int(match.group(1), 8)]), text)
            for escaped, plain in _ESCAPED.items():
                text = text.replace(escaped, plain)
            parts.append(text.decode("cp1252", errors="replace"))
    return "".join(parts)


async def _vet(session, clinic=None, **overrides):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session, name=overrides.pop("name", "Dra. Ana Prado"))
    membership = await make_membership(
        session,
        clinic=clinic,
        user=user,
        role="vet",
        license_number="SP-12345",
        license_authority="CRMV-SP",
        **overrides,
    )
    return clinic, membership


async def _internacao_com_registros(session, clinic, membership):
    patient = await make_patient(session, clinic=clinic, breed="SRD")
    hospitalization = await make_hospitalization(
        session, clinic=clinic, patient=patient, membership=membership
    )
    session.add(
        ProgressNote(
            clinic_id=clinic.id,
            hospitalization_id=hospitalization.id,
            membership_id=membership.id,
            author_name="Dra. Ana Prado",
            author_license="SP-12345",
            author_license_authority="CRMV-SP",
            assessment="Paciente alerta, mucosas normocoradas.",
            signed_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hospitalization,
        title="Dipirona 25 mg/kg IV",
        status="done",
        executed_at=datetime.now(UTC) - timedelta(hours=1),
        executed_by=membership.id,
    )
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hospitalization,
        title="Sondagem",
        status="not_done",
        outcome_reason="refused",
        executed_at=datetime.now(UTC) - timedelta(minutes=30),
        executed_by=membership.id,
    )
    await session.flush()
    return hospitalization


async def test_o_pdf_e_um_pdf_de_verdade(client, session):
    """O botão prometia arquivo e abria o diálogo de impressão do navegador."""
    clinic, membership = await _vet(session)
    hospitalization = await _internacao_com_registros(session, clinic, membership)

    resp = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/record.pdf",
        headers=bearer(personal_token(membership)),
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert f'filename="record-{hospitalization.id}.pdf"' in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF")
    assert resp.content.rstrip().endswith(b"%%EOF")
    # Um PDF de uma página com timbre e duas seções não cabe em 1 kB: o teste
    # falha se a montagem devolver um documento vazio.
    assert len(resp.content) > 2000


async def test_o_documento_traz_timbre_assinatura_e_paginacao(client, session):
    clinic, membership = await _vet(session)
    clinic.address = "Rua das Acacias, 210 - Sao Paulo, SP"
    clinic.phone = "(11) 3456-7890"
    clinic.tax_id = "12.345.678/0001-90"
    hospitalization = await _internacao_com_registros(session, clinic, membership)

    resp = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/record.pdf",
        headers=bearer(personal_token(membership)),
    )
    text = pdf_text(resp.content)

    # Timbre: os três campos que a tela lia de lugar nenhum.
    assert clinic.name in text
    assert "Rua das Acacias, 210 - Sao Paulo, SP" in text
    assert "(11) 3456-7890" in text
    assert "12.345.678/0001-90" in text
    # Nome + registro por evolução (spec §2) e a execução com hora e autor.
    assert "Assinado:" in text
    assert "Dra. Ana Prado" in text
    assert "CRMV-SP SP-12345" in text
    assert "Dipirona 25 mg/kg IV" in text
    # O que NÃO foi feito também é prontuário.
    assert "Sondagem · não realizada" in text
    assert "Paciente recusou" in text
    assert "OCORRÊNCIAS" in text
    # Rodapé: sem "Página N de M" uma folha perdida não teria como aparecer.
    assert "gina 1 de 1" in text
    assert "sem rasuras" in text
    # Nenhum identificador inventado: o id interno sai inteiro e rotulado.
    assert str(hospitalization.id) in text


async def test_clinica_sem_timbre_ainda_entrega_o_documento(client, session):
    """Endereço, telefone e CNPJ são opcionais: sem eles o prontuário sai
    completo, só sem a linha do timbre. Nunca 500."""
    clinic, membership = await _vet(session)
    hospitalization = await _internacao_com_registros(session, clinic, membership)

    resp = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/record.pdf",
        headers=bearer(personal_token(membership)),
    )

    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
    assert clinic.name in pdf_text(resp.content)


async def test_texto_fora_do_winansi_nao_derruba_a_exportacao(client, session):
    """Uma evolução colada do celular com emoji não pode transformar o
    documento entregue ao tutor em erro 500."""
    clinic, membership = await _vet(session)
    hospitalization = await _internacao_com_registros(session, clinic, membership)
    session.add(
        ProgressNote(
            clinic_id=clinic.id,
            hospitalization_id=hospitalization.id,
            membership_id=membership.id,
            author_name="Dra. Ana Prado",
            assessment="Melhora clínica 🐶 & alta prevista <amanhã>",
            signed_at=datetime.now(UTC),
        )
    )
    await session.flush()

    resp = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/record.pdf",
        headers=bearer(personal_token(membership)),
    )

    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
    assert "alta prevista <amanhã>" in pdf_text(resp.content)


async def test_o_include_vale_no_pdf_como_vale_no_json(client, session):
    clinic, membership = await _vet(session)
    hospitalization = await _internacao_com_registros(session, clinic, membership)
    token = bearer(personal_token(membership))

    so_evolucoes = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/record.pdf?include=progress_notes",
        headers=token,
    )
    texto = pdf_text(so_evolucoes.content)
    assert so_evolucoes.status_code == 200
    assert "Paciente alerta" in texto
    # Seção não pedida não vira título vazio no papel.
    assert "Dipirona 25 mg/kg IV" not in texto

    invalido = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/record.pdf?include=segredos",
        headers=token,
    )
    assert invalido.status_code == 422
    assert invalido.json()["error"]["code"] == "validation_error"


async def test_o_pdf_exige_a_mesma_capacidade_do_json(client, session):
    """Mesma trava da rota JSON: é o mesmo documento em outro formato."""
    clinic, membership = await _vet(session)
    hospitalization = await _internacao_com_registros(session, clinic, membership)

    # Estação sem PIN: ninguém identificado não leva o prontuário embora.
    anonimo = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/record.pdf",
        headers=bearer(station_token(clinic)),
    )
    assert anonimo.status_code == 403
    assert anonimo.json()["error"]["code"] == "operator_required"

    # O administrador toca a clínica, não o paciente: não lê prontuário.
    admin_user = await make_user(session)
    admin = await make_membership(session, clinic=clinic, user=admin_user, role="admin")
    await session.flush()
    negado = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/record.pdf",
        headers=bearer(personal_token(admin)),
    )
    assert negado.status_code == 403
    assert negado.json()["error"]["params"]["capability"] == "record.read"


async def test_a_conta_no_pdf_tem_a_mesma_trava_do_json(client, session):
    clinic, membership = await _vet(session)
    hospitalization = await _internacao_com_registros(session, clinic, membership)
    tech_user = await make_user(session)
    tech = await make_membership(session, clinic=clinic, user=tech_user, role="tech")
    await session.flush()
    caminho = f"/api/v1/hospitalizations/{hospitalization.id}/record"
    query = "?include=progress_notes,charges"

    pdf = await client.get(f"{caminho}.pdf{query}", headers=bearer(personal_token(tech)))
    json_ = await client.get(f"{caminho}{query}", headers=bearer(personal_token(tech)))

    assert pdf.status_code == json_.status_code == 403
    assert pdf.json() == json_.json()
    assert pdf.json()["error"]["params"]["capability"] == "charges.read"


async def test_baixar_o_pdf_fica_na_trilha(client, session):
    """Ler o prontuário inteiro deixa rastro em QUALQUER formato: a cadeia
    registrava quem mudou o documento e nunca quem o levou embora."""
    clinic, membership = await _vet(session)
    hospitalization = await _internacao_com_registros(session, clinic, membership)

    resp = await client.get(
        f"/api/v1/hospitalizations/{hospitalization.id}/record.pdf",
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200

    entries = (
        (
            await session.execute(
                sa.select(AuditEntry).where(
                    AuditEntry.clinic_id == clinic.id,
                    AuditEntry.action == "record_read",
                    AuditEntry.entity_id == hospitalization.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(entries) == 1
    assert entries[0].actor_name == "Dra. Ana Prado"
    assert entries[0].payload["extra"]["format"] == "pdf"
    assert entries[0].payload["extra"]["sections"] == ["prescriptions", "progress_notes", "tasks"]


async def test_pdf_de_outra_clinica_nao_existe(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b, membership_b = await _vet(session, clinic=await make_clinic(session))
    hospitalization_b = await _internacao_com_registros(session, clinic_b, membership_b)

    resp = await client.get(
        f"/api/v1/hospitalizations/{hospitalization_b.id}/record.pdf",
        headers=bearer(personal_token(membership_a)),
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
