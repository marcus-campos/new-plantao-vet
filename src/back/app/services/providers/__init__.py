"""Provedores de IA, trocáveis por variável de ambiente.

`AI_TEXT_PROVIDER=vertex` vira `openai` vira `anthropic` vira `stub` sem tocar
numa linha de domínio. Foi por isso que aqui não entrou framework de agente: o
caso de uso são DUAS chamadas HTTP (um texto a partir de um esqueleto
determinístico e um áudio a transcrever), e um `Protocol` com uma implementação
por provedor entrega a troca por `.env` de forma mais direta, com uma árvore de
dependências que cabe num backend clínico.

Três regras que nenhum provedor pode quebrar:

1. **O esqueleto é a verdade.** A narrativa é rascunho; se o provedor cair,
   ficar lento ou devolver bobagem, a passagem de plantão continua de pé com os
   números contados do banco.
2. **Nada de dado de contato do tutor sai daqui.** O que vai para o provedor é
   o esqueleto clínico: contadores, eventos, notas do plantão. Telefone, CPF e
   endereço nunca entram no prompt (LGPD, spec §8.4: DPA com o fornecedor).
3. **O texto sai no locale da clínica**, nunca no idioma do prompt.
"""

from typing import Protocol

from app.core.config import settings


class TextProvider(Protocol):
    """Redige um texto a partir de um pedido já montado."""

    name: str

    async def complete(self, prompt: str, *, locale: str) -> str: ...


class SpeechProvider(Protocol):
    """Transcreve áudio falado em texto."""

    name: str

    async def transcribe(self, audio: bytes, *, filename: str, locale: str) -> str: ...


class ProviderUnavailable(RuntimeError):
    """O provedor não está configurado ou não respondeu.

    Nunca vira erro de API: quem chama cai no comportamento determinístico. Uma
    integração de IA fora do ar não pode derrubar a passagem de plantão."""


def text_provider() -> TextProvider:
    """O provedor de texto escolhido em `AI_TEXT_PROVIDER`."""
    from app.services.providers.registry import build_text

    return build_text(settings.ai_text_provider)


def speech_provider() -> SpeechProvider:
    """O provedor de transcrição escolhido em `AI_SPEECH_PROVIDER`."""
    from app.services.providers.registry import build_speech

    return build_speech(settings.ai_speech_provider)
