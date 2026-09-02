"""Sinais vitais: a outra metade da ficha de tratamento.

A pesquisa (§1) descreve a prancheta em duas metades: a grade de medicações e a
**grade horária de monitoramento** (temperatura, FC, FR, mucosa/TPC, escore de
dor 0–4). A segunda metade não existia aqui: `tasks.values` é jsonb livre, então
toda medição já registrada era write-only: ninguém conseguia ler a série, nem
saber se 82 mg/dL era normal ou emergência.

Este módulo tem a mesma forma de `app/compliance/`: dataclasses congeladas, um
registro e uma consulta. Acrescentar um vital novo é acrescentar uma entrada
aqui; nenhuma rota, schema ou migração muda.

Duas faixas, que NÃO se confundem:

* **`reference`** é o normal da espécie. Valor fora dela é o ACHADO: o animal
  está doente, e é exatamente isso que a ficha existe para registrar. Nunca
  bloqueia: recusar o registro seria o sistema se negando a documentar um
  paciente grave.
* **`plausible`** é o limite do que um ser vivo pode apresentar. 385 °C não é
  febre, é o dedo que escorregou no teclado. Só esta faixa recusa.

**Valores clínicos, pendentes de confirmação (spec §8.1, risco 1):** as faixas
marcadas com `needs_vet_review=True` variam por porte, idade e método de
aferição (FC de um yorkshire não é a de um dogue alemão; gato em consulta faz
hiperglicemia de estresse). Estão aqui com as faixas mais citadas na literatura
de referência, e precisam da confirmação do veterinário na validação de domínio.

Fontes das faixas: MSD/Merck Veterinary Manual, "Routine Health Care of Dogs /
of Cats" (temperatura, FC, FR) e valores de referência laboratoriais (glicemia);
ACVIM Consensus Statement 2018 sobre hipertensão sistêmica em cães e gatos
(pressão arterial); Colorado State University Acute Pain Scale, canina e felina
(escore de dor 0–4); TPC < 2 s é a avaliação de perfusão padrão de emergência.
"""

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: As espécies com faixa própria. Uma terceira (exótico, equino) entra aqui e
#: nas tabelas de `reference`, em nenhum outro lugar.
DOG = "dog"
CAT = "cat"

#: O que `values` carrega além de vital: nota de beira de box, fração da dose
#: parcial, detalhe do motivo de não realizada. Não são medições e não têm
#: faixa; entram na lista para que uma tarefa que declara vitais continue
#: aceitando o texto livre que o resto do sistema já grava.
FREE_FORM_KEYS: frozenset[str] = frozenset({"note", "dose_given", "outcome_detail"})


@dataclass(frozen=True)
class ReferenceRange:
    low: float
    high: float

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high


@dataclass(frozen=True)
class VitalKind:
    """Um parâmetro da grade de monitoramento."""

    kind: str
    label_key: str
    #: Símbolo da unidade, propositalmente neutro de idioma (ADR-0004): "/min"
    #: serve a `bpm` e a `mpm` sem escolher um português dentro do código.
    unit: str
    #: "number" ou "choice". Mucosa é cor, não número; forçá-la a uma escala
    #: numérica seria inventar precisão que o exame físico não tem.
    value_type: str
    #: Normal da espécie. Espécie ausente = sem faixa conhecida, e a interface
    #: mostra o campo SEM referência, nunca a faixa do cão num coelho.
    reference: Mapping[str, ReferenceRange] = field(default_factory=dict)
    #: Limite fisiológico. Fora daqui é erro de digitação, e só isso recusa.
    plausible: ReferenceRange | None = None
    decimals: int = 0
    #: Valores aceitos quando `value_type == "choice"`.
    choices: tuple[str, ...] = ()
    #: Quais desses valores são o normal (a "faixa de referência" da cor).
    normal_choices: tuple[str, ...] = ()
    #: Faixa que depende de porte/idade/método e precisa de aval de veterinário.
    needs_vet_review: bool = False


