"""Quem pode o quê. Atos privativos do profissional habilitado e política da clínica."""

import pytest

from app import permissions
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_prescription,
    make_task,
    make_user,
)
from tests.helpers import bearer, operator_token, personal_token, station_token


async def _quem(session, clinic, role):
    user = await make_user(session)
    return await make_membership(session, clinic=clinic, user=user, role=role)


@pytest.mark.parametrize("capability", sorted(permissions.LICENSED_ONLY))
def test_ato_privativo_e_exclusivo_do_profissional_habilitado(capability):
    """Prescrever, evoluir e dar alta não são delegáveis: é lei, não preferência.

    Se algum dia alguém acrescentar esses atos ao técnico ou ao administrador,
    este teste quebra antes de a clínica descobrir na fiscalização."""
    for role in ("tech", "admin"):
        assert not permissions.can(role, capability), f"{role} não pode {capability}"
    assert permissions.can(permissions.LICENSED_ROLE, capability)


def test_ninguem_sem_papel_pode_nada():
    assert permissions.capabilities_of(None) == frozenset()
    assert permissions.capabilities_of("visitante") == frozenset()


async def test_tecnico_nao_prescreve(client, session):
    clinic = await make_clinic(session)
    vet = await _quem(session, clinic, "vet")
    tech = await _quem(session, clinic, "tech")
    hosp = await make_hospitalization(session, clinic=clinic, membership=vet)
    await session.flush()

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "category": "medication",
            "kind": "recurring",
            "name": "Dipirona",
            "frequency_minutes": 480,
        },
        headers=bearer(personal_token(tech)),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    assert resp.json()["error"]["params"]["capability"] == "prescription.create"


async def test_tecnico_nao_da_alta(client, session):
    clinic = await make_clinic(session)
    vet = await _quem(session, clinic, "vet")
    tech = await _quem(session, clinic, "tech")
    hosp = await make_hospitalization(session, clinic=clinic, membership=vet)
    await session.flush()

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        json={"outcome": "discharged", "note": "alta clínica"},
        headers=bearer(personal_token(tech)),
    )
    assert resp.status_code == 403


async def test_tecnico_executa_tarefa(client, session):
    """O que o técnico FAZ continua aberto: administrar é o trabalho dele."""
    clinic = await make_clinic(session)
    vet = await _quem(session, clinic, "vet")
    tech = await _quem(session, clinic, "tech")
    hosp = await make_hospitalization(session, clinic=clinic, membership=vet)
    prescription = await make_prescription(session, clinic=clinic, hospitalization=hosp)
    task = await make_task(
        session, clinic=clinic, hospitalization=hosp, prescription_id=prescription.id
    )
    await session.flush()

    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={}, headers=bearer(personal_token(tech))
    )
    assert resp.status_code == 200


async def test_administrador_nao_prescreve_nem_executa(client, session):
    """Ser dono da clínica não dá registro no conselho."""
    clinic = await make_clinic(session)
    vet = await _quem(session, clinic, "vet")
    admin = await _quem(session, clinic, "admin")
    hosp = await make_hospitalization(session, clinic=clinic, membership=vet)
    prescription = await make_prescription(session, clinic=clinic, hospitalization=hosp)
    task = await make_task(
        session, clinic=clinic, hospitalization=hosp, prescription_id=prescription.id
    )
    await session.flush()
    headers = bearer(personal_token(admin))

    assert (
        await client.post(
            f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
            json={
                "category": "medication",
                "kind": "recurring",
                "name": "Dipirona",
                "frequency_minutes": 480,
            },
            headers=headers,
        )
    ).status_code == 403
    assert (
        await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    ).status_code == 403


async def test_estacao_usa_o_papel_de_quem_digitou_o_pin(client, session):
    """O celular compartilhado não tem papel: quem responde é o dono do PIN.

    Sem isto, o técnico prescreveria pela estação e o sistema registraria o
    ato como se o aparelho tivesse feito."""
    clinic = await make_clinic(session)
    vet = await _quem(session, clinic, "vet")
    tech = await _quem(session, clinic, "tech")
    hosp = await make_hospitalization(session, clinic=clinic, membership=vet)
    await session.flush()

    corpo = {
        "category": "medication",
        "kind": "recurring",
        "name": "Dipirona",
        "frequency_minutes": 480,
    }
    estacao = bearer(station_token(clinic))

    negado = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json=corpo,
        headers={**estacao, "X-Operator-Token": operator_token(tech)},
    )
    assert negado.status_code == 403
    assert negado.json()["error"]["params"]["role"] == "tech"

    permitido = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json=corpo,
        headers={**estacao, "X-Operator-Token": operator_token(vet)},
    )
    assert permitido.status_code == 201


async def test_me_diz_o_que_a_pessoa_pode(client, session):
    """A interface precisa saber o que esconder; senão oferece botão que dá 403."""
    clinic = await make_clinic(session)
    tech = await _quem(session, clinic, "tech")
    await session.flush()

    corpo = (await client.get("/api/v1/auth/me", headers=bearer(personal_token(tech)))).json()
    assert corpo["role"] == "tech"
    assert "task.execute" in corpo["capabilities"]
    assert "prescription.create" not in corpo["capabilities"]


# --------------------------------------------------------------------------
# Leitura. Antes desta seção, NENHUMA leitura do sistema tinha capacidade: um
# tablet logado na clínica, sem ninguém identificado, lia CPF e telefone de
# todo tutor, o extrato inteiro e o prontuário completo, enquanto a interface
# escondia os itens de menu e dizia ao usuário que aquilo era do administrador.
# --------------------------------------------------------------------------


