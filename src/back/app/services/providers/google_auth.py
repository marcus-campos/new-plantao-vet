"""Token de acesso da service account do Google: Vertex E FCM usam o MESMO.

São dois produtos diferentes com a mesma autenticação: um JSON de service
account (ou as credenciais da própria VM) trocado por um access token OAuth2 no
escopo pedido. Ter dois lugares mintando token daria dois caches, dois bugs de
expiração e duas formas de o deploy no GCP quebrar, e por isso a Vertex importa
`CLOUD_PLATFORM_SCOPE` daqui e o push importa `FIREBASE_MESSAGING_SCOPE`.

Duas decisões que não são detalhe:

* **Cache até pouco antes de vencer.** O token vale ~1h; mintar a cada chamada
  colocaria um round trip a mais em toda passagem de plantão e em todo push, e
  o Google passa a rate-limitar quem faz isso.
* **Transporte próprio sobre httpx.** O adaptador oficial (`google.auth.
  transport.requests`) exige a lib `requests`, que este backend não instala,
  e não vale arrastar um segundo cliente HTTP só para renovar token.
"""

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import google.auth.transport
import httpx

from app.core.config import settings
from app.services.providers import ProviderUnavailable

logger = logging.getLogger(__name__)

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
FIREBASE_MESSAGING_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

#: Margem antes do vencimento. Um token que expira no voo derruba a chamada com
#: 401 depois de já ter mandado o corpo; renovar cedo é mais barato que tratar.
_EXPIRY_SKEW = timedelta(seconds=120)

#: Vida assumida quando o provedor não devolve `expiry`. Curta de propósito: é
#: melhor mintar de novo cedo demais do que usar token morto.
_ASSUMED_LIFETIME = timedelta(minutes=5)

_REFRESH_TIMEOUT = 20.0


@dataclass(frozen=True)
class _Token:
    value: str
    expires_at: datetime


#: Cache por ESCOPO: o token do Vertex não serve ao FCM e vice-versa.
_cache: dict[str, _Token] = {}
#: Lock de thread, não de asyncio: o refresh do google-auth é síncrono e roda em
#: `asyncio.to_thread`. Um `asyncio.Lock` de módulo se prenderia ao primeiro
#: event loop e estouraria no seguinte (a suíte cria um loop por teste).
_lock = threading.Lock()


class _HttpxResponse(google.auth.transport.Response):
    """Resposta do google-auth por cima de uma resposta do httpx."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._response.headers)

    @property
    def data(self) -> bytes:
        return self._response.content


class _HttpxRequest(google.auth.transport.Request):
    """O transporte que o google-auth usa para bater no endpoint de token."""

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> _HttpxResponse:
        response = httpx.request(
            method,
            url,
            content=body,
            headers=headers,
            timeout=timeout or _REFRESH_TIMEOUT,
        )
        return _HttpxResponse(response)


def _load_credentials(scope: str) -> Any:
    from google.auth import default as application_default
    from google.oauth2 import service_account

    if settings.google_credentials_file:
        return service_account.Credentials.from_service_account_file(
            settings.google_credentials_file, scopes=[scope]
        )
    # Sem arquivo de chave, Application Default Credentials: num deploy no GCP a
    # própria VM/Cloud Run já responde por si, e distribuir um JSON de service
    # account num container é o jeito mais comum de vazar credencial.
    credentials, _project = application_default(scopes=[scope])
    return credentials


def _mint(scope: str) -> _Token:
    from google.auth.exceptions import GoogleAuthError

    try:
        credentials = _load_credentials(scope)
        credentials.refresh(_HttpxRequest())
    except (GoogleAuthError, httpx.HTTPError, OSError, ValueError) as exc:
        # Credencial ausente ou inválida NÃO é erro de API: quem chama cai no
        # caminho determinístico. Integração fora do ar não derruba o produto.
        raise ProviderUnavailable(f"credencial do Google indisponível (escopo {scope})") from exc

    token = getattr(credentials, "token", None)
    if not token:
        raise ProviderUnavailable(f"credencial do Google sem token (escopo {scope})")

    expiry = getattr(credentials, "expiry", None)
    if expiry is None:
        expires_at = datetime.now(UTC) + _ASSUMED_LIFETIME
    else:
        # O google-auth devolve `expiry` naive em UTC.
        expires_at = expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry
    return _Token(value=token, expires_at=expires_at)


def _fresh(token: _Token | None, now: datetime) -> bool:
    return token is not None and token.expires_at - _EXPIRY_SKEW > now


def _mint_cached(scope: str) -> _Token:
    with _lock:
        # Confere de novo dentro do lock: duas corrotinas que chegaram juntas ao
        # cache vazio não precisam gastar dois round trips.
        cached = _cache.get(scope)
        if _fresh(cached, datetime.now(UTC)):
            return cached  # type: ignore[return-value]
        token = _mint(scope)
        _cache[scope] = token
        logger.info("google_token_minted scope=%s expires_at=%s", scope, token.expires_at)
        return token


async def access_token(scope: str = CLOUD_PLATFORM_SCOPE) -> str:
    """Access token válido para `scope`, do cache quando ainda dá tempo.

    Levanta `ProviderUnavailable` quando não há credencial configurada, nunca
    erro de API."""
    if _fresh(_cache.get(scope), datetime.now(UTC)):
        return _cache[scope].value
    # `refresh` do google-auth é bloqueante: fora da thread do event loop ele
    # seguraria todas as outras requisições da API durante o round trip.
    token = await asyncio.to_thread(_mint_cached, scope)
    return token.value


def reset_cache() -> None:
    """Esquece os tokens em cache. Existe para o teste e para a troca de chave."""
    with _lock:
        _cache.clear()
