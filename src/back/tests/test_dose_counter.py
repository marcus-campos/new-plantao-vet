"""Contador de dose acumulada por fármaco.

A pesquisa (§5.8) aponta isto como dor explícita e não resolvida do concorrente,
com 51% dos erros de medicação sendo dose errada. A spec §2 já dizia que
`details.drug` normalizado existe "para o contador de dose agregar" — e nada
agregava. O caso que estes testes protegem é o `partial`: meia dose contada como
dose inteira faz o total mentir para mais, justo no número que o vet usa para
decidir a próxima.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_prescription,
    make_task,
    make_user,
)
from tests.helpers import bearer, personal_token


async def _contadores(client, membership, hosp) -> dict[str, dict]:
    resp = await client.get(
        f"/api/v1/hospitalizations/{hosp.id}", headers=bearer(personal_token(membership))
    )
    assert resp.status_code == 200
    return {row["drug"]: row for row in resp.json()["drug_doses"]}


async def _cenario(session):
    clinic = await make_clinic(session)
    membership = await make_membership(
        session, clinic=clinic, user=await make_user(session), role="vet"
    )
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    return clinic, membership, hosp


async def test_soma_24h_e_internacao_inteira(client, session):
    clinic, membership, hosp = await _cenario(session)
    agora = datetime.now(UTC)
    prescription = await make_prescription(
        session,
        clinic=clinic,
        hospitalization=hosp,
        name="Dipirona 25 mg/kg IV",
        details={"drug": "dipirona", "dose": "25 mg/kg", "route": "IV"},
    )
    # Uma dose de anteontem e duas de hoje: o total da internação inclui as
    # três, o de 24h só as duas.
    for horas in (30, 6, 1):
        await make_task(
            session,
            clinic=clinic,
            hospitalization=hosp,
            prescription_id=prescription.id,
            status="done",
            scheduled_for=agora - timedelta(hours=horas),
            executed_at=agora - timedelta(hours=horas),
        )

    linha = (await _contadores(client, membership, hosp))["dipirona"]
    assert linha["count_24h"] == 2
    assert linha["count_total"] == 3
    assert Decimal(str(linha["dose_sum_24h"])) == Decimal("50")
    assert Decimal(str(linha["dose_sum_total"])) == Decimal("75")
    assert linha["dose_unit"] == "mg/kg"


async def test_dose_parcial_nao_conta_como_dose_inteira(client, session):
    clinic, membership, hosp = await _cenario(session)
    agora = datetime.now(UTC)
    prescription = await make_prescription(
        session,
        clinic=clinic,
        hospitalization=hosp,
        name="Ondansetrona",
        details={"drug": "ondansetrona", "dose": "0,5 mg/kg"},
    )
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        prescription_id=prescription.id,
        status="done",
        executed_at=agora - timedelta(hours=2),
    )
    # Vomitou metade: a administração aconteceu (conta como contato com o
    # fármaco), mas o que entrou no animal foi meia dose.
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        prescription_id=prescription.id,
        status="partial",
        executed_at=agora - timedelta(hours=1),
        values={"dose_given": "0.5"},
    )

    linha = (await _contadores(client, membership, hosp))["ondansetrona"]
    assert linha["count_24h"] == 2
    assert Decimal(str(linha["dose_sum_24h"])) == Decimal("0.75")


async def test_execucao_nao_realizada_nao_entra_no_contador(client, session):
    clinic, membership, hosp = await _cenario(session)
    prescription = await make_prescription(
        session,
        clinic=clinic,
        hospitalization=hosp,
        details={"drug": "dipirona", "dose": "25 mg/kg"},
    )
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        prescription_id=prescription.id,
        status="not_done",
        outcome_reason="refused",
        executed_at=datetime.now(UTC),
    )
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        prescription_id=prescription.id,
        status="pending",
    )
    # O que o animal recusou não é dose recebida: incluir falsearia o número
    # que decide a próxima administração.
    assert await _contadores(client, membership, hosp) == {}


async def test_dose_ilegivel_conta_sem_somar(client, session):
    clinic, membership, hosp = await _cenario(session)
    prescription = await make_prescription(
        session,
        clinic=clinic,
        hospitalization=hosp,
        name="Metadona",
        details={"drug": "metadona", "dose": "a critério"},
    )
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        prescription_id=prescription.id,
        status="done",
        executed_at=datetime.now(UTC),
    )
    linha = (await _contadores(client, membership, hosp))["metadona"]
    assert linha["count_total"] == 1
    # Contagem sem total, nunca um total inventado.
    assert linha["dose_sum_total"] is None
    assert linha["dose_unit"] is None


async def test_unidades_diferentes_no_mesmo_farmaco_nao_sao_somadas(client, session):
    clinic, membership, hosp = await _cenario(session)
    agora = datetime.now(UTC)
    for dose in ("2 mg", "0,1 mg/kg"):
        prescription = await make_prescription(
            session,
            clinic=clinic,
            hospitalization=hosp,
            name=f"Metadona {dose}",
            details={"drug": "metadona", "dose": dose},
        )
        await make_task(
            session,
            clinic=clinic,
            hospitalization=hosp,
            prescription_id=prescription.id,
            status="done",
            executed_at=agora,
        )
    linha = (await _contadores(client, membership, hosp))["metadona"]
    assert linha["count_total"] == 2
    # Somar mg com mg/kg daria um número que não significa nada.
    assert linha["dose_sum_total"] is None


async def test_farmaco_agrega_mesmo_com_caixa_e_espaco_diferentes(client, session):
    clinic, membership, hosp = await _cenario(session)
    agora = datetime.now(UTC)
    for drug in ("dipirona", "Dipirona "):
        prescription = await make_prescription(
            session,
            clinic=clinic,
            hospitalization=hosp,
            details={"drug": drug, "dose": "25 mg/kg"},
        )
        await make_task(
            session,
            clinic=clinic,
            hospitalization=hosp,
            prescription_id=prescription.id,
            status="done",
            executed_at=agora,
        )
    contadores = await _contadores(client, membership, hosp)
    # Duas linhas para o mesmo fármaco seriam duas metades da verdade.
    assert list(contadores) == ["dipirona"]
    assert contadores["dipirona"]["count_total"] == 2


async def test_tarefa_sem_farmaco_nao_vira_linha_do_contador(client, session):
    clinic, membership, hosp = await _cenario(session)
    prescription = await make_prescription(
        session,
        clinic=clinic,
        hospitalization=hosp,
        category="monitoring",
        name="TPR",
        details={"vitals": ["temperature_c"]},
    )
    await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        prescription_id=prescription.id,
        category="monitoring",
        status="done",
        executed_at=datetime.now(UTC),
    )
    assert await _contadores(client, membership, hosp) == {}
