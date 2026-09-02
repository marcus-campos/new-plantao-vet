"""Busca e cadastro de paciente: o mesmo schema serve veterinária e saúde humana."""

import pytest

from app.compliance import get_profile
from app.core.errors import AppError
from app.services.patient_search import PatientSearchService, normalize
from tests.factories import make_clinic, make_membership, make_owner, make_patient, make_user
from tests.helpers import bearer, personal_token


async def _vet(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    return clinic, membership


def test_normalize_tira_pontuacao_de_documento():
    assert normalize("123.456.789-00") == "12345678900"
    assert normalize("981 020 000 123 456") == "981020000123456"


def test_perfis_declaram_identificadores_diferentes():
    """Mesma tabela, perfis diferentes: é o que faz o produto servir aos dois."""
    vet = {k.kind for k in get_profile("br").patient_identifier_kinds}
    humano = {k.kind for k in get_profile("br_human").patient_identifier_kinds}
    assert "microchip" in vet and "cpf" not in vet
    assert {"cpf", "cns"} <= humano and "microchip" not in humano


async def test_clinica_veterinaria_recusa_identificador_de_saude_humana(session):
    clinic = await make_clinic(session)  # perfil br (veterinária)
    with pytest.raises(AppError) as exc:
        PatientSearchService.validate_identifier(clinic, "cns", "981020000123456")
    assert exc.value.code == "identifier_kind_not_allowed"


async def test_microchip_invalido_e_recusado(session):
    clinic = await make_clinic(session)
    with pytest.raises(AppError) as exc:
        PatientSearchService.validate_identifier(clinic, "microchip", "abc")
    assert exc.value.code == "identifier_invalid"

    assert PatientSearchService.validate_identifier(clinic, "microchip", "981 020 000 123") == (
        "981020000123"
    )


async def test_cadastrar_paciente_com_tutor_e_microchip(client, session):
    clinic, membership = await _vet(session)
    resp = await client.post(
        "/api/v1/patients/register",
        json={
            "name": "Thor",
            "species": "dog",
            "breed": "SRD",
            "weight_kg": "24.3",
            "identifiers": [{"kind": "microchip", "value": "981.020.000.123456"}],
            "owner_name": "Marina Campos",
            "owner_phone_e164": "+5511999990000",
            "owner_tax_id": "123.456.789-00",
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Thor"


async def test_busca_acha_por_microchip_nome_e_cpf_do_tutor(client, session):
    clinic, membership = await _vet(session)
    token = bearer(personal_token(membership))
    await client.post(
        "/api/v1/patients/register",
        json={
            "name": "Thor",
            "species": "dog",
            "identifiers": [{"kind": "microchip", "value": "981020000123456"}],
            "owner_name": "Marina Campos",
            "owner_phone_e164": "+5511999990000",
            "owner_tax_id": "12345678900",
        },
        headers=token,
    )

    # pelo microchip, com e sem pontuação
    for consulta in ("981020000123456", "981.020.000.123456"):
        achados = (await client.get(f"/api/v1/patients/search?q={consulta}", headers=token)).json()
        assert [hit["name"] for hit in achados] == ["Thor"]
        assert achados[0]["identifiers"][0]["kind"] == "microchip"

    # pelo nome do paciente e pelo nome do tutor
    for consulta in ("tho", "marina"):
        achados = (await client.get(f"/api/v1/patients/search?q={consulta}", headers=token)).json()
        assert [hit["name"] for hit in achados] == ["Thor"]

    # pelo CPF do tutor
    achados = (await client.get("/api/v1/patients/search?q=123.456.789-00", headers=token)).json()
    assert [hit["name"] for hit in achados] == ["Thor"]


async def test_busca_avisa_que_o_paciente_ja_esta_internado(client, session):
    """Quem busca precisa saber se abre a ficha ou interna de novo."""
    from tests.factories import make_hospitalization

    clinic, membership = await _vet(session)
    owner = await make_owner(session, clinic=clinic, name="Marina")
    patient = await make_patient(session, clinic=clinic, owner=owner, name="Nina")
    hosp = await make_hospitalization(session, clinic=clinic, patient=patient)
    await session.flush()

    achados = (
        await client.get(
            "/api/v1/patients/search?q=nina", headers=bearer(personal_token(membership))
        )
    ).json()
    assert achados[0]["active_hospitalization_id"] == str(hosp.id)


async def test_mesmo_microchip_em_dois_pacientes_e_recusado(client, session):
    clinic, membership = await _vet(session)
    token = bearer(personal_token(membership))
    corpo = {
        "species": "dog",
        "identifiers": [{"kind": "microchip", "value": "981020000999999"}],
        "owner_name": "Tutor",
        "owner_phone_e164": "+5511999990000",
    }
    assert (
        await client.post("/api/v1/patients/register", json={**corpo, "name": "Um"}, headers=token)
    ).status_code == 201
    repetido = await client.post(
        "/api/v1/patients/register", json={**corpo, "name": "Outro"}, headers=token
    )
    assert repetido.status_code == 409
    assert repetido.json()["error"]["code"] == "identifier_taken"


async def test_busca_nao_vaza_paciente_de_outra_clinica(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="busca-b")
    owner_b = await make_owner(session, clinic=clinic_b, name="Tutor de outra")
    await make_patient(session, clinic=clinic_b, owner=owner_b, name="Rex")
    await session.flush()

    achados = (
        await client.get(
            "/api/v1/patients/search?q=rex", headers=bearer(personal_token(membership_a))
        )
    ).json()
    assert achados == []


async def test_perfil_da_clinica_diz_ao_front_quais_identificadores_pedir(client, session):
    clinic, membership = await _vet(session)
    body = (
        await client.get("/api/v1/clinic/profile", headers=bearer(personal_token(membership)))
    ).json()
    assert body["profile"] == "br"
    assert body["responsible_label_key"] == "responsible.owner"
    assert [k["kind"] for k in body["patient_identifier_kinds"]] == ["microchip", "rga"]
    assert body["patient_identifier_kinds"][0]["label_key"] == "identifier.microchip"


async def test_perfil_de_saude_humana_muda_a_tela_sem_mudar_o_codigo(client, session):
    clinic = await make_clinic(session, slug="humana")
    clinic.compliance_profile = "br_human"
    _, membership = await _vet(session, clinic)
    body = (
        await client.get("/api/v1/clinic/profile", headers=bearer(personal_token(membership)))
    ).json()
    assert [k["kind"] for k in body["patient_identifier_kinds"]] == ["cpf", "cns", "mrn"]
    assert body["responsible_label_key"] == "responsible.guardian"
    assert body["retention_years"] == 20


async def test_busca_ignora_acento_e_pontuacao(client, session):
    """Ninguém digita acento com a mão na luva, nem ponto no CPF do leitor."""
    clinic, membership = await _vet(session)
    token = bearer(personal_token(membership))
    # O documento entra COM pontuação, como outra tela poderia ter gravado.
    owner = await make_owner(session, clinic=clinic, name="José Antônio")
    owner.tax_id = "987.654.321-00"
    await make_patient(session, clinic=clinic, owner=owner, name="Niña")
    await session.flush()

    for consulta in ("jose", "JOSÉ", "antonio", "nina", "Niña", "98765432100", "987.654.321-00"):
        achados = (await client.get(f"/api/v1/patients/search?q={consulta}", headers=token)).json()
        assert [hit["name"] for hit in achados] == ["Niña"], consulta


async def test_documento_do_responsavel_e_gravado_em_forma_canonica(client, session):
    """O mesmo CPF não pode existir em dois formatos: quebra busca e duplica tutor."""
    clinic, membership = await _vet(session)
    token = bearer(personal_token(membership))

    criado = (
        await client.post(
            "/api/v1/owners",
            json={"name": "Tutor", "phone_e164": "+5511999990000", "tax_id": "123.456.789-00"},
            headers=token,
        )
    ).json()
    assert criado["tax_id"] == "12345678900"

    alterado = (
        await client.patch(
            f"/api/v1/owners/{criado['id']}", json={"tax_id": "987.654.321-00"}, headers=token
        )
    ).json()
    assert alterado["tax_id"] == "98765432100"

    # E o cadastro em um passo grava do mesmo jeito.
    await client.post(
        "/api/v1/patients/register",
        json={
            "name": "Thor",
            "species": "dog",
            "owner_name": "Outro tutor",
            "owner_phone_e164": "+5511999990001",
            "owner_tax_id": "111.222.333-44",
        },
        headers=token,
    )
    achados = (await client.get("/api/v1/patients/search?q=11122233344", headers=token)).json()
    assert [hit["name"] for hit in achados] == ["Thor"]
