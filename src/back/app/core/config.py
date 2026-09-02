from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://plantaovet:plantaovet@localhost:5432/plantaovet"
    jwt_secret: str = "dev-secret-change-me"
    env: str = "dev"
    # Origens do front (web em dev); em produção vem por env var.
    cors_origins: str = "http://localhost:5173,http://localhost:4173"
    #: O job que estende a janela de aprazamento. Desligado nos testes, que
    #: chamam `extend_scheduling_window` direto e não querem um agendador vivo
    #: no event loop do teste.
    scheduler_enabled: bool = True

    # ---- IA -----------------------------------------------------------------
    #: Trocar de provedor é trocar ESTA linha.
    #:
    #: `stub` é o texto determinístico que já existia, e é o default de
    #: propósito: sem credencial, o sistema funciona inteiro, só sem a redação.
    #: A regra do produto não muda com o provedor: o esqueleto do boletim é a
    #: verdade e nunca é redigido por modelo nenhum.
    ai_text_provider: str = "stub"
    ai_speech_provider: str = "stub"

    #: Vertex AI (Gemini). A credencial é a service account do GCP.
    vertex_project: str | None = None
    vertex_location: str = "us-central1"
    vertex_model: str = "gemini-2.5-flash"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_transcribe_model: str = "gpt-4o-transcribe"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    #: Caminho do JSON da service account do Google. Serve a Vertex E ao FCM:
    #: é o mesmo mecanismo de autenticação nos dois.
    google_credentials_file: str | None = None

    #: Teto de tempo de qualquer chamada de IA. A passagem de plantão não pode
    #: ficar pendurada esperando um provedor: se estourar, cai no determinístico.
    ai_timeout_seconds: float = 20.0

    # ---- WhatsApp (Meta Cloud API) ------------------------------------------
    #: Sem `whatsapp_phone_number_id` e `whatsapp_token` o envio NÃO acontece e
    #: o contato é gravado como `failed`, nunca como enviado. O prontuário não
    #: pode afirmar uma entrega que não houve.
    whatsapp_phone_number_id: str | None = None
    whatsapp_token: str | None = None
    whatsapp_api_version: str = "v21.0"
    #: Template aprovado pela Meta para mensagem iniciada pela clínica. Fora da
    #: janela de 24h, texto livre é recusado pela API.
    whatsapp_template_name: str = "boletim_internacao"
    #: Segredo que valida o webhook de recebimento (assinatura X-Hub-Signature-256).
    whatsapp_app_secret: str | None = None
    whatsapp_verify_token: str | None = None

    # ---- Push (Firebase Cloud Messaging) ------------------------------------
    fcm_project: str | None = None
    #: Orçamento de alertas (pesquisa §4: só 5–13% dos alarmes de UTI são
    #: acionáveis). Notificação ativa é para dose CRÍTICA fora da janela e para
    #: intercorrência com "avisar o veterinário"; o resto é escalonamento visual
    #: no painel. Este número é o teto por pessoa por hora.
    push_max_per_hour: int = 6


settings = Settings()
