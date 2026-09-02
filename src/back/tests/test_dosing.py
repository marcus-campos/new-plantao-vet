"""A calculadora de dose.

**51% dos erros de medicação são de dose** (pesquisa §5.8) e a calculadora está
na lista de paridade obrigatória do mercado (§2.7): todo concorrente tem, e aqui
o veterinário digitava o resultado de uma conta feita de cabeça.

Estes testes fixam as decisões que NÃO são de engenharia: as que, se alguém
mudar sem querer, viram erro de medicação.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.models.dose_rule import DoseRule
from app.models.price_list_item import PriceListItem
from app.services.dosing import DosingService
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_patient,
    make_user,
)
from tests.helpers import bearer, personal_token


def _item(**overrides) -> PriceListItem:
    valores = {
        "clinic_id": uuid.uuid4(),
        "name": "Ondansetrona 2 mg/ml",
        "category": "medication",
        "unit": "por dose",
        "price_minor": 2200,
        "concentration_mg_per_ml": Decimal("2"),
        **overrides,
    }
    return PriceListItem(**valores)


def _rule(**overrides) -> DoseRule:
    valores = {
        "id": uuid.uuid4(),
        "clinic_id": uuid.uuid4(),
        "price_list_item_id": uuid.uuid4(),
        "dose_default_per_kg": Decimal("0.15"),
        "dose_min_per_kg": Decimal("0.1"),
        "dose_max_per_kg": Decimal("0.2"),
        "reviewed_at": datetime.now(UTC),
        "reviewed_by_name": "Dra. Paula Martins",
        **overrides,
    }
    return DoseRule(**valores)


def test_a_conta_padrao():
    """dose_mg = mg/kg × peso; volume_ml = dose_mg ÷ concentração."""
    calc = DosingService.calculate(
        rule=_rule(), item=_item(), weight_kg=Decimal("3.6"), species="Felino"
    )
    assert calc.dose_per_kg == Decimal("0.15")
    assert calc.dose_mg == Decimal("0.5400")
    assert calc.volume_ml == Decimal("0.270")
    assert calc.warnings == []
    assert calc.reviewed is True


def test_a_conta_acompanha_o_valor_digitado_pelo_veterinario():
    """O vet sempre pode digitar outro valor, e a conta o segue."""
    calc = DosingService.calculate(
        rule=_rule(),
        item=_item(),
        weight_kg=Decimal("10"),
        dose_per_kg_override=Decimal("0.5"),
    )
    assert calc.dose_mg == Decimal("5.0000")
    assert calc.volume_ml == Decimal("2.500")
    # Fora da faixa AVISA, nunca bloqueia: fricção sem valor clínico percebido é
    # contornada, e o sistema passa a mentir (pesquisa §4).
    assert "above_range" in calc.warnings


def test_dose_fixa_por_animal_nao_multiplica_pelo_peso():
    """Clorfeniramina 1–2 mg/gato, atenolol 6,25–12,5 mg/gato.

    Multiplicar dose por animal pelo peso É o erro de dose que esta coluna
    existe para evitar: num gato de 4 kg daria quatro vezes a dose."""
    calc = DosingService.calculate(
        rule=_rule(dose_default_per_kg=None, fixed_dose_mg=Decimal("2")),
        item=_item(concentration_mg_per_ml=Decimal("4")),
        weight_kg=Decimal("4"),
        species="Felino",
    )
    assert calc.dose_mg == Decimal("2")
    assert calc.volume_ml == Decimal("0.500")
    assert "fixed_dose" in calc.warnings


def test_teto_absoluto_vale():
    """Alguns fármacos não escalam linearmente com o peso."""
    calc = DosingService.calculate(
        rule=_rule(dose_default_per_kg=Decimal("1"), max_total_mg=Decimal("20")),
        item=_item(),
        weight_kg=Decimal("40"),
    )
    assert calc.dose_mg == Decimal("20")
    assert "capped" in calc.warnings


def test_contraindicacao_de_especie_avisa_e_nao_bloqueia():
    """Carprofeno é fatal em gato; o gato não tem várias vias de glicuronidação
    hepática que o cão usa. Mas quem decide é quem tem registro no conselho: o
    sistema avisa e registra."""
    calc = DosingService.calculate(
        rule=_rule(is_contraindicated=True, warning="Fatal em felinos.", species="Felino"),
        item=_item(),
        weight_kg=Decimal("4"),
        species="Felino",
    )
    assert "contraindicated" in calc.warnings
    assert "Fatal em felinos." in calc.notes
    # E a conta continua saindo: bloquear empurraria a dose para o papel.
    assert calc.dose_mg is not None


def test_raca_sensivel_avisa():
    """ABCB1-1∆ (MDR1) em raças pastoreiras compromete a glicoproteína-P na
    barreira hematoencefálica. Raça é texto livre, então casamos por texto."""
    calc = DosingService.calculate(
        rule=_rule(breeds="Collie, Pastor Australiano, Border Collie", breed_warning="MDR1"),
        item=_item(),
        weight_kg=Decimal("20"),
        breed="Border Collie",
    )
    assert "breed_sensitivity" in calc.warnings
    assert "MDR1" in calc.notes


def test_regra_nao_conferida_nao_e_apresentada_como_verdade():
    """O sistema não pode afirmar uma dose que nenhum veterinário assinou."""
    calc = DosingService.calculate(
        rule=_rule(reviewed_at=None, reviewed_by_name=None),
        item=_item(),
        weight_kg=Decimal("10"),
    )
    assert calc.reviewed is False
    assert "unreviewed_rule" in calc.warnings


def test_sem_peso_e_sem_concentracao_diz_o_que_falta():
    sem_peso = DosingService.calculate(rule=_rule(), item=_item(), weight_kg=None)
    assert "no_weight" in sem_peso.warnings
    assert sem_peso.dose_mg is None

    sem_conc = DosingService.calculate(
        rule=_rule(), item=_item(concentration_mg_per_ml=None), weight_kg=Decimal("10")
    )
    assert sem_conc.dose_mg == Decimal("1.5000")
    assert sem_conc.volume_ml is None
    assert "no_concentration" in sem_conc.warnings


async def test_a_regra_da_especie_vence_a_generica(session):
    clinic = await make_clinic(session)
    item = PriceListItem(
        clinic_id=clinic.id,
        name="Dipirona 500 mg/ml",
        category="medication",
        unit="por dose",
        price_minor=1800,
        concentration_mg_per_ml=Decimal("500"),
    )
    session.add(item)
    await session.flush()
    session.add_all(
        [
            DoseRule(
                clinic_id=clinic.id,
                price_list_item_id=item.id,
                species=None,
                dose_default_per_kg=Decimal("25"),
            ),
            DoseRule(
                clinic_id=clinic.id,
                price_list_item_id=item.id,
                species="Felino",
                dose_default_per_kg=Decimal("12.5"),
            ),
        ]
    )
    await session.flush()

    felino = await DosingService.rule_for(
        session, clinic_id=clinic.id, price_list_item_id=item.id, species="Felino"
    )
    assert felino.dose_default_per_kg == Decimal("12.5")

    canino = await DosingService.rule_for(
        session, clinic_id=clinic.id, price_list_item_id=item.id, species="Canino"
    )
    assert canino.dose_default_per_kg == Decimal("25"), "sem regra da espécie, cai na genérica"


async def test_preview_calcula_para_o_paciente_da_internacao(client, session):
    """O sistema já sabe o peso e a espécie. Perguntar de novo o que ele sabe é
    pedir ao veterinário uma conta que a máquina faz certo."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    patient = await make_patient(session, clinic=clinic, species="Felino")
    patient.weight_kg = Decimal("3.6")
    hosp = await make_hospitalization(
        session, clinic=clinic, patient=patient, membership=membership
    )
    item = PriceListItem(
        clinic_id=clinic.id,
        name="Ondansetrona 2 mg/ml",
        category="medication",
        unit="por dose",
        price_minor=2200,
        concentration_mg_per_ml=Decimal("2"),
    )
    session.add(item)
    await session.flush()
    session.add(
        DoseRule(
            clinic_id=clinic.id,
            price_list_item_id=item.id,
            species="Felino",
            dose_default_per_kg=Decimal("0.15"),
            reviewed_at=datetime.now(UTC),
            reviewed_by_name="Dra. Paula",
        )
    )
    await session.flush()

    resp = await client.post(
        "/api/v1/prescriptions/dose-preview",
        json={
            "price_list_item_id": str(item.id),
            "hospitalization_id": str(hosp.id),
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    corpo = resp.json()
    assert Decimal(corpo["dose_per_kg"]) == Decimal("0.15")
    assert Decimal(corpo["weight_kg"]) == Decimal("3.6")
    assert Decimal(corpo["dose_mg"]) == Decimal("0.5400")
    assert Decimal(corpo["volume_ml"]) == Decimal("0.270")
    assert corpo["reviewed"] is True
    assert corpo["reviewed_by_name"] == "Dra. Paula"


async def test_conferir_a_dose_e_ato_de_quem_tem_registro(client, session):
    """Cadastrar a tabela é gestão; dizer que uma dose foi conferida é clínico."""
    clinic = await make_clinic(session)
    admin_user = await make_user(session)
    admin = await make_membership(session, clinic=clinic, user=admin_user, role="admin")
    item = PriceListItem(
        clinic_id=clinic.id,
        name="Metadona 10 mg/ml",
        category="medication",
        unit="por dose",
        price_minor=3800,
        concentration_mg_per_ml=Decimal("10"),
    )
    session.add(item)
    await session.flush()

    corpo = {"species": "Canino", "dose_default_per_kg": "0.2", "reviewed": True}
    negado = await client.put(
        f"/api/v1/price-list/{item.id}/dose-rules",
        json=corpo,
        headers=bearer(personal_token(admin)),
    )
    assert negado.status_code == 403
    assert negado.json()["error"]["params"]["capability"] == "prescription.create"

    # Sem marcar como conferida, o administrador cadastra normalmente.
    ok = await client.put(
        f"/api/v1/price-list/{item.id}/dose-rules",
        json={**corpo, "reviewed": False},
        headers=bearer(personal_token(admin)),
    )
    assert ok.status_code == 200
    assert ok.json()["reviewed_at"] is None


async def test_mexer_na_dose_invalida_a_conferencia(client, session):
    """Manter o selo faria o sistema afirmar que alguém conferiu um valor que
    nunca viu."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    vet = await make_membership(session, clinic=clinic, user=user, role="vet")
    admin_user = await make_user(session)
    await make_membership(session, clinic=clinic, user=admin_user, role="admin")
    item = PriceListItem(
        clinic_id=clinic.id,
        name="Dipirona 500 mg/ml",
        category="medication",
        unit="por dose",
        price_minor=1800,
        concentration_mg_per_ml=Decimal("500"),
    )
    session.add(item)
    await session.flush()
    headers = bearer(personal_token(vet))

    resposta = await client.put(
        f"/api/v1/price-list/{item.id}/dose-rules",
        json={"species": "Canino", "dose_default_per_kg": "25", "reviewed": True},
        headers=headers,
    )
    assert resposta.status_code == 200, resposta.json()
    assert resposta.json()["reviewed_at"] is not None

    alterada = (
        await client.put(
            f"/api/v1/price-list/{item.id}/dose-rules",
            json={"species": "Canino", "dose_default_per_kg": "50", "reviewed": False},
            headers=headers,
        )
    ).json()
    assert alterada["reviewed_at"] is None, "trocar o número derruba a conferência"
