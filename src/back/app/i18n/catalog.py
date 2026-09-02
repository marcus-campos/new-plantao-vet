import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_I18N_DIR = Path(__file__).parent
SOURCE_LOCALE = "pt-BR"


@lru_cache
def _load(locale: str) -> dict[str, str]:
    path = _I18N_DIR / f"{locale}.json"
    if not path.exists():
        # Fallback para o idioma-fonte quando o locale não tem catálogo.
        path = _I18N_DIR / f"{SOURCE_LOCALE}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def translate(key: str, locale: str, **params: Any) -> str:
    catalog = _load(locale)
    template = catalog[key]  # chave ausente levanta KeyError: falha no teste,
    return template.format(**params)  # nunca produção silenciosa


def catalog_keys(locale: str) -> set[str]:
    return set(_load(locale))