_VITALS: tuple[VitalKind, ...] = (
    VitalKind(
        kind="temperature_c",
        label_key="vital.temperature_c",
        unit="°C",
        value_type="number",
        decimals=1,
        reference={DOG: ReferenceRange(37.5, 39.2), CAT: ReferenceRange(38.1, 39.2)},
        # Hipotermia de choque chega a 30 °C e é registro legítimo; 25–45 °C é o
        # que separa paciente crítico de tecla presa duas vezes.
        plausible=ReferenceRange(25.0, 45.0),
    ),
    VitalKind(
        kind="heart_rate_bpm",
        label_key="vital.heart_rate_bpm",
        unit="/min",
        value_type="number",
        reference={DOG: ReferenceRange(60, 140), CAT: ReferenceRange(140, 220)},
        plausible=ReferenceRange(10, 350),
        # Depende fortemente do porte: raça toy passa de 180 em repouso e raça
        # gigante fica abaixo de 70. Faixa única é simplificação a confirmar.
        needs_vet_review=True,
    ),
    VitalKind(
        kind="respiratory_rate_rpm",
        label_key="vital.respiratory_rate_rpm",
        unit="/min",
        value_type="number",
        reference={DOG: ReferenceRange(10, 30), CAT: ReferenceRange(20, 40)},
        plausible=ReferenceRange(2, 120),
        # Ofegação térmica em cão passa de 100 sem doença nenhuma: o limite
        # fisiológico é largo de propósito.
        needs_vet_review=True,
    ),
    VitalKind(
        kind="mucous_membrane",
        label_key="vital.mucous_membrane",
        unit="",
        value_type="choice",
        choices=("pink", "pale", "white", "hyperemic", "icteric", "cyanotic", "muddy"),
        normal_choices=("pink",),
    ),
    VitalKind(
        kind="crt_seconds",
        label_key="vital.crt_seconds",
        unit="s",
        value_type="number",
        decimals=1,
        # Perfusão não é espécie-dependente: < 2 s é o normal em cão e gato.
        reference={DOG: ReferenceRange(1.0, 2.0), CAT: ReferenceRange(1.0, 2.0)},
        plausible=ReferenceRange(0.0, 15.0),
    ),
    VitalKind(
        kind="pain_score",
        label_key="vital.pain_score",
        unit="",
        value_type="number",
        # Escala CSU: 0 sem dor, 4 dor intensa. A escala em si é o limite
        # fisiológico: 7 não é dor pior, é escala errada.
        reference={DOG: ReferenceRange(0, 1), CAT: ReferenceRange(0, 1)},
        plausible=ReferenceRange(0, 4),
    ),
    VitalKind(
        kind="glucose_mg_dl",
        label_key="vital.glucose_mg_dl",
        unit="mg/dL",
        value_type="number",
        reference={DOG: ReferenceRange(70, 120), CAT: ReferenceRange(70, 150)},
        plausible=ReferenceRange(5, 900),
        # O teto felino é mais alto porque gato contido faz hiperglicemia de
        # estresse; onde exatamente fica a fronteira é decisão de veterinário.
        needs_vet_review=True,
    ),
    VitalKind(
        kind="systolic_bp_mmhg",
        label_key="vital.systolic_bp_mmhg",
        unit="mmHg",
        value_type="number",
        reference={DOG: ReferenceRange(110, 140), CAT: ReferenceRange(110, 140)},
        plausible=ReferenceRange(20, 300),
    ),
    VitalKind(
        kind="spo2_pct",
        label_key="vital.spo2_pct",
        unit="%",
        value_type="number",
        reference={DOG: ReferenceRange(95, 100), CAT: ReferenceRange(95, 100)},
        plausible=ReferenceRange(30, 100),
    ),
)

_BY_KIND: Mapping[str, VitalKind] = {vital.kind: vital for vital in _VITALS}

