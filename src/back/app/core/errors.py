from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Códigos de erro da v1 (brief, regra transversal 1). Todo código novo entra aqui.
ERROR_CODES: frozenset[str] = frozenset(
    {
        "invalid_credentials",
        "token_expired",
        "operator_required",
        "pin_locked_out",
        "pin_duplicate",
        "pin_same_as_current",
        "station_key_rotated",
        # --- Aparelhos compartilhados -----------------------------------
        # `device_locked` é 423 e NÃO carrega `retry_after_seconds`: esperar
        # não resolve. O que resolve é um administrador liberar o aparelho, e
        # a interface precisa dizer isso em vez de sugerir nova tentativa.
        "device_locked",
        "device_revoked",
        # --- Plataforma (quem vende e dá suporte) ------------------------
        # A porta fecha no LOGIN, nunca no meio da sessão: uma clínica com
        # paciente internado não perde a prescrição por causa de boleto.
        "clinic_suspended",
        "email_taken",
        "slug_taken",
        "unknown_plan",
        "plan_retired",
        "plan_code_taken",
        "plan_in_use",
        "whatsapp_opt_in_required",
        "whatsapp_webhook_unverified",
        "whatsapp_send_failed",
        "whatsapp_not_configured",
        "task_already_processed",
        "early_confirmation_required",
        "prn_guardrail",
        "fasting_active",
        "consent_reason_required",
        "outcome_note_required",
        "pending_tasks_confirmation_required",
        "not_found",
        "forbidden",
        "validation_error",
        "identifier_kind_not_allowed",
        "identifier_invalid",
        "identifier_taken",
        "compliance_profile_in_use",
        # --- Cadastro pelo site e fim do teste ---------------------------
        # `trial_expired` é 403 e não 402: não há nada a pagar dentro do
        # produto ainda. Quem recebe precisa saber que a escrita parou e por
        # quê, e a leitura continua aberta.
        "signup_rate_limited",
        "trial_expired",
        # --- Nota de áudio e provedores de IA ---------------------------
        "audio_empty",
        "audio_too_large",
        "audio_type_unsupported",
        "transcription_unavailable",
        "ai_provider_unknown",
    }
)


class AppError(Exception):
    def __init__(self, code: str, status_code: int = 400, **params: Any) -> None:
        self.code = code
        self.status_code = status_code
        self.params = params
        super().__init__(code)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "params": exc.params}},
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Sem prosa: só a localização e o tipo do erro; quem traduz é o cliente.
    fields = [
        {"loc": [str(part) for part in error["loc"]], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "params": {"fields": fields}}},
    )
