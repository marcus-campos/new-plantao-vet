import json
import logging
from typing import Any

from app.i18n.catalog import translate
from app.services.providers import ProviderUnavailable, text_provider

logger = logging.getLogger(__name__)

#: O QUE PODE SAIR DAQUI. Allowlist, nunca denylist: o esqueleto vai ganhar
#: campos, e um campo novo que vazasse por padrão levaria telefone, CPF ou
#: endereço de tutor para um provedor externo sem ninguém perceber (LGPD, spec
#: §8.4: o DPA cobre dado clínico, não a agenda de contatos da clínica).
PROMPT_FIELDS: tuple[str, ...] = (
    "period",
    "tasks",
    "events",
    "prescription_changes",
    "notes",
)

#: Teto do rascunho aceito. É UM parágrafo; um modelo que devolve mais que isso
#: ignorou o formato (costuma ser o JSON de volta, ou uma lista com títulos) e o
#: que ele mandou não é boletim. Nesse caso o determinístico é melhor.
MAX_DRAFT_CHARS = 1500

#: Instruções no imperativo e em inglês porque é o registro em que os três
#: provedores são mais estáveis. O IDIOMA DE SAÍDA é dito explicitamente e não
#: tem relação com o idioma do prompt (regra 3 de `providers/__init__.py`).
_PROMPT = """\
You are drafting the shift handover summary for ONE hospitalized veterinary \
patient, to be read by the professional taking over the next shift.

OUTPUT LANGUAGE: {locale}. Write the whole paragraph in {locale}, regardless of \
the language of these instructions or of the field names in the data.

FORMAT: one short paragraph, 2 to 5 sentences, plain prose in the register a \
veterinarian actually writes in the record. No heading, no bullet list, no \
markdown, no greeting, no sign-off, no preamble. Return the paragraph and \
nothing else.

RULES – this text goes into a medical record:
- State ONLY what the DATA below contains. Never add a finding, diagnosis, \
vital sign, drug, dose, time, name or opinion that is not there.
- Name the concrete pending work the next shift picks up: the pending and \
overdue items, by title and time.
- Reproduce the shift notes as their author wrote them; do not translate, \
soften or reinterpret them.
- Say less when the data is thin. Never pad, never speculate, never close with \
a recommendation of your own.

DATA (JSON):
{data}
"""


class NarrativeService:
    """Rascunho narrativo do boletim, no locale da clínica.

    Duas regras valem sempre, com ou sem provedor de IA configurado:

    1. A narrativa é um RASCUNHO. O esqueleto é a fonte de verdade e nunca é
       redigido por modelo nenhum; se o provedor cair, ficar lento ou devolver
       bobagem, `draft` devolve o determinístico e a passagem continua de pé.
    2. O texto sai no locale da clínica (`clinics.locale`), nunca no idioma do
       prompt nem no do servidor.
    """

    @staticmethod
    def deterministic(skeleton: dict[str, Any], locale: str) -> str:
        tasks = skeleton.get("tasks") or {}
        counters = {
            key: int(tasks.get(key, 0))
            for key in ("done", "partial", "not_done", "pending", "overdue")
        }
        changes = skeleton.get("prescription_changes") or {}
        events = skeleton.get("events") or []
        notes = skeleton.get("notes") or []

        parts = [translate("handover.narrative.tasks", locale, **counters)]
        parts.append(
            translate("handover.narrative.events", locale, count=len(events))
            if events
            else translate("handover.narrative.no_events", locale)
        )
        parts.append(
            translate(
                "handover.narrative.prescriptions",
                locale,
                created=len(changes.get("created") or []),
                adjusted=len(changes.get("adjusted") or []),
                suspended=len(changes.get("suspended") or []),
            )
        )
        if notes:
            # O texto da nota entra COMO A CLÍNICA ESCREVEU (spec §3.6): conteúdo
            # do cliente não é traduzido nem reescrito.
            texts = " | ".join(
                f"{note.get('author_name')}: {note.get('text')}".strip() for note in notes
            )
            parts.append(translate("handover.narrative.notes", locale, texts=texts))
        else:
            parts.append(translate("handover.narrative.no_notes", locale))
        return " ".join(parts)

    @staticmethod
    def clinical_payload(skeleton: dict[str, Any]) -> dict[str, Any]:
        """O recorte do esqueleto que pode sair da clínica."""
        return {key: skeleton[key] for key in PROMPT_FIELDS if key in skeleton}

    @staticmethod
    def build_prompt(skeleton: dict[str, Any], locale: str) -> str:
        payload = NarrativeService.clinical_payload(skeleton)
        return _PROMPT.format(
            locale=locale,
            data=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )

    @staticmethod
    def usable(raw: str | None) -> str | None:
        """O rascunho limpo, ou `None` quando não dá para usar.

        Verificar em vez de confiar: o modelo pode devolver vazio, devolver o
        JSON de volta ou embrulhar tudo numa cerca de markdown. Um boletim
        vazio ou com ``` no meio é pior que o determinístico."""
        text = (raw or "").strip()
        if text.startswith("```"):
            # Cerca de markdown apesar do "no markdown": tira a primeira e a
            # última linha em vez de descartar um texto que pode estar bom.
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        text = text.strip('"').strip()
        if len(text) < 2 or len(text) > MAX_DRAFT_CHARS:
            return None
        return text

    @staticmethod
    async def draft(skeleton: dict[str, Any], locale: str) -> str:
        """O rascunho do boletim. Ponto ÚNICO em que a IA entra no sistema.

        Nunca levanta por causa do provedor: sem credencial, com timeout, com o
        provedor fora do ar ou com uma resposta que não serve, devolve o texto
        determinístico. A passagem de plantão não pode ficar pendurada esperando
        um fornecedor.

        A exceção é `AI_TEXT_PROVIDER` com nome inexistente: aí sobe, porque é
        erro de deploy e o silêncio esconderia que a IA nunca foi ligada.
        """
        # Fora do try: nome de provedor errado é para estourar.
        provider = text_provider()
        fallback = NarrativeService.deterministic(skeleton, locale)
        if provider.name == "stub":
            return fallback

        prompt = NarrativeService.build_prompt(skeleton, locale)
        try:
            raw = await provider.complete(prompt, locale=locale)
        except ProviderUnavailable as exc:
            logger.info("narrative_deterministico provider=%s motivo=%s", provider.name, exc)
            return fallback
        except Exception:  # noqa: BLE001 (nada do provedor derruba a passagem)
            logger.exception("narrative_draft falhou provider=%s", provider.name)
            return fallback

        text = NarrativeService.usable(raw)
        if text is None:
            logger.warning(
                "narrative_draft descartado provider=%s chars=%d",
                provider.name,
                len(raw or ""),
            )
            return fallback
        return text
