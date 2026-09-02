"""Provedores de IA: troca por `.env`, queda sem dano e o áudio que não fica.

Nenhum teste aqui toca a internet: o `httpx` dos provedores é substituído por
um `MockTransport` através do gancho `registry.TRANSPORT`, e o token do Google
por um dublê. Um teste de integração que sai para a rede não é teste: é uma
suíte que quebra quando a OpenAI tem um dia ruim.

As três garantias sob teste são as três regras de `providers/__init__.py`:
o esqueleto é a verdade, dado de tutor não sai daqui, e o texto sai no locale
da clínica.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa

from app.api.deps import get_session
from app.core.config import settings
from app.core.errors import ERROR_CODES, AppError
from app.main import create_app
from app.models.shift_note import ShiftNote
from app.services import transcription as transcription_module
from app.services.narrative import NarrativeService
from app.services.providers import ProviderUnavailable, speech_provider, text_provider
from app.services.providers import registry as registry_module
from app.services.providers.anthropic import AnthropicTextProvider
from app.services.providers.openai import OpenAISpeechProvider, OpenAITextProvider
from app.services.providers.registry import (
    StubSpeechProvider,
    StubTextProvider,
    UnknownProvider,
    build_text,
)
from app.services.providers.vertex import VertexTextProvider
from app.services.transcription import AUDIO_MAX_BYTES, TranscriptionService
from tests.factories import (
    make_clinic,
    make_hospitalization,
    make_membership,
    make_owner,
    make_patient,
    make_user,
)
from tests.helpers import bearer, personal_token

SKELETON = {
    "period": {"since": "2026-08-31T07:00:00+00:00", "until": "2026-08-31T19:00:00+00:00"},
    "tasks": {"done": 11, "partial": 0, "not_done": 1, "pending": 3, "overdue": 1},
    "events": [
        {
            "id": "e1",
            "title": "Vômito bilioso",
            "category": "observation",
            "status": "done",
            "scheduled_for": "2026-08-31T14:32:00+00:00",
            "executed_at": "2026-08-31T14:32:00+00:00",
        }
    ],
    "prescription_changes": {"created": [], "adjusted": [], "suspended": []},
    "notes": [
        {"id": "n1", "author_name": "Dra. Paula", "text": "Mucosa pálida.", "source": "typed"}
    ],
}


@pytest.fixture(autouse=True)
def isolated_providers(monkeypatch):
    """Cada teste começa sem provedor, sem transporte e sem token em cache."""
    from app.services.providers import google_auth

    monkeypatch.setattr(settings, "ai_text_provider", "stub", raising=False)
    monkeypatch.setattr(settings, "ai_speech_provider", "stub", raising=False)
    monkeypatch.setattr(registry_module, "TRANSPORT", None, raising=False)
    google_auth.reset_cache()
    yield
    google_auth.reset_cache()


def fake_transport(monkeypatch, handler) -> list[httpx.Request]:
    """Instala um transporte de mentira e devolve a lista de requisições vistas."""
    seen: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(registry_module, "TRANSPORT", httpx.MockTransport(_handle))
    return seen


# --- 1. A troca por .env ------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("stub", StubTextProvider),
        ("vertex", VertexTextProvider),
        ("openai", OpenAITextProvider),
        ("anthropic", AnthropicTextProvider),
    ],
)
def test_registry_de_texto_devolve_o_provedor_do_env(monkeypatch, name, expected):
    monkeypatch.setattr(settings, "ai_text_provider", name)
    assert isinstance(text_provider(), expected)
    assert isinstance(build_text(name), expected)


def test_registry_de_texto_ignora_caixa_e_espaco(monkeypatch):
    monkeypatch.setattr(settings, "ai_text_provider", "  Anthropic ")
    assert isinstance(text_provider(), AnthropicTextProvider)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("stub", StubSpeechProvider), ("openai", OpenAISpeechProvider)],
)
def test_registry_de_fala_devolve_o_provedor_do_env(monkeypatch, name, expected):
    monkeypatch.setattr(settings, "ai_speech_provider", name)
    assert isinstance(speech_provider(), expected)


def test_nome_desconhecido_falha_alto_em_vez_de_cair_no_stub(monkeypatch):
    """Cair no stub calado seria pior: a clínica acharia que ligou o Vertex."""
    monkeypatch.setattr(settings, "ai_text_provider", "vertexai")
    with pytest.raises(UnknownProvider) as exc:
        text_provider()
    assert exc.value.code == "ai_provider_unknown"
    assert exc.value.status_code == 500
    assert exc.value.params["provider"] == "vertexai"
    assert "vertex" in exc.value.params["known"]


def test_nome_de_fala_desconhecido_tambem_falha(monkeypatch):
    monkeypatch.setattr(settings, "ai_speech_provider", "whisper")
    with pytest.raises(UnknownProvider):
        speech_provider()


def test_codigo_do_provedor_desconhecido_esta_registrado():
    # ADR-0004: todo código devolvido pela API existe em ERROR_CODES.
    assert "ai_provider_unknown" in ERROR_CODES
    for code in ("audio_too_large", "audio_type_unsupported", "transcription_unavailable"):
        assert code in ERROR_CODES


def test_validate_recusa_configuracao_invalida_na_subida(monkeypatch):
    monkeypatch.setattr(settings, "ai_text_provider", "gpt")
    with pytest.raises(UnknownProvider):
        registry_module.validate()


def test_validate_aceita_a_configuracao_padrao():
    registry_module.validate()


# --- 2. Cada provedor sobre HTTP falso ---------------------------------------


async def test_vertex_monta_url_e_manda_bearer(monkeypatch):
    monkeypatch.setattr(settings, "vertex_project", "plantaovet-prod")
    monkeypatch.setattr(settings, "vertex_location", "us-central1")
    monkeypatch.setattr(settings, "vertex_model", "gemini-2.5-flash")

    async def _token(scope: str) -> str:
        return "token-de-mentira"

    monkeypatch.setattr("app.services.providers.vertex.access_token", _token)
    seen = fake_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "Dia estável."}]}}]}
        ),
    )

    assert await VertexTextProvider().complete("prompt", locale="pt-BR") == "Dia estável."
    url = str(seen[0].url)
    assert url.startswith("https://us-central1-aiplatform.googleapis.com/v1/projects/")
    assert "plantaovet-prod" in url and "gemini-2.5-flash:generateContent" in url
    assert seen[0].headers["authorization"] == "Bearer token-de-mentira"


async def test_vertex_sem_projeto_nao_chama_nada(monkeypatch):
    monkeypatch.setattr(settings, "vertex_project", None)
    seen = fake_transport(monkeypatch, lambda request: httpx.Response(200, json={}))
    with pytest.raises(ProviderUnavailable):
        await VertexTextProvider().complete("prompt", locale="pt-BR")
    assert seen == []


async def test_openai_texto_le_a_primeira_escolha(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-teste")
    seen = fake_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": " Dia estável. "}}]}
        ),
    )
    assert await OpenAITextProvider().complete("prompt", locale="pt-BR") == "Dia estável."
    assert str(seen[0].url) == "https://api.openai.com/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-teste"


async def test_anthropic_manda_o_header_de_versao(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-teste")
    seen = fake_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"content": [{"type": "text", "text": "Dia estável."}]}
        ),
    )
    assert await AnthropicTextProvider().complete("prompt", locale="pt-BR") == "Dia estável."
    assert seen[0].headers["anthropic-version"]
    assert seen[0].headers["x-api-key"] == "sk-ant-teste"


@pytest.mark.parametrize("api_key_field", ["openai_api_key", "anthropic_api_key"])
async def test_provedor_sem_chave_recusa_sem_sair_para_a_rede(monkeypatch, api_key_field):
    monkeypatch.setattr(settings, api_key_field, None)
    seen = fake_transport(monkeypatch, lambda request: httpx.Response(200, json={}))
    provider = (
        OpenAITextProvider() if api_key_field.startswith("openai") else AnthropicTextProvider()
    )
    with pytest.raises(ProviderUnavailable):
        await provider.complete("prompt", locale="pt-BR")
    assert seen == []


async def test_erro_http_do_provedor_nao_vaza_o_corpo(monkeypatch):
    """O corpo de erro costuma trazer o prompt de volta, e prompt é dado clínico."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-teste")
    fake_transport(
        monkeypatch,
        lambda request: httpx.Response(500, json={"error": "prompt: Thor, UTI 03, CPF ..."}),
    )
    with pytest.raises(ProviderUnavailable) as exc:
        await OpenAITextProvider().complete("prompt", locale="pt-BR")
    assert "Thor" not in str(exc.value)
    assert "500" in str(exc.value)


