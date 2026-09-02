"""OpenAI: texto (chat completions) e fala (transcrição).

É o único provedor que faz as duas coisas, por isso mora num arquivo só. Sem
`OPENAI_API_KEY` nenhum dos dois chama nada.
"""

import logging
from typing import Any

from app.core.config import settings
from app.services.providers import ProviderUnavailable
from app.services.providers.registry import request_json

logger = logging.getLogger(__name__)

_CHAT_URL = "https://api.openai.com/v1/chat/completions"
_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"


def _language(locale: str) -> str:
    """`pt-BR` -> `pt`. A API de transcrição quer ISO-639-1, não BCP-47.

    Mandar a língua importa: sem ela o modelo detecta sozinho e um plantão
    silencioso ou com ruído de fundo vira transcrição em outro idioma."""
    return (locale or "pt-BR").split("-")[0].lower()


def _extract(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices") or []
    if not choices:
        return ""
    return ((choices[0] or {}).get("message") or {}).get("content", "").strip()


def _auth_headers() -> dict[str, str]:
    if not settings.openai_api_key:
        raise ProviderUnavailable("openai: OPENAI_API_KEY ausente")
    return {"Authorization": f"Bearer {settings.openai_api_key}"}


class OpenAITextProvider:
    name = "openai"

    async def complete(self, prompt: str, *, locale: str) -> str:
        headers = _auth_headers()
        payload = {
            "model": settings.openai_model,
            # Temperatura baixa: o modelo redige o que está no esqueleto, não
            # inventa variações de um registro clínico.
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }
        data = await request_json(
            "POST", _CHAT_URL, provider=self.name, headers=headers, json=payload
        )
        return _extract(data)


class OpenAISpeechProvider:
    name = "openai"

    async def transcribe(self, audio: bytes, *, filename: str, locale: str) -> str:
        headers = _auth_headers()
        # multipart: os bytes vão da memória direto para o corpo da requisição e
        # morrem com ela. Nada é salvo em disco nem em bucket (LGPD, spec §2).
        files = {"file": (filename, audio)}
        form = {
            "model": settings.openai_transcribe_model,
            "language": _language(locale),
        }
        data = await request_json(
            "POST",
            _TRANSCRIPTION_URL,
            provider=self.name,
            headers=headers,
            data=form,
            files=files,
        )
        if not isinstance(data, dict):
            return ""
        return (data.get("text") or "").strip()
