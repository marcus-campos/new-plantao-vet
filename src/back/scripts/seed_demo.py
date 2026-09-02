"""Clínica demo do PlantãoVet: o que o vendedor abre na frente do cliente.

uv run python -m scripts.seed_demo
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory
from app.core.security import hash_password, verify_password
from app.models import (
    PLAN_TIERS,
    Clinic,
    DoseRule,
    Hospitalization,
    Kennel,
    Membership,
    Owner,
    Patient,
    PatientIdentifier,
    Prescription,
    PriceListItem,
    ProgressNote,
    Shift,
    ShiftNote,
    Task,
    User,
)
from app.schemas.prescription import default_tolerance
from app.services.audit import ActorInfo
from app.services.handover import HandoverService
from app.services.hospitalization import HospitalizationService
from app.services.narrative import NarrativeService
from app.services.plans import PlanService
from app.services.tasks import TaskService

STATION_KEY = "estacao-123"
SENHA = "senha-123"

# Quem opera a PLATAFORMA na demo: vende, faz onboarding e dá suporte. Não é
# membro de clínica nenhuma; entra por /plataforma com o próprio login.
PLATAFORMA = ("Marcus Campos", "suporte@plantao.vet", SENHA)

# PIN de SEIS dígitos: com quatro são 10 mil combinações, e o PIN é único por
# clínica (dois iguais atribuiriam o ato clínico à pessoa errada). Em algumas
# centenas de pessoas a colisão deixa de ser exceção e vira o caso comum.
EQUIPE = [
    ("Dra. Paula Martins", "paula@demo.vet", "vet", "12345", "CRMV-SP", "123456"),
    ("Marina Coelho", "marina@demo.vet", "tech", None, None, "234567"),
    ("Rafael Souza", "rafael@demo.vet", "admin", None, None, "345678"),
]

# (código, nome, categoria, unidade, preço, diária, área, mg/ml)
#
# A concentração não é enfeite de catálogo: é ela que transforma a dose em
# volume na seringa. Sem mg/ml o cálculo para em miligramas, e quem administra
# recebe "0,54 mg" sem saber quanto puxar.
PRECOS = [
    ("MED-018", "Dipirona sódica 500 mg/ml", "medication", "por dose", 1800, False, None, 500),
    ("MED-022", "Ondansetrona 2 mg/ml", "medication", "por dose", 2200, False, None, 2),
    ("MED-041", "Metadona 10 mg/ml", "medication", "por dose", 3800, False, None, 10),
    (
        "PRO-007",
        "Aferição de pressão arterial",
        "procedure",
        "por aferição",
        4500,
        False,
        None,
        None,
    ),
    (
        "PRO-012",
        "Checagem de bomba de infusão",
        "procedure",
        "por checagem",
        1200,
        False,
        None,
        None,
    ),
    ("INS-003", "Ringer Lactato 500 ml", "fluids", "por bolsa", 3400, False, None, None),
    ("DIA-001", "Diária de internação · UTI", "care", "por dia", 28000, True, "UTI", None),
    ("DIA-002", "Diária de internação · geral", "care", "por dia", 16500, True, "Geral", None),
]

# Posologia da demo, por (código do item, espécie).
#
# Os números vêm da literatura veterinária de referência (Plumb's). Ficam aqui
# como DADO da clínica demo, nunca como constante do produto: cada clínica
# cadastra a sua, e a interface diz quem conferiu e quando. Um sistema que
# embutisse a própria posologia estaria afirmando uma dose que ninguém assinou.
POSOLOGIAS = [
    # (código, espécie, via, mín, padrão, máx, frequência, aviso)
    ("MED-018", "Canino", "IV", "20", "25", "35", 480, None),
    (
        "MED-018",
        "Felino",
        "IV",
        "20",
        "25",
        "25",
        1440,
        "Gato: intervalo mais longo e uso curto. Rever a cada 3 dias.",
    ),
    ("MED-022", "Canino", "IV", "0.5", "0.5", "1", 720, None),
    ("MED-022", "Felino", "IV", "0.5", "0.5", "1", 720, None),
    ("MED-041", "Canino", "IV", "0.1", "0.2", "0.5", 240, None),
    (
        "MED-041",
        "Felino",
        "IV",
        "0.1",
        "0.2",
        "0.3",
        360,
        "Gato: faixa mais estreita e intervalo maior que no cão.",
    ),
]

FONTE = "Plumb's Veterinary Drug Handbook, 10ª edição"

PACIENTES = [
    ("Thor", "Canino", "UTI 03", 24.3),
    ("Nina", "Felino", "Box 07", 4.1),
    ("Mel", "Felino", "UTI 01", 3.6),
    ("Bob", "Canino", "Box 02", 12.0),
    ("Luna", "Felino", "Box 04", 3.9),
]


async def completar(session: AsyncSession, clinic: Clinic) -> None:
    """Põe na demo já existente o que foi acrescentado depois dela.

    O seed saía cedo quando a clínica existia, e toda funcionalidade nova
    nascia invisível para quem já tinha o banco de pé: a demo continuava sem
    concentração e sem posologia, a calculadora abria vazia, e a conclusão
    natural era que a funcionalidade não funcionava. Completar é diferente de
    recriar: nada que já está lá é tocado.
    """
    itens = {
        item.code: item
        for item in (
            await session.execute(
                sa.select(PriceListItem).where(PriceListItem.clinic_id == clinic.id)
            )
        ).scalars()
        if item.code
    }
    for code, _, _, _, _, _, _, mg_ml in PRECOS:
        item = itens.get(code)
        if item is not None and mg_ml is not None and item.concentration_mg_per_ml is None:
            item.concentration_mg_per_ml = Decimal(str(mg_ml))

    vet = await session.scalar(
        sa.select(Membership).where(Membership.clinic_id == clinic.id, Membership.role == "vet")
    )
    ja_tem = {
        (rule.price_list_item_id, rule.species)
        for rule in (
            await session.execute(sa.select(DoseRule).where(DoseRule.clinic_id == clinic.id))
        ).scalars()
    }
    # PIN de seis dígitos: a demo precisa refletir o que o produto exige hoje,
    # senão o teclado da estação pede seis e os PINs da demo têm quatro. Só a
    # clínica `demo` passa por aqui.
    pins = 0
    for _nome, email, _papel, _reg, _orgao, pin in EQUIPE:
        vinculo = await session.scalar(
            sa.select(Membership)
            .join(User, User.id == Membership.user_id)
            .where(Membership.clinic_id == clinic.id, User.email == email)
        )
        if vinculo is None or (
            vinculo.pin_hash is not None and verify_password(pin, vinculo.pin_hash)
        ):
            continue
        vinculo.pin_hash = hash_password(pin)
        pins += 1

    operadores = await garantir_operador(session)
    # O catálogo de planos precisa existir antes de qualquer clínica apontar
    # para um; e o plano da demo era um valor de antes de existirem planos.
    await PlanService.ensure_defaults(session)
    if clinic.plan_tier not in PLAN_TIERS:
        clinic.plan_tier = "pro"

    novas = 0
    for code, especie, via, minimo, padrao, maximo, frequencia, aviso in POSOLOGIAS:
        item = itens.get(code)
        if item is None or (item.id, especie) in ja_tem:
            continue
        session.add(
            DoseRule(
                clinic_id=clinic.id,
                price_list_item_id=item.id,
                species=especie,
                route=via,
                dose_min_per_kg=Decimal(minimo),
                dose_default_per_kg=Decimal(padrao),
                dose_max_per_kg=Decimal(maximo),
                frequency_minutes=frequencia,
                warning=aviso,
                source=FONTE,
                reviewed_at=datetime.now(UTC),
                reviewed_by=vet.id if vet is not None else None,
                reviewed_by_name=EQUIPE[0][0],
            )
        )
        novas += 1
    await session.commit()
    print(
        f"Clínica demo já existe. Posologias acrescentadas: {novas}, "
        f"PINs atualizados: {pins}, operadores da plataforma: {operadores}."
    )


async def garantir_operador(session: AsyncSession) -> int:
    """Cria o operador da plataforma da demo, se ainda não existir."""
    nome, email, senha = PLATAFORMA
    existente = await session.scalar(sa.select(User).where(User.email == email))
    if existente is not None:
        if not existente.is_platform_operator:
            existente.is_platform_operator = True
            return 1
        return 0
    session.add(
        User(
            name=nome,
            email=email,
            password_hash=hash_password(senha),
            is_platform_operator=True,
        )
    )
    return 1


async def main() -> None:
    async with async_session_factory() as session:
        # Idempotente: rodar de novo (ex.: no entrypoint do container) não duplica.
        existing = await session.scalar(sa.select(Clinic).where(Clinic.slug == "demo"))
        if existing is not None:
            await completar(session, existing)
            return

        await garantir_operador(session)
        await PlanService.ensure_defaults(session)
        clinic = Clinic(
            name="Clínica Vida Animal",
            slug="demo",
            bed_limit=25,
            plan_tier="pro",
            subscription_status="active",
            contact_name="Rafael Souza",
            contact_email="rafael@demo.vet",
            station_key_hash=hash_password(STATION_KEY),
        )
        session.add(clinic)
        await session.flush()

        memberships = {}
        for nome, email, papel, registro, orgao, pin in EQUIPE:
            user = User(name=nome, email=email, password_hash=hash_password(SENHA))
            session.add(user)
            await session.flush()
            membership = Membership(
                clinic_id=clinic.id,
                user_id=user.id,
                role=papel,
                license_number=registro,
                license_authority=orgao,
                pin_hash=hash_password(pin),
            )
            session.add(membership)
            memberships[papel] = membership
        await session.flush()

        vet = memberships["vet"]
        actor = ActorInfo(
            membership_id=vet.id,
            name="Dra. Paula Martins",
            license_number="12345",
            license_authority="CRMV-SP",
        )

        kennels = {}
        for nome in ("UTI 01", "UTI 03", "Box 02", "Box 04", "Box 07"):
            kennel = Kennel(
                clinic_id=clinic.id, name=nome, area="UTI" if "UTI" in nome else "Geral"
            )
            session.add(kennel)
            kennels[nome] = kennel
        await session.flush()

        agora = datetime.now(UTC)
        for indice, (nome, especie, box, peso) in enumerate(PACIENTES):
            # CPF e microchip fictícios, sequenciais: a demo tem de mostrar a
            # busca por documento do tutor e por chip do paciente funcionando.
            owner = Owner(
                clinic_id=clinic.id,
                name=f"Tutor de {nome}",
                phone_e164="+5511999990000",
                tax_id=f"1112223330{indice}",
            )
            session.add(owner)
            await session.flush()
            patient = Patient(
                clinic_id=clinic.id,
                owner_id=owner.id,
                name=nome,
                species=especie,
                weight_kg=peso,
            )
            session.add(patient)
            await session.flush()
            session.add(
                PatientIdentifier(
                    clinic_id=clinic.id,
                    patient_id=patient.id,
                    kind="microchip",
                    value=f"98102000012345{indice}",
                )
            )
            hospitalization = Hospitalization(
                clinic_id=clinic.id,
                patient_id=patient.id,
                kennel_id=kennels[box].id,
                vet_membership_id=vet.id,
                consent_status="consent_recorded",
                admitted_at=agora - timedelta(days=2),
            )
            session.add(hospitalization)
            await session.flush()
            await HospitalizationService.create_default_prescriptions(
                session, hospitalization=hospitalization, clinic=clinic, actor=actor
            )

            receitas = [
                dict(
                    kind="recurring",
                    category="medication",
                    name="Dipirona 25 mg/kg IV",
                    frequency_minutes=480,
                    criticality="normal",
                    price_minor=1800,
                    details={"drug": "dipirona", "dose": "25 mg/kg", "route": "IV"},
                ),
                dict(
                    kind="continuous",
                    category="fluids",
                    name="Ringer Lactato",
                    frequency_minutes=120,
                    criticality="normal",
                    price_minor=1200,
                    details={"rate_ml_h": 60},
                ),
                dict(
                    kind="recurring",
                    category="monitoring",
                    name="Pressão arterial",
                    frequency_minutes=720,
                    criticality="critical",
                    price_minor=4500,
                    details={},
                ),
                dict(
                    kind="recurring",
                    category="nutrition",
                    name="Alimentação úmida",
                    frequency_minutes=480,
                    criticality="normal",
                    price_minor=0,
                    details={},
                ),
                dict(
                    kind="prn",
                    category="medication",
                    name="Metadona 0,2 mg/kg IM",
                    frequency_minutes=None,
                    criticality="critical",
                    price_minor=3800,
                    max_doses_24h=4,
                    min_interval_minutes=240,
                    details={"drug": "metadona", "route": "IM"},
                ),
            ]
            for receita in receitas:
                prescription = Prescription(
                    clinic_id=clinic.id,
                    hospitalization_id=hospitalization.id,
                    tolerance_minutes=default_tolerance(
                        receita["criticality"], receita["frequency_minutes"]
                    ),
                    starts_at=agora - timedelta(hours=8),
                    created_by=vet.id,
                    **receita,
                )
                session.add(prescription)
                await session.flush()
                await TaskService.materialize(
                    session,
                    prescription=prescription,
                    clinic=clinic,
                    until=agora + timedelta(hours=48),
                )

        # Uma tarefa crítica deliberadamente atrasada: é o que a demo precisa mostrar.
        atrasada = await session.scalar(
            sa.select(Task)
            .where(Task.clinic_id == clinic.id, Task.criticality == "critical")
            .limit(1)
        )
        if atrasada is not None:
            atrasada.scheduled_for = agora - timedelta(hours=3)

        # Catálogo de preços: é dele que a prescrição puxa o valor.
        itens: dict[str, PriceListItem] = {}
        for code, nome, categoria, unidade, valor, diaria, area, mg_ml in PRECOS:
            item = PriceListItem(
                clinic_id=clinic.id,
                code=code,
                name=nome,
                category=categoria,
                unit=unidade,
                price_minor=valor,
                is_daily_rate=diaria,
                kennel_area=area,
                concentration_mg_per_ml=Decimal(str(mg_ml)) if mg_ml is not None else None,
            )
            session.add(item)
            if code:
                itens[code] = item
        await session.flush()

        # Posologia conferida pela veterinária da demo: sem conferência a regra
        # existe mas não pré-preenche nada, e a calculadora ficaria vazia na
        # primeira tela que o vendedor abre.
        for code, especie, via, minimo, padrao, maximo, frequencia, aviso in POSOLOGIAS:
            session.add(
                DoseRule(
                    clinic_id=clinic.id,
                    price_list_item_id=itens[code].id,
                    species=especie,
                    route=via,
                    dose_min_per_kg=Decimal(minimo),
                    dose_default_per_kg=Decimal(padrao),
                    dose_max_per_kg=Decimal(maximo),
                    frequency_minutes=frequencia,
                    warning=aviso,
                    source=FONTE,
                    reviewed_at=datetime.now(UTC),
                    reviewed_by=vet.id,
                    reviewed_by_name=EQUIPE[0][0],
                )
            )
        await session.flush()

        internacoes = list(
            (
                await session.execute(
                    sa.select(Hospitalization).where(Hospitalization.clinic_id == clinic.id)
                )
            ).scalars()
        )

        # Uma evolução assinada e uma nota de plantão por paciente, para as telas
        # de evolução e de passagem nascerem com conteúdo de verdade.
        for index, internacao in enumerate(internacoes):
            session.add(
                ProgressNote(
                    clinic_id=clinic.id,
                    hospitalization_id=internacao.id,
                    membership_id=vet.id,
                    author_name="Dra. Paula Martins",
                    author_license="12345",
                    author_license_authority="CRMV-SP",
                    subjective="Mais alerta ao longo do dia; aceitou água.",
                    findings="Mucosas hipocoradas, TPC 2s. Hidratação melhorando.",
                    assessment="Evolução favorável.",
                    plan="Manter fluidoterapia e reavaliar taxa pela manhã.",
                    signed_at=agora - timedelta(hours=30 if index == 0 else 6),
                )
            )
            session.add(
                ShiftNote(
                    clinic_id=clinic.id,
                    hospitalization_id=internacao.id,
                    membership_id=memberships["tech"].id,
                    author_name="Marina Coelho",
                    text=(
                        "Mais responsivo, aceitou água. Mucosa ainda pálida; "
                        "atenção na PA da noite."
                    ),
                    source="audio",
                    created_at=agora - timedelta(hours=3),
                )
            )
        await session.flush()

        # Escala: o turno que sai (já fechado, com boletins) e o que entra.
        #
        # A veterinária responde pelos dois turnos, e a técnica entra junto no
        # diurno. Antes o turno da noite era do ADMINISTRADOR (que por política
        # não opera plantão nem executa tarefa) e nenhum dos dois tinha
        # veterinário responsável, então a demonstração abria a escala com
        # "turno sem veterinário" em 100% dos turnos, contradizendo o discurso de
        # venda (a fiscalização "De Olho no Plantão" autuou 82 estabelecimentos
        # exatamente por isso).
        turno_diurno = Shift(
            clinic_id=clinic.id,
            name="Diurno",
            starts_at=agora - timedelta(hours=12),
            ends_at=agora,
            membership_id=memberships["vet"].id,
            is_vet_responsible=True,
        )
        turno_diurno_tech = Shift(
            clinic_id=clinic.id,
            name="Diurno",
            starts_at=agora - timedelta(hours=12),
            ends_at=agora,
            membership_id=memberships["tech"].id,
            is_vet_responsible=False,
        )
        turno_noturno = Shift(
            clinic_id=clinic.id,
            name="Noturno",
            starts_at=agora,
            ends_at=agora + timedelta(hours=12),
            membership_id=memberships["vet"].id,
            is_vet_responsible=True,
        )
        session.add_all([turno_diurno, turno_diurno_tech, turno_noturno])
        await session.flush()

        # Boletins do turno que sai: alguns aprovados, um deliberadamente sem
        # revisão, que é o caso que a tela precisa mostrar com o selo vermelho.
        boletins = await HandoverService.generate(
            session,
            clinic=clinic,
            from_shift=turno_diurno,
            to_shift=turno_noturno,
            actor=actor,
        )
        for posicao, boletim in enumerate(boletins):
            await HandoverService.set_narrative(
                session,
                report=boletim,
                narrative=NarrativeService.deterministic(boletim.skeleton, clinic.locale),
                actor=actor,
            )
            if posicao != 0:
                await HandoverService.approve(session, report=boletim, actor=actor)

        await session.commit()
        print(f"Clínica demo criada · slug=demo · senha={SENHA} · station_key={STATION_KEY}")
        print("PINs: vet 123456 · técnico 234567 · admin 345678")
        print(f"Plataforma: {PLATAFORMA[1]} · senha={SENHA} · /plataforma")


if __name__ == "__main__":
    asyncio.run(main())