async def test_stub_de_texto_recusa_para_o_chamador_cair_no_deterministico():
    with pytest.raises(ProviderUnavailable):
        await StubTextProvider().complete("prompt", locale="pt-BR")


# --- 3. O prompt --------------------------------------------------------------


def test_prompt_nao_leva_dado_de_contato_do_tutor():
    """Regra 2 de providers/__init__: telefone, CPF e endereço nunca saem daqui.

    O esqueleto abaixo vem contaminado de propósito: a allowlist tem de segurar
    campo que ninguém previu, porque a denylist só segura o que já se conhece."""
    contaminado = {
        **SKELETON,
        "owner": {
            "name": "Marcos Silva",
            "phone_e164": "+5511999990000",
            "tax_id": "123.456.789-00",
            "address": "Rua das Acácias, 42",
        },
        "owner_contacts": [{"phone_e164": "+5511988887777"}],
    }
    prompt = NarrativeService.build_prompt(contaminado, "pt-BR")
    for vazamento in ("+5511999990000", "123.456.789-00", "Rua das Acácias", "+5511988887777"):
        assert vazamento not in prompt
    assert "owner" not in NarrativeService.clinical_payload(contaminado)
    # E o clínico continua indo: sem esqueleto o modelo não tem o que redigir.
    assert "Vômito bilioso" in prompt
    assert "Dra. Paula" in prompt


