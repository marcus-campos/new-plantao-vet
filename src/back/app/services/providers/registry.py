"""Quem responde por `AI_TEXT_PROVIDER` / `AI_SPEECH_PROVIDER`, e o cano HTTP.

Trocar de provedor é trocar UMA linha do `.env`. Nenhum módulo de domínio
importa Vertex, OpenAI ou Anthropic: importa `text_provider()` e pronto.

**Nome desconhecido é ERRO, nunca fallback silencioso.** Uma clínica que digitou
`AI_TEXT_PROVIDER=vertexai` e está calada no stub tem um produto que funciona,
uma conta do GCP e nenhuma narrativa, e nada no sistema dizendo por quê. Um
erro alto e imediato custa cinco minutos; o silêncio custa a confiança de achar
que o recurso está ligado.

O cano HTTP mora aqui porque é o mesmo nos quatro: um POST, um teto de tempo, e
QUALQUER falha vira `ProviderUnavailable`, o único sinal que os chamadores
sabem tratar.
"""

import logging
from collections.abc import Callable
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import AppError
from app.services.providers import ProviderUnavailable, SpeechProvider, TextProvider

logger = logging.getLogger(__name__)

#: Gancho de teste: nenhum teste desta suíte pode tocar a internet. Trocado por
#: um `httpx.MockTransport` em `tests/test_ai_providers.py`.
TRANSPORT: httpx.AsyncBaseTransport | None = None


class UnknownProvider(AppError):
    """`AI_*_PROVIDER` com um nome que não existe.

    É `AppError` para sair no envelope do ADR-0004 (`{"error": {"code": ...}}`)
    em vez de um 500 com prosa, mas continua sendo 500 e continua ruidoso: é
    erro de deploy, não indisponibilidade, e não pode virar fallback."""

    def __init__(self, kind: str, name: str, known: list[str]) -> None:
        super().__init__(
            "ai_provider_unknown", 500, kind=kind, provider=name, known=sorted(known)
        )


async def request_json(
    method: str,
    url: str,
    *,
    provider: str,
    headers: dict[str, str] | None = None,
    json: Any = None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
) -> Any:
    """Uma chamada ao provedor. Qualquer desfecho ruim vira `ProviderUnavailable`."""
    try:
        async with httpx.AsyncClient(
            timeout=settings.ai_timeout_seconds, transport=TRANSPORT
        ) as client:
            response = await client.request(
                method, url, headers=headers, json=json, data=data, files=files
            )
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"{provider}: falha de rede ({type(exc).__name__})") from exc

    if response.status_code >= 400:
        # O corpo do erro NÃO entra na exceção nem no log: vários provedores
        # devolvem o prompt inteiro de volta, e prompt é dado clínico.
        raise ProviderUnavailable(f"{provider}: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderUnavailable(f"{provider}: resposta não era JSON") from exc


class StubTextProvider:
    """Sem provedor de texto: o boletim sai determinístico e completo.

    Recusar aqui, em vez de devolver texto vazio, é o que garante que ninguém
    grave rascunho falso no prontuário: o chamador cai no `deterministic()`."""

    name = "stub"

    async def complete(self, prompt: str, *, locale: str) -> str:
        raise ProviderUnavailable("AI_TEXT_PROVIDER=stub")


class StubSpeechProvider:
    """Sem provedor de fala: a nota de áudio não acontece, e é dito em voz alta.

    Aqui NÃO existe caminho determinístico: não há como adivinhar o que a
    pessoa falou. Inventar texto de transcrição seria escrever no prontuário uma
    frase que ninguém disse."""

    name = "stub"

    async def transcribe(self, audio: bytes, *, filename: str, locale: str) -> str:
        raise ProviderUnavailable("AI_SPEECH_PROVIDER=stub")


def _vertex_text() -> TextProvider:
    from app.services.providers.vertex import VertexTextProvider

    return VertexTextProvider()


def _openai_text() -> TextProvider:
    from app.services.providers.openai import OpenAITextProvider

    return OpenAITextProvider()


def _anthropic_text() -> TextProvider:
    from app.services.providers.anthropic import AnthropicTextProvider

    return AnthropicTextProvider()


def _openai_speech() -> SpeechProvider:
    from app.services.providers.openai import OpenAISpeechProvider

    return OpenAISpeechProvider()


# Os imports são preguiçosos: subir a API não deve depender de nenhum SDK de
# provedor que a clínica não usa.
TEXT_BUILDERS: dict[str, Callable[[], TextProvider]] = {
    "stub": StubTextProvider,
    "vertex": _vertex_text,
    "openai": _openai_text,
    "anthropic": _anthropic_text,
}

SPEECH_BUILDERS: dict[str, Callable[[], SpeechProvider]] = {
    "stub": StubSpeechProvider,
    "openai": _openai_speech,
}


def _normalize(name: str | None) -> str:
    return (name or "").strip().lower()


def build_text(name: str) -> TextProvider:
    key = _normalize(name)
    builder = TEXT_BUILDERS.get(key)
    if builder is None:
        raise UnknownProvider("text", key, list(TEXT_BUILDERS))
    return builder()


def build_speech(name: str) -> SpeechProvider:
    key = _normalize(name)
    builder = SPEECH_BUILDERS.get(key)
    if builder is None:
        raise UnknownProvider("speech", key, list(SPEECH_BUILDERS))
    return builder()


def validate() -> None:
    """Confere os dois nomes na subida. Chame no lifespan da app.

    Descobrir o nome errado só quando o primeiro plantão fecha é descobrir tarde
    demais. O processo tem de recusar a subir, como recusaria com uma URL de
    banco inválida."""
    build_text(settings.ai_text_provider)
    build_speech(settings.ai_speech_provider)
    logger.info(
        "ai_providers text=%s speech=%s",
        _normalize(settings.ai_text_provider),
        _normalize(settings.ai_speech_provider),
    )
