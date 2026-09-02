"""Sinais vitais: a grade de monitoramento como dado de primeira classe.

O que estes testes travam é a fronteira entre as duas faixas. Fora da faixa de
REFERÊNCIA o valor é gravado: é o achado clínico. Fora do limite FISIOLÓGICO é
recusado: é erro de digitação. Trocar as duas faz o sistema recusar o registro
de um animal grave, que é a pior coisa que uma ficha pode fazer.
"""

from app import vitals
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_patient,
    make_prescription,
    make_task,
    make_user,
)
from tests.helpers import bearer, personal_token


async def _cenario(session, *, species="Canino", declared=("temperature_c", "pain_score")):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    patient = await make_patient(session, clinic=clinic, species=species)
    hosp = await make_hospitalization(session, clinic=clinic, patient=patient)
    prescription = await make_prescription(
        session,
        clinic=clinic,
        hospitalization=hosp,
        category="monitoring",
        name="TPR + escore de dor",
        details={"vitals": list(declared)},
        frequency_minutes=240,
    )
    task = await make_task(
        session,
        clinic=clinic,
        hospitalization=hosp,
        prescription_id=prescription.id,
        category="monitoring",
        title="TPR + escore de dor",
        price_minor=None,
    )
    return clinic, membership, hosp, prescription, task


# ---- o registro em si -----------------------------------------------------


def test_faixa_de_referencia_difere_entre_cao_e_gato():
    fc = vitals.get_vital("heart_rate_bpm")
    assert vitals.reference_for(fc, "Canino") != vitals.reference_for(fc, "Felino")
    assert vitals.reference_for(fc, "Felino").low == 140


def test_especie_digitada_em_portugues_encontra_a_faixa():
    # `patients.species` é texto livre digitado na admissão: casar por
    # igualdade com "dog" devolveria faixa nenhuma para todo paciente real.
    assert vitals.normalize_species("Canino") == vitals.DOG
    assert vitals.normalize_species("Cão") == vitals.DOG
    assert vitals.normalize_species("felino") == vitals.CAT


def test_especie_desconhecida_nao_herda_a_faixa_do_cao():
    temperatura = vitals.get_vital("temperature_c")
    assert vitals.normalize_species("Papagaio") is None
    assert vitals.reference_for(temperatura, "Papagaio") is None
    # Mas o limite fisiológico continua valendo: 385 °C não é febre de exótico.
    assert temperatura.plausible is not None


def test_tipo_desconhecido_nao_derruba_a_leitura():
    assert vitals.get_vital("temperatuar") is None
    assert vitals.declared_kinds({"vitals": ["temperature_c", 7, None]}) == ("temperature_c",)
    assert vitals.declared_kinds({}) == ()


# ---- captura pela execução da tarefa --------------------------------------


async def test_vital_declarado_valida_e_faz_round_trip(client, session):
    clinic, membership, hosp, prescription, task = await _cenario(session)
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"values": {"temperature_c": 39.1, "pain_score": 1}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert resp.json()["values"] == {"temperature_c": 39.1, "pain_score": 1}

    # E continua lá na leitura da ficha, não só na resposta do POST.
    ficha = await client.get(
        f"/api/v1/hospitalizations/{hosp.id}", headers=bearer(personal_token(membership))
    )
    registrada = [row for row in ficha.json()["tasks"] if row["id"] == str(task.id)][0]
    assert registrada["values"]["temperature_c"] == 39.1


async def test_valor_fora_da_faixa_de_referencia_e_registrado(client, session):
    clinic, membership, hosp, prescription, task = await _cenario(session)
    # 41,2 °C é febre alta, o achado que a ficha existe para capturar.
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"values": {"temperature_c": 41.2, "pain_score": 4}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert resp.json()["values"]["temperature_c"] == 41.2
    assert resp.json()["status"] == "done"