def test_prompt_pede_o_locale_da_clinica_explicitamente():
    assert "en-GB" in NarrativeService.build_prompt(SKELETON, "en-GB")
    assert "pt-BR" in NarrativeService.build_prompt(SKELETON, "pt-BR")


def test_prompt_proibe_inventar_e_pede_paragrafo_com_pendencias():
    prompt = NarrativeService.build_prompt(SKELETON, "pt-BR")
    assert "Never add a finding" in prompt
    assert "paragraph" in prompt
    assert "pending and overdue" in prompt


# --- 4. A narrativa nunca derruba a passagem ---------------------------------


class _FailingProvider:
    name = "falso"

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def complete(self, prompt: str, *, locale: str) -> str:
        raise self._error


class _RespondingProvider:
    name = "falso"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.seen: list[str] = []

    async def complete(self, prompt: str, *, locale: str) -> str:
        self.seen.append(prompt)
        return self.answer


def _install(monkeypatch, provider) -> None:
    monkeypatch.setattr(settings, "ai_text_provider", "openai")
    monkeypatch.setattr(registry_module, "TEXT_BUILDERS", {"openai": lambda: provider})


async def test_sem_provedor_o_boletim_sai_deterministico():
    # `AI_TEXT_PROVIDER=stub` é o default: o produto roda inteiro sem credencial.
    assert await NarrativeService.draft(SKELETON, "pt-BR") == NarrativeService.deterministic(
        SKELETON, "pt-BR"
    )


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailable("sem credencial"),
        httpx.ConnectTimeout("estourou o tempo"),
        RuntimeError("o provedor explodiu de um jeito novo"),
    ],
)
async def test_falha_do_provedor_cai_no_deterministico(monkeypatch, error):
    _install(monkeypatch, _FailingProvider(error))
    assert await NarrativeService.draft(SKELETON, "pt-BR") == NarrativeService.deterministic(
        SKELETON, "pt-BR"
    )


@pytest.mark.parametrize("resposta", ["", "   ", "\n```\n```\n", "x" * 4000])
async def test_resposta_inutilizavel_cai_no_deterministico(monkeypatch, resposta):
    _install(monkeypatch, _RespondingProvider(resposta))
    assert await NarrativeService.draft(SKELETON, "pt-BR") == NarrativeService.deterministic(
        SKELETON, "pt-BR"
    )


async def test_rascunho_bom_e_devolvido_e_o_prompt_carrega_o_esqueleto(monkeypatch):
    provider = _RespondingProvider("Dia estável com melhora gradual. Atenção à PA das 22h.")
    _install(monkeypatch, provider)
    assert await NarrativeService.draft(SKELETON, "pt-BR") == (
        "Dia estável com melhora gradual. Atenção à PA das 22h."
    )
    assert "Vômito bilioso" in provider.seen[0]


