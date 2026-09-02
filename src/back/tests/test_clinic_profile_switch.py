"""Escolher a área de atuação da clínica — veterinária ou saúde humana."""

from tests.factories import make_clinic, make_membership, make_owner, make_patient, make_user
from tests.helpers import bearer, personal_token


async def _admin(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="admin")
    return clinic, membership


async def _vet(session, clinic):
    user = await make_user(session)
    return await make_membership(session, clinic=clinic, user=user, role="vet")


async def test_lista_as_areas_disponiveis(client, session):
    clinic, membership = await _admin(session)
    body = (
        await client.get("/api/v1/clinic/profiles", headers=bearer(personal_token(membership)))
    ).json()
    por_nome = {item["profile"]: item for item in body}
    assert {"br", "br_human"} <= set(por_nome)
    assert por_nome["br"]["name_key"] == "compliance.profile.br"
    assert por_nome["br_human"]["retention_years"] == 20


async def test_clinica_sem_dados_troca_para_saude_humana(client, session):
    clinic, membership = await _admin(session)
    token = bearer(personal_token(membership))

    resp = await client.patch(
        "/api/v1/clinic", json={"compliance_profile": "br_human"}, headers=token
    )
    assert resp.status_code == 200
    assert resp.json()["compliance_profile"] == "br_human"

    # A troca precisa chegar às telas: os identificadores pedidos mudam junto.
    profile = (await client.get("/api/v1/clinic/profile", headers=token)).json()
    assert [k["kind"] for k in profile["patient_identifier_kinds"]] == ["cpf", "cns", "mrn"]
    assert profile["responsible_label_key"] == "responsible.guardian"


async def test_area_desconhecida_e_recusada(client, session):
    clinic, membership = await _admin(session)
    resp = await client.patch(
        "/api/v1/clinic",
        json={"compliance_profile": "atlantis"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_clinica_com_microchip_nao_vira_saude_humana_em_silencio(client, session):
    """Trocar aqui deixaria microchip órfão: nenhuma tela do novo perfil o edita."""
    clinic, membership = await _admin(session)
    token = bearer(personal_token(membership))
    vet = await _vet(session, clinic)
    await client.post(
        "/api/v1/patients/register",
        json={
            "name": "Thor",
            "species": "dog",
            "identifiers": [{"kind": "microchip", "value": "981020000123456"}],
            "owner_name": "Tutor",
            "owner_phone_e164": "+5511999990000",
        },
        headers=bearer(personal_token(vet)),
    )

    resp = await client.patch(
        "/api/v1/clinic", json={"compliance_profile": "br_human"}, headers=token
    )
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "compliance_profile_in_use"
    assert body["params"]["kinds"] == ["microchip"]

    # E a clínica continua no que era: recusa não pode ter efeito colateral.
    assert (await client.get("/api/v1/clinic", headers=token)).json()[
        "compliance_profile"
    ] == "br"


async def test_paciente_sem_identificador_nao_impede_a_troca(client, session):
    """Só identificação órfã bloqueia — paciente cadastrado sem chip, não."""
    clinic, membership = await _admin(session)
    owner = await make_owner(session, clinic=clinic, name="Tutor")
    await make_patient(session, clinic=clinic, owner=owner, name="Nina")
    await session.flush()

    resp = await client.patch(
        "/api/v1/clinic",
        json={"compliance_profile": "br_human"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200


async def test_so_admin_troca_a_area(client, session):
    clinic, _ = await _admin(session)
    vet = await _vet(session, clinic)
    resp = await client.patch(
        "/api/v1/clinic",
        json={"compliance_profile": "br_human"},
        headers=bearer(personal_token(vet)),
    )
    assert resp.status_code == 403