#: Como a clínica digita a espécie. `patients.species` é texto livre preenchido
#: na admissão, em português e com acento ("Canino", "Felino", "Cão"). Casar
#: por igualdade com "dog" devolveria faixa nenhuma para todo paciente real.
_SPECIES_ALIASES: Mapping[str, str] = {
    "dog": DOG,
    "canine": DOG,
    "canino": DOG,
    "cao": DOG,
    "cachorro": DOG,
    "caes": DOG,
    "cat": CAT,
    "feline": CAT,
    "felino": CAT,
    "gato": CAT,
    "gata": CAT,
}


def list_vitals() -> tuple[VitalKind, ...]:
    """Tudo que a grade de monitoramento sabe medir."""
    return _VITALS


def get_vital(kind: str) -> VitalKind | None:
    """O vital, ou `None` quando o tipo é desconhecido.

    Devolve `None` em vez de levantar (ao contrário de `get_profile`) porque um
    tipo desconhecido chega de dado: de `details["vitals"]` de uma prescrição
    antiga ou escrita à mão. Derrubar a ficha inteira por causa de uma linha da
    prescrição seria pior que exibi-la sem faixa de referência."""
    return _BY_KIND.get(kind)


def normalize_species(species: str | None) -> str | None:
    """A espécie em forma canônica, ou `None` quando não reconhecemos.

    `None` é resposta legítima e importante: paciente exótico simplesmente não
    tem faixa aqui, e a interface mostra o campo sem referência. Aplicar a faixa
    do cão a um coelho seria pior que não mostrar faixa nenhuma."""
    if not species:
        return None
    folded = (
        unicodedata.normalize("NFKD", species.strip())
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    return _SPECIES_ALIASES.get(folded)


def reference_for(vital: VitalKind, species: str | None) -> ReferenceRange | None:
    """A faixa de normalidade deste vital para esta espécie."""
    canonical = normalize_species(species)
    if canonical is None:
        return None
    return vital.reference.get(canonical)


def declared_kinds(details: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Quais vitais esta prescrição manda capturar (`details["vitals"]`).

    Sem migração: `details` já é jsonb. Uma prescrição de monitoramento que não
    declara nada continua válida: vira tarefa de checagem sem grade."""
    raw = (details or {}).get("vitals")
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, str))


def validate_values(
    values: Mapping[str, Any] | None, declared: Sequence[str] = ()
) -> dict[str, str] | None:
    """Confere a grade preenchida. Devolve os params do `validation_error`, ou `None`.

    Recusa três coisas, e só três: chave que não é vital nem texto livre quando a
    prescrição declarou uma grade (é o "temperatuar" que faria a medição sumir do
    prontuário), valor do tipo errado, e valor fora do limite FISIOLÓGICO.

    Não recusa valor fora da faixa de referência: esse é o achado clínico, e um
    sistema que se recusa a registrar 41,2 °C é um sistema que perde a febre."""
    if not values:
        return None
    declared_set = set(declared)
    for key, value in values.items():
        vital = get_vital(key)
        if vital is None:
            # Chave livre só é recusada quando existe grade declarada: fora
            # disso `values` é o jsonb aberto que o resto do sistema usa.
            if declared_set and key not in FREE_FORM_KEYS and key not in declared_set:
                return {"field": f"values.{key}", "rule": "unknown_vital"}
            continue
        if declared_set and key not in declared_set:
            return {"field": f"values.{key}", "rule": "vital_not_declared"}
        error = _validate_one(vital, value)
        if error is not None:
            return error
    return None


def _validate_one(vital: VitalKind, value: Any) -> dict[str, str] | None:
    if vital.value_type == "choice":
        if value not in vital.choices:
            return {"field": f"values.{vital.kind}", "rule": "invalid_choice"}
        return None
    # bool é int em Python: `True` viraria temperatura 1 °C sem este teste.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return {"field": f"values.{vital.kind}", "rule": "not_numeric"}
    if vital.plausible is not None and not vital.plausible.contains(float(value)):
        return {"field": f"values.{vital.kind}", "rule": "out_of_physiological_range"}
    return None