async def test_cerca_de_markdown_e_limpa_em_vez_de_descartada(monkeypatch):
    _install(monkeypatch, _RespondingProvider("```\nDia estável com melhora gradual.\n```"))
    assert await NarrativeService.draft(SKELETON, "pt-BR") == "Dia estável com melhora gradual."


async def test_nome_de_provedor_errado_sobe_em_vez_de_virar_rascunho_falso(monkeypatch):
    monkeypatch.setattr(settings, "ai_text_provider", "gemini")
    with pytest.raises(UnknownProvider):
        await NarrativeService.draft(SKELETON, "pt-BR")


# --- 5. Token do Google, em cache --------------------------------------------


async def test_token_do_google_e_reaproveitado_ate_perto_do_vencimento(monkeypatch):
    from app.services.providers import google_auth

    chamadas: list[str] = []

    def _mint(scope: str):
        chamadas.append(scope)
        return google_auth._Token(
            value=f"token-{len(chamadas)}", expires_at=datetime.now(UTC) + timedelta(hours=1)
        )

    monkeypatch.setattr(google_auth, "_mint", _mint)
    primeiro = await google_auth.access_token(google_auth.CLOUD_PLATFORM_SCOPE)
    segundo = await google_auth.access_token(google_auth.CLOUD_PLATFORM_SCOPE)
    assert primeiro == segundo == "token-1"
    # Mintar a cada chamada colocaria um round trip a mais em toda passagem.
    assert chamadas == [google_auth.CLOUD_PLATFORM_SCOPE]

    # Escopo diferente é token diferente: o do Vertex não serve ao FCM.
    await google_auth.access_token(google_auth.FIREBASE_MESSAGING_SCOPE)
    assert chamadas == [google_auth.CLOUD_PLATFORM_SCOPE, google_auth.FIREBASE_MESSAGING_SCOPE]


async def test_token_vencido_e_renovado(monkeypatch):
    from app.services.providers import google_auth

    chamadas: list[str] = []

    def _mint(scope: str):
        chamadas.append(scope)
        return google_auth._Token(
            value=f"token-{len(chamadas)}", expires_at=datetime.now(UTC) + timedelta(seconds=10)
        )

    monkeypatch.setattr(google_auth, "_mint", _mint)
    assert await google_auth.access_token() == "token-1"
    # 10s de vida está dentro da margem de segurança: renova.
    assert await google_auth.access_token() == "token-2"


async def test_sem_credencial_o_token_e_indisponivel_nao_erro_de_api(monkeypatch):
    from app.services.providers import google_auth

    monkeypatch.setattr(settings, "google_credentials_file", None)

    def _boom(scope: str):
        from google.auth.exceptions import DefaultCredentialsError

        raise DefaultCredentialsError("sem ADC nesta máquina")

    monkeypatch.setattr(google_auth, "_load_credentials", _boom)
    with pytest.raises(ProviderUnavailable):
        await google_auth.access_token()


# --- 6. Transcrição: validação de entrada ------------------------------------


def test_tipo_nao_audio_e_recusado():
    for content_type in ("application/pdf", "text/plain", "application/octet-stream", None):
        with pytest.raises(AppError) as exc:
            TranscriptionService.upload_name(content_type)
        assert exc.value.code == "audio_type_unsupported"


def test_nome_do_arquivo_vem_do_tipo_nao_do_cliente():
    assert TranscriptionService.upload_name("audio/m4a") == "note.m4a"
    assert TranscriptionService.upload_name("audio/mpeg; codecs=mp3") == "note.mp3"


def test_teto_do_audio_fica_abaixo_do_spool_em_disco_do_starlette():
    # Acima de 1 MiB o multipart do Starlette grava arquivo temporário, e o
    # áudio bruto passaria a existir fora da memória (LGPD, spec §2).
    assert AUDIO_MAX_BYTES <= 1024 * 1024


async def test_transcricao_indisponivel_nunca_devolve_texto_vazio(monkeypatch):
    monkeypatch.setattr(settings, "ai_speech_provider", "stub")
    with pytest.raises(AppError) as exc:
        await TranscriptionService.transcribe(b"\x00\x01", filename="note.m4a", locale="pt-BR")
    assert exc.value.code == "transcription_unavailable"