async def test_estacao_sem_pin_nao_le_dado_de_tutor(client, session):
    clinic = await make_clinic(session)
    resp = await client.get("/api/v1/owners", headers=bearer(station_token(clinic)))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "operator_required"


async def test_estacao_com_pin_le_dado_de_tutor(client, session):
    """Com alguém identificado, a leitura acontece: o modo estação existe para
    a equipe trabalhar num aparelho compartilhado, não para bloquear a rotina."""
    clinic = await make_clinic(session)
    tech = await _quem(session, clinic, "tech")
    await session.flush()
    resp = await client.get(
        "/api/v1/owners",
        headers={
            **bearer(station_token(clinic)),
            "X-Operator-Token": operator_token(tech),
        },
    )
    assert resp.status_code == 200


async def test_tecnico_nao_le_a_conta(client, session):
    """A tabela de papéis nega `charges.read` ao técnico desde sempre, e o
    extrato era leitura aberta, então ele via cada centavo e exportava em CSV."""
    clinic = await make_clinic(session)
    tech = await _quem(session, clinic, "tech")
    hosp = await make_hospitalization(session, clinic=clinic)
    await session.flush()

    resp = await client.get(
        f"/api/v1/hospitalizations/{hosp.id}/charges", headers=bearer(personal_token(tech))
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    assert resp.json()["error"]["params"]["capability"] == "charges.read"


async def test_vet_le_a_conta(client, session):
    clinic = await make_clinic(session)
    vet = await _quem(session, clinic, "vet")
    hosp = await make_hospitalization(session, clinic=clinic)
    await session.flush()
    resp = await client.get(
        f"/api/v1/hospitalizations/{hosp.id}/charges", headers=bearer(personal_token(vet))
    )
    assert resp.status_code == 200


async def test_tecnico_nao_le_a_lista_da_equipe(client, session):
    """`GET /memberships` devolve e-mail e quem tem PIN. Quem só precisa de
    nomes tem `/memberships/roster`, que continua aberto a todo membro."""
    clinic = await make_clinic(session)
    tech = await _quem(session, clinic, "tech")
    await session.flush()
    headers = bearer(personal_token(tech))

    negado = await client.get("/api/v1/memberships", headers=headers)
    assert negado.status_code == 403
    assert negado.json()["error"]["params"]["capability"] == "team.read"

    liberado = await client.get("/api/v1/memberships/roster", headers=headers)
    assert liberado.status_code == 200
    assert all("email" not in item for item in liberado.json())


async def test_o_prontuario_exige_identificacao_e_fica_na_trilha(client, session):
    """Documento regulado pelo CFMV: sai inteiro, então quem o abriu fica
    registrado. A cadeia gravava quem MUDOU o prontuário e nunca quem o leu."""
    clinic = await make_clinic(session)
    vet = await _quem(session, clinic, "vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=vet)
    await session.flush()

    anonimo = await client.get(
        f"/api/v1/hospitalizations/{hosp.id}/record", headers=bearer(station_token(clinic))
    )
    assert anonimo.status_code == 403
    assert anonimo.json()["error"]["code"] == "operator_required"

    lido = await client.get(
        f"/api/v1/hospitalizations/{hosp.id}/record", headers=bearer(personal_token(vet))
    )
    assert lido.status_code == 200

    trilha = (
        await client.get(
            f"/api/v1/audit?entity_type=hospitalization&entity_id={hosp.id}",
            headers=bearer(personal_token(vet)),
        )
    ).json()
    assert any(item["action"] == "record_read" for item in trilha["items"])


async def test_a_conta_nao_entra_no_prontuario_de_quem_nao_le_conta(client, session):
    clinic = await make_clinic(session)
    tech = await _quem(session, clinic, "tech")
    hosp = await make_hospitalization(session, clinic=clinic)
    await session.flush()
    resp = await client.get(
        f"/api/v1/hospitalizations/{hosp.id}/record?include=progress_notes,charges",
        headers=bearer(personal_token(tech)),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["params"]["capability"] == "charges.read"


async def test_operator_diz_o_que_o_dono_do_pin_pode(client, session):
    """O buraco do modo estação: `/auth/me` devolve papel nulo de propósito, e o
    cliente concluía "posso tudo". Agora existe a pergunta certa: o que pode
    quem acabou de digitar o PIN."""
    clinic = await make_clinic(session)
    tech = await _quem(session, clinic, "tech")
    await session.flush()

    sem_pin = await client.get("/api/v1/auth/operator", headers=bearer(station_token(clinic)))
    assert sem_pin.status_code == 403
    assert sem_pin.json()["error"]["code"] == "operator_required"

    com_pin = await client.get(
        "/api/v1/auth/operator",
        headers={**bearer(station_token(clinic)), "X-Operator-Token": operator_token(tech)},
    )
    assert com_pin.status_code == 200
    corpo = com_pin.json()
    assert corpo["role"] == "tech"
    assert "task.execute" in corpo["capabilities"]
    assert "prescription.create" not in corpo["capabilities"]
    assert "charges.read" not in corpo["capabilities"]


async def test_tecnico_abre_box(client, session):
    """`kennel.manage` é operação do plantão, não configuração: o próprio
    módulo de permissões diz que o profissional de madrugada precisa abrir um
    box sem esperar o administrador. Às 3h quem está ao lado do box é o técnico,
    e era o único papel sem a capacidade."""
    clinic = await make_clinic(session)
    tech = await _quem(session, clinic, "tech")
    await session.flush()
    resp = await client.post(
        "/api/v1/kennels",
        json={"name": "Isolamento 2"},
        headers=bearer(personal_token(tech)),
    )
    assert resp.status_code == 201
