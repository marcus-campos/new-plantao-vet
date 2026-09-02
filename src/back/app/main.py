import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import audit as audit_routes
from app.api.routes import auth as auth_routes
from app.api.routes import board as board_routes
from app.api.routes import charges as charge_routes
from app.api.routes import clinic_settings as clinic_settings_routes
from app.api.routes import devices as device_routes
from app.api.routes import handover as handover_routes
from app.api.routes import hospitalizations as hospitalization_routes
from app.api.routes import kennels as kennel_routes
from app.api.routes import memberships as membership_routes
from app.api.routes import owner_contacts as owner_contact_routes
from app.api.routes import owners as owner_routes
from app.api.routes import patients as patient_routes
from app.api.routes import platform as platform_routes
from app.api.routes import prescriptions as prescription_routes
from app.api.routes import price_list as price_list_routes
from app.api.routes import progress_notes as progress_note_routes
from app.api.routes import records as record_routes
from app.api.routes import shifts as shift_routes
from app.api.routes import station_devices as station_device_routes
from app.api.routes import tasks as task_routes
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import AppError, app_error_handler, validation_error_handler
from app.services.providers.registry import validate as validate_providers
from app.workers.scheduler import build_scheduler, hourly

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Liga o único job do sistema: estender a janela de aprazamento.

    Sem ele as tarefas existem só na janela gravada no momento da prescrição, e
    a ficha de uma internação mais longa que essa janela esvazia sozinha, o
    "patients randomly fall off my board" que a pesquisa (§6) aponta como dano
    irrecuperável de confiança. O job já existia, completo e testado; faltava
    alguém chamá-lo.

    Roda uma vez na subida, além do intervalo: um processo que ficou fora do ar
    precisa alcançar o presente antes da próxima hora cheia.
    """
    # Um nome errado em AI_TEXT_PROVIDER só apareceria no primeiro fechamento
    # de turno. Falha na subida, como uma URL de banco errada falharia.
    validate_providers()

    if not settings.scheduler_enabled:
        yield
        return
    try:
        await hourly(async_session_factory, now=datetime.now(UTC))
    except Exception:  # noqa: BLE001 (a API sobe mesmo se o catch-up falhar)
        logger.exception("catch-up do aprazamento falhou na subida")
    scheduler = build_scheduler(async_session_factory)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="PlantaoVet API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    app.include_router(auth_routes.router)
    app.include_router(membership_routes.router)
    app.include_router(kennel_routes.router)
    app.include_router(owner_routes.router)
    app.include_router(patient_routes.router)
    app.include_router(hospitalization_routes.router)
    app.include_router(prescription_routes.router)
    app.include_router(task_routes.router)
    app.include_router(board_routes.router)
    app.include_router(audit_routes.router)
    app.include_router(price_list_routes.router)
    app.include_router(charge_routes.router)
    app.include_router(progress_note_routes.router)
    app.include_router(progress_note_routes.compliance_router)
    app.include_router(record_routes.router)
    app.include_router(shift_routes.router)
    app.include_router(shift_routes.notes_router)
    app.include_router(handover_routes.router)
    app.include_router(clinic_settings_routes.router)
    app.include_router(owner_contact_routes.router)
    app.include_router(device_routes.router)
    app.include_router(station_device_routes.router)
    app.include_router(platform_routes.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