async def test_transcricao_em_branco_do_provedor_vira_erro(monkeypatch):
    class _Mudo:
        name = "falso"

        async def transcribe(self, audio, *, filename, locale):
            return "   "

    monkeypatch.setattr(settings, "ai_speech_provider", "openai")
    monkeypatch.setattr(registry_module, "SPEECH_BUILDERS", {"openai": _Mudo})
    with pytest.raises(AppError) as exc:
        await TranscriptionService.transcribe(b"\x00", filename="note.m4a", locale="pt-BR")
    assert exc.value.code == "transcription_unavailable"


async def test_openai_transcreve_com_a_lingua_da_clinica(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-teste")
    monkeypatch.setattr(settings, "openai_transcribe_model", "gpt-4o-transcribe")
    seen = fake_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"text": "Thor aceitou água."})
    )
    text = await OpenAISpeechProvider().transcribe(
        b"bytes-de-audio", filename="note.m4a", locale="pt-BR"
    )
    assert text == "Thor aceitou água."
    corpo = seen[0].content
    assert str(seen[0].url) == "https://api.openai.com/v1/audio/transcriptions"
    # ISO-639-1, não BCP-47: sem a língua o modelo detecta sozinho e um plantão
    # com ruído de fundo vira transcrição em outro idioma.
    assert b'name="language"\r\n\r\npt' in corpo
    assert b"gpt-4o-transcribe" in corpo
    assert b"bytes-de-audio" in corpo


# --- 7. A rota de nota de áudio ----------------------------------------------


@pytest.fixture
async def client(db_session):
    # `create_app` já monta o notes_router; o teste só troca a sessão.
    app = create_app()

    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def _cenario(session, locale="pt-BR"):
    clinic = await make_clinic(session, locale=locale)
    user = await make_user(session, name="Dra. Paula")
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    owner = await make_owner(session, clinic=clinic)
    patient = await make_patient(session, clinic=clinic, owner=owner)
    hosp = await make_hospitalization(session, clinic=clinic, patient=patient)
    await session.commit()
    return clinic, membership, hosp


def _fake_speech(monkeypatch, text="Thor aceitou água à tarde; mucosa ainda pálida."):
    recebido: dict = {}

    class _Provider:
        name = "falso"

        async def transcribe(self, audio, *, filename, locale):
            recebido["bytes"] = audio
            recebido["filename"] = filename
            recebido["locale"] = locale
            return text

    monkeypatch.setattr(settings, "ai_speech_provider", "openai")
    monkeypatch.setattr(registry_module, "SPEECH_BUILDERS", {"openai": _Provider})
    return recebido


async def test_rota_de_audio_cria_a_nota_transcrita(session, client, monkeypatch):
    clinic, membership, hosp = await _cenario(session)
    recebido = _fake_speech(monkeypatch)

    response = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/shift-notes/audio",
        files={"audio": ("gravacao-do-celular.m4a", b"bytes-de-audio", "audio/m4a")},
        headers=bearer(personal_token(membership)),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["text"] == "Thor aceitou água à tarde; mucosa ainda pálida."
    assert body["source"] == "audio"
    assert body["author_name"] == "Dra. Paula"
    assert body["hospitalization_id"] == str(hosp.id)
    # O provedor recebeu os bytes e o locale da CLÍNICA, com nome derivado do
    # content-type (o nome do cliente é entrada não confiável).
    assert recebido["bytes"] == b"bytes-de-audio"
    assert recebido["filename"] == "note.m4a"
    assert recebido["locale"] == "pt-BR"

    note = (
        await session.execute(sa.select(ShiftNote).where(ShiftNote.id == uuid.UUID(body["id"])))
    ).scalar_one()
    assert str(note.source) == "audio"


async def test_resposta_e_prontuario_nao_carregam_audio(session, client, monkeypatch):
    clinic, membership, hosp = await _cenario(session)
    _fake_speech(monkeypatch)

    response = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/shift-notes/audio",
        files={"audio": ("nota.m4a", b"bytes-de-audio", "audio/m4a")},
        headers=bearer(personal_token(membership)),
    )

    body = response.json()
    # LGPD, spec §2: só o texto integra o prontuário. Nem a resposta nem a linha
    # gravada podem trazer o áudio de volta de nenhuma forma.
    assert set(body) == {
        "id",
        "hospitalization_id",
        "shift_id",
        "membership_id",
        "author_name",
        "text",
        "source",
        "created_at",
    }
    assert "bytes-de-audio" not in response.text
    note = (
        await session.execute(sa.select(ShiftNote).where(ShiftNote.id == uuid.UUID(body["id"])))
    ).scalar_one()
    assert not any(
        isinstance(value, bytes | bytearray) for value in vars(note).values()
    )