async def test_valor_nao_numerico_e_recusado(client, session):
    clinic, membership, hosp, prescription, task = await _cenario(session)
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"values": {"temperature_c": "trinta e nove"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert resp.json()["error"]["params"] == {
        "field": "values.temperature_c",
        "rule": "not_numeric",
    }


async def test_valor_fora_do_limite_fisiologico_e_recusado(client, session):
    clinic, membership, hosp, prescription, task = await _cenario(session)
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"values": {"temperature_c": 385}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["params"]["rule"] == "out_of_physiological_range"


async def test_escore_de_dor_fora_da_escala_e_recusado(client, session):
    # A escala CSU vai de 0 a 4: "7" não é dor pior, é escala errada.
    clinic, membership, hosp, prescription, task = await _cenario(session)
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"values": {"pain_score": 7}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["params"]["field"] == "values.pain_score"


async def test_chave_com_erro_de_digitacao_nao_entra_no_prontuario(client, session):
    clinic, membership, hosp, prescription, task = await _cenario(session)
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"values": {"temperatuar": 39.1}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["params"]["rule"] == "unknown_vital"


async def test_vital_fora_da_grade_declarada_e_recusado(client, session):
    clinic, membership, hosp, prescription, task = await _cenario(
        session, declared=("temperature_c",)
    )
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"values": {"glucose_mg_dl": 82}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["params"]["rule"] == "vital_not_declared"


async def test_texto_livre_continua_aceito_ao_lado_da_grade(client, session):
    clinic, membership, hosp, prescription, task = await _cenario(session)
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"values": {"temperature_c": 38.4, "note": "mucosa rosada"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert resp.json()["values"]["note"] == "mucosa rosada"


async def test_tarefa_sem_grade_declarada_ainda_confere_o_que_e_vital(client, session):
    """Prescrição antiga, sem `details["vitals"]`: o jsonb segue aberto, mas um
    número que se diz temperatura continua tendo de ser um número."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    hosp = await make_hospitalization(session, clinic=clinic)
    task = await make_task(session, clinic=clinic, hospitalization=hosp)
    headers = bearer(personal_token(membership))

    livre = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"values": {"observacao_da_clinica": "sem intercorrência"}},
        headers=headers,
    )
    assert livre.status_code == 200

    outra = await make_task(session, clinic=clinic, hospitalization=hosp)
    invalido = await client.post(
        f"/api/v1/tasks/{outra.id}/execute",
        json={"values": {"temperature_c": "38,4"}},
        headers=headers,
    )
    assert invalido.status_code == 422


# ---- a faixa exposta ao cliente -------------------------------------------


async def test_ficha_expoe_a_faixa_da_especie_do_paciente(client, session):
    clinic, membership, hosp, prescription, task = await _cenario(session, species="Felino")
    resp = await client.get(
        f"/api/v1/hospitalizations/{hosp.id}", headers=bearer(personal_token(membership))
    )
    assert resp.status_code == 200
    por_tipo = {row["kind"]: row for row in resp.json()["vitals"]}
    # É o número do mockup AppTarefa: "82 mg/dL · faixa de referência 70–150".
    assert por_tipo["glucose_mg_dl"]["reference_low"] == 70
    assert por_tipo["glucose_mg_dl"]["reference_high"] == 150
    assert por_tipo["glucose_mg_dl"]["unit"] == "mg/dL"
    assert por_tipo["temperature_c"]["label_key"] == "vital.temperature_c"
    assert por_tipo["mucous_membrane"]["value_type"] == "choice"


async def test_ficha_de_exotico_mostra_o_campo_sem_faixa(client, session):
    clinic, membership, hosp, prescription, task = await _cenario(session, species="Papagaio")
    resp = await client.get(
        f"/api/v1/hospitalizations/{hosp.id}", headers=bearer(personal_token(membership))
    )
    por_tipo = {row["kind"]: row for row in resp.json()["vitals"]}
    assert por_tipo["temperature_c"]["reference_low"] is None
    # A funcionalidade fica visivelmente ausente, não errada: o limite
    # fisiológico continua, a faixa da espécie não é inventada.
    assert por_tipo["temperature_c"]["min_value"] == 25.0
