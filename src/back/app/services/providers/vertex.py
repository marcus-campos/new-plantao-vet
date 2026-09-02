"""Vertex AI (Gemini): texto.

Autentica com a service account do GCP, o mesmo mecanismo do FCM
(`google_auth.py`). Sem `VERTEX_PROJECT` não há chamada: recusa na hora e o
boletim sai determinístico.
"""

import logging
from typing import Any

from app.core.config import settings
from app.services.providers import ProviderUnavailable
from app.services.providers.google_auth import CLOUD_PLATFORM_SCOPE, access_token
from app.services.providers.registry import request_json

logger = logging.getLogger(__name__)

_ENDPOINT = (
    "https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
    "/locations/{location}/publishers/google/models/{model}:generateContent"
)


def _extract(payload: Any) -> str:
    """Junta as partes de texto do primeiro candidato.

    Tolerante de propósito: a resposta pode vir sem `candidates` (filtro de
    segurança do Gemini) ou sem `parts`. Nada disso é exceção: é texto vazio, e
    quem chama já sabe cair no determinístico."""
    if not isinstance(payload, dict):
        return ""
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()


class VertexTextProvider:
    name = "vertex"

    async def complete(self, prompt: str, *, locale: str) -> str:
        if not settings.vertex_project:
            raise ProviderUnavailable("vertex: VERTEX_PROJECT ausente")

        token = await access_token(CLOUD_PLATFORM_SCOPE)
        url = _ENDPOINT.format(
            location=settings.vertex_location,
            project=settings.vertex_project,
            model=settings.vertex_model,
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # Temperatura baixa: boletim de plantão é registro clínico, e o que
            # se quer do modelo é redação do que ESTÁ no esqueleto, não variedade.
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 512},
        }
        data = await request_json(
            "POST",
            url,
            provider=self.name,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        return _extract(data)