async def test_provedor_fora_do_ar_nao_grava_nota_nenhuma(session, client, monkeypatch):
    """A regra mais importante: nunca registrar o que não aconteceu."""
    clinic, membership, hosp = await _cenario(session)
    monkeypatch.setattr(settings, "ai_speech_provider", "stub")

    response = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/shift-notes/audio",
        files={"audio": ("nota.m4a", b"bytes-de-audio", "audio/m4a")},
        headers=bearer(personal_token(membership)),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "transcription_unavailable"
    notes = (
        (await session.execute(sa.select(ShiftNote).where(ShiftNote.clinic_id == clinic.id)))
        .scalars()
        .all()
    )
    assert notes == []


async def test_rota_recusa_arquivo_que_nao_e_audio(session, client, monkeypatch):
    clinic, membership, hosp = await _cenario(session)
    _fake_speech(monkeypatch)

    response = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/shift-notes/audio",
        files={"audio": ("laudo.pdf", b"%PDF-1.7", "application/pdf")},
        headers=bearer(personal_token(membership)),
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "audio_type_unsupported"


async def test_rota_recusa_audio_absurdo(session, client, monkeypatch):
    clinic, membership, hosp = await _cenario(session)
    _fake_speech(monkeypatch)
    monkeypatch.setattr(transcription_module, "AUDIO_MAX_BYTES", 1024)

    response = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/shift-notes/audio",
        files={"audio": ("nota.m4a", b"\x00" * 4096, "audio/m4a")},
        headers=bearer(personal_token(membership)),
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "audio_too_large"


async def test_rota_de_audio_exige_a_capacidade_de_plantao(session, client, monkeypatch):
    clinic, _membership, hosp = await _cenario(session)
    _fake_speech(monkeypatch)
    user = await make_user(session)
    # O administrador toca a clínica, não o paciente: não escreve no prontuário.
    admin = await make_membership(session, clinic=clinic, user=user, role="admin")
    await session.commit()

    response = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/shift-notes/audio",
        files={"audio": ("nota.m4a", b"bytes-de-audio", "audio/m4a")},
        headers=bearer(personal_token(admin)),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_internacao_de_outra_clinica_nao_recebe_nota(session, client, monkeypatch):
    _clinic, membership, _hosp = await _cenario(session)
    _fake_speech(monkeypatch)
    outra = await make_clinic(session)
    alheia = await make_hospitalization(session, clinic=outra)
    await session.commit()

    response = await client.post(
        f"/api/v1/hospitalizations/{alheia.id}/shift-notes/audio",
        files={"audio": ("nota.m4a", b"bytes-de-audio", "audio/m4a")},
        headers=bearer(personal_token(membership)),
    )

    assert response.status_code == 404


async def test_transcrever_sem_gravar_preserva_a_revisao(client, session, monkeypatch):
    """A revisão antes de salvar não é enfeite.

    A transcrição erra, e o que ela escreve vai para o prontuário, que é
    append-only. A spec diz que o áudio é apagado depois da transcrição
    **confirmada**, e confirmar é uma pessoa ler o que saiu. Criar a nota direto
    do áudio empurraria a correção para adendo, que é registro de erro em vez de
    prevenção.
    """
    import sqlalchemy as sa

    from app.models.shift_note import ShiftNote
    from app.services import transcription as transcription_module

    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    await session.flush()

    async def _fake(audio, *, filename, locale):
        return "Mais responsivo, aceitou água."

    monkeypatch.setattr(
        transcription_module.TranscriptionService, "transcribe", staticmethod(_fake)
    )

    antes = await session.scalar(sa.select(sa.func.count()).select_from(ShiftNote))
    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/shift-notes/transcribe",
        files={"audio": ("nota.m4a", b"\x00\x01\x02", "audio/m4a")},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "Mais responsivo, aceitou água."
    # E nada foi escrito: quem decide o que entra no prontuário é a pessoa.
    assert await session.scalar(sa.select(sa.func.count()).select_from(ShiftNote)) == antes
