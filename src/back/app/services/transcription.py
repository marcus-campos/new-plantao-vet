"""Transcrição da nota de áudio, e o descarte do áudio.

Spec §2, literal: *"O áudio bruto é apagado após a transcrição confirmada (só o
texto integra o prontuário — LGPD, voz de funcionário)"*. Aqui isso é mecânico,
não uma promessa: os bytes existem em memória durante a chamada ao provedor e
somem com a requisição. Não há coluna, bucket, fila nem log que os guarde.

E o inverso da regra da narrativa: **transcrição não tem caminho
determinístico**. Não dá para adivinhar o que a pessoa falou, então provedor
fora do ar vira erro explícito (`transcription_unavailable`) e o app pede para
digitar. Nota vazia ou texto inventado no prontuário é risco clínico.
"""

import logging

from fastapi import UploadFile

from app.core.errors import AppError
from app.services.providers import ProviderUnavailable, speech_provider

logger = logging.getLogger(__name__)

#: Teto do upload. Nota de plantão é falada em segundos, não em minutos, mas o
#: número não é arbitrário: 1 MiB é o limiar em que o Starlette para de manter o
#: multipart em memória e passa a gravar arquivo temporário em DISCO. Acima
#: disso o áudio bruto passaria a existir fora da memória do processo, que é
#: exatamente o que a spec proíbe.
AUDIO_MAX_BYTES = 1024 * 1024

#: Formatos que um provedor de transcrição aceita. Recusar o resto aqui evita
#: mandar 1 MiB para a OpenAI só para ela responder 400, e fecha a porta para
#: "áudio" que é outra coisa.
EXTENSION_BY_CONTENT_TYPE: dict[str, str] = {
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/mp4": "m4a",
    "audio/aac": "m4a",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}


class TranscriptionService:
    @staticmethod
    def extension_for(content_type: str | None) -> str:
        """Extensão do formato, ou `audio_type_unsupported`."""
        base = (content_type or "").split(";")[0].strip().lower()
        extension = EXTENSION_BY_CONTENT_TYPE.get(base)
        if extension is None:
            raise AppError("audio_type_unsupported", 415, content_type=base or None)
        return extension

    @staticmethod
    def upload_name(content_type: str | None) -> str:
        """Nome do arquivo mandado ao provedor: derivado do tipo, nunca do cliente.

        O provedor decide o decoder pela extensão, e o nome que vem no multipart
        é texto arbitrário de quem chamou (inclusive com `../`). Derivar do
        content-type dá um nome previsível e tira o cliente da jogada."""
        return f"note.{TranscriptionService.extension_for(content_type)}"

    @staticmethod
    async def read_capped(upload: UploadFile) -> bytes:
        """Lê o upload até o teto e recusa o que passar dele.

        Em pedaços, e não `await upload.read()`: um arquivo enorme lido inteiro
        antes da checagem já teria custado a memória que a checagem existe para
        proteger."""
        chunks: list[bytes] = []
        total = 0
        while chunk := await upload.read(64 * 1024):
            total += len(chunk)
            if total > AUDIO_MAX_BYTES:
                raise AppError("audio_too_large", 413, max_bytes=AUDIO_MAX_BYTES)
            chunks.append(chunk)
        if total == 0:
            raise AppError("audio_empty", 400)
        return b"".join(chunks)

    @staticmethod
    async def transcribe(audio: bytes, *, filename: str, locale: str) -> str:
        """O texto falado, no locale da clínica. Nunca devolve string vazia.

        Falha do provedor NÃO vira nota: a rota levanta antes de escrever
        qualquer coisa no prontuário. Gravar uma nota vazia (ou um placeholder)
        seria registrar que alguém documentou o plantão quando ninguém
        documentou."""
        # Fora do try: nome de provedor errado no `.env` é erro de deploy e sobe.
        provider = speech_provider()
        try:
            raw = await provider.transcribe(audio, filename=filename, locale=locale)
        except ProviderUnavailable as exc:
            logger.info("transcricao_indisponivel provider=%s motivo=%s", provider.name, exc)
            raise AppError("transcription_unavailable", 503, provider=provider.name) from exc
        except Exception as exc:  # noqa: BLE001 (sem texto, e o app sabe pedir para digitar)
            logger.exception("transcricao_falhou provider=%s", provider.name)
            raise AppError("transcription_unavailable", 503, provider=provider.name) from exc

        text = (raw or "").strip()
        if not text:
            # Silêncio, ruído ou recusa do provedor. Continua sendo "não houve
            # transcrição", e não houve nota.
            logger.info("transcricao_vazia provider=%s bytes=%d", provider.name, len(audio))
            raise AppError("transcription_unavailable", 503, provider=provider.name)
        return text
