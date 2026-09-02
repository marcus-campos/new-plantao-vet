"""Anthropic (Claude): texto.

Sem `ANTHROPIC_API_KEY` não há chamada. O header `anthropic-version` é
obrigatório: sem ele a API recusa com 400, e a data fixa é o que impede uma
mudança futura da API de reescrever o boletim de um jeito diferente da noite
para o dia.
"""

import logging
from typing import Any

from app.core.config import settings
from app.services.providers import ProviderUnavailable
from app.services.providers.registry import request_json

logger = logging.getLogger(__name__)

_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"

#: Teto de saída. O boletim é UM parágrafo; um teto curto é a segunda barreira
#: contra um modelo que resolveu escrever um relatório.
_MAX_TOKENS = 700


def _extract(payload: Any) -> str:
    """Concatena os blocos de texto da resposta (pode vir mais de um)."""
    if not isinstance(payload, dict):
        return ""
    blocks = payload.get("content") or []
    return "".join(
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type", "text") == "text"
    ).strip()


class AnthropicTextProvider:
    name = "anthropic"

    async def complete(self, prompt: str, *, locale: str) -> str:
        if not settings.anthropic_api_key:
            raise ProviderUnavailable("anthropic: ANTHROPIC_API_KEY ausente")
        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": settings.anthropic_model,
            "max_tokens": _MAX_TOKENS,
            # Temperatura baixa: registro clínico se redige, não se varia.
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }
        data = await request_json("POST", _URL, provider=self.name, headers=headers, json=payload)
        return _extract(data)
