"""A conta da dose, e sobretudo a conta EXPOSTA.

O que o veterinário confere não é o resultado: é a conta. "0,27 ml" sozinho não
se verifica; "0,15 mg/kg × 3,6 kg = 0,54 mg ÷ 2 mg/ml = 0,27 ml" se verifica num
relance. Por isso este serviço devolve os passos, não só o número, e por isso
a interface os mostra sempre, mesmo quando o campo já vem preenchido.

A aritmética é a padrão da medicina veterinária:

    dose_mg   = dose_mg_por_kg × peso_kg          (ou a dose fixa por animal)
    volume_ml = dose_mg ÷ concentração_mg_por_ml

Duas posturas que este módulo não abandona:

* **Avisa, nunca bloqueia.** Fora da faixa, contraindicado na espécie, raça
  sensível: tudo vira aviso com registro. É a mesma escolha já feita nos
  guardrails de PRN e no jejum, e vem da pesquisa (§4): fricção sem valor
  clínico percebido é contornada, e o sistema passa a mentir. Quem decide é
  quem tem registro no conselho.
* **Não inventa dose.** Sem regra conferida por um veterinário, não há
  sugestão: há um campo vazio e um aviso de que ninguém conferiu aquilo.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dose_rule import DoseRule
from app.models.price_list_item import PriceListItem
from app.services.patient_search import _fold


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass
class DoseCalculation:
    """O que a tela precisa mostrar: o resultado E o caminho até ele."""

    #: mg/kg efetivamente usado. None quando a dose é fixa por animal.
    dose_per_kg: Decimal | None
    weight_kg: Decimal | None
    #: Total em mg.
    dose_mg: Decimal | None
    concentration_mg_per_ml: Decimal | None
    volume_ml: Decimal | None
    #: Códigos de aviso: a API nunca devolve prosa (ADR-0004). O cliente
    #: traduz e decide o peso visual de cada um.
    warnings: list[str] = field(default_factory=list)
    #: Texto curto escrito pela clínica (contraindicação, raça). Conteúdo do
    #: cliente: não é traduzido.
    notes: list[str] = field(default_factory=list)
    #: A regra usada, para a tela dizer quem conferiu e quando.
    rule_id: uuid.UUID | None = None
    reviewed: bool = False
    reviewed_by_name: str | None = None
    dose_min_per_kg: Decimal | None = None
    dose_max_per_kg: Decimal | None = None
    frequency_minutes: int | None = None


class DosingService:
    @staticmethod
    async def rule_for(
        session: AsyncSession,
        *,
        clinic_id: uuid.UUID,
        price_list_item_id: uuid.UUID,
        species: str | None,
    ) -> DoseRule | None:
        """A regra da espécie vence a genérica.

        Uma posologia única por fármaco seria a posologia errada para metade dos
        pacientes: o gato não tem várias vias de glicuronidação que o cão usa."""
        rules = list(
            (
                await session.execute(
                    sa.select(DoseRule).where(
                        DoseRule.clinic_id == clinic_id,
                        DoseRule.price_list_item_id == price_list_item_id,
                        DoseRule.is_active.is_(True),
                    )
                )
            ).scalars()
        )
        if not rules:
            return None
        alvo = _fold(species or "")
        especifica = next((r for r in rules if r.species and _fold(r.species) == alvo), None)
        return especifica or next((r for r in rules if r.species is None), None)

    @staticmethod
    def calculate(
        *,
        rule: DoseRule | None,
        item: PriceListItem | None,
        weight_kg: object,
        species: str | None = None,
        breed: str | None = None,
        dose_per_kg_override: object = None,
    ) -> DoseCalculation:
        peso = _decimal(weight_kg)
        concentracao = _decimal(getattr(item, "concentration_mg_per_ml", None))
        override = _decimal(dose_per_kg_override)

        calc = DoseCalculation(
            dose_per_kg=None,
            weight_kg=peso,
            dose_mg=None,
            concentration_mg_per_ml=concentracao,
            volume_ml=None,
        )

        if rule is None:
            # Sem regra não há sugestão, e dizer isso é melhor que deixar o
            # campo vazio sem explicação.
            calc.warnings.append("no_rule")
            if override is not None and peso is not None:
                calc.dose_per_kg = override
                calc.dose_mg = (override * peso).quantize(Decimal("0.0001"))
            return DosingService._finish(calc, concentracao)

        calc.rule_id = rule.id
        calc.reviewed = rule.reviewed_at is not None
        calc.reviewed_by_name = rule.reviewed_by_name
        calc.dose_min_per_kg = rule.dose_min_per_kg
        calc.dose_max_per_kg = rule.dose_max_per_kg
        calc.frequency_minutes = rule.frequency_minutes

        if rule.is_contraindicated:
            calc.warnings.append("contraindicated")
        if not calc.reviewed:
            # A regra existe mas ninguém a conferiu: o número aparece como
            # referência, nunca como preenchimento silencioso.
            calc.warnings.append("unreviewed_rule")
        if rule.warning:
            calc.notes.append(rule.warning)

        # Raça sensível: casa por texto porque raça é campo livre, e a lista é
        # curada pela clínica. ABCB1-1∆ (MDR1) nas raças pastoreiras compromete
        # a glicoproteína-P na barreira hematoencefálica.
        if rule.breeds and breed:
            alvo = _fold(breed)
            if any(_fold(r.strip()) and _fold(r.strip()) in alvo for r in rule.breeds.split(",")):
                calc.warnings.append("breed_sensitivity")
                if rule.breed_warning:
                    calc.notes.append(rule.breed_warning)

        # Dose FIXA por animal vence: multiplicar 1–2 mg/gato pelo peso é
        # exatamente o erro que esta coluna existe para evitar.
        if rule.fixed_dose_mg is not None and override is None:
            calc.dose_mg = rule.fixed_dose_mg
            calc.warnings.append("fixed_dose")
            return DosingService._finish(calc, concentracao)

        por_kg = override if override is not None else rule.dose_default_per_kg
        if por_kg is None or peso is None:
            if peso is None:
                # Sem peso não há conta. É o dado que o sistema deveria ter e
                # não tem. Dizer isso é melhor que mostrar um zero.
                calc.warnings.append("no_weight")
            return DosingService._finish(calc, concentracao)

        calc.dose_per_kg = por_kg
        if rule.dose_min_per_kg is not None and por_kg < rule.dose_min_per_kg:
            calc.warnings.append("below_range")
        if rule.dose_max_per_kg is not None and por_kg > rule.dose_max_per_kg:
            calc.warnings.append("above_range")

        total = (por_kg * peso).quantize(Decimal("0.0001"))
        if rule.max_total_mg is not None and total > rule.max_total_mg:
            # Alguns fármacos não escalam linearmente com o peso: o teto vale.
            total = rule.max_total_mg
            calc.warnings.append("capped")
        calc.dose_mg = total
        return DosingService._finish(calc, concentracao)

    @staticmethod
    def _finish(calc: DoseCalculation, concentracao: Decimal | None) -> DoseCalculation:
        if calc.dose_mg is not None and concentracao:
            calc.volume_ml = (calc.dose_mg / concentracao).quantize(Decimal("0.001"))
        elif calc.dose_mg is not None and not concentracao:
            # Sem a concentração da apresentação só dá para chegar em mg. O
            # número que a pessoa aspira na seringa é o volume.
            calc.warnings.append("no_concentration")
        return calc
