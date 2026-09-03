"""O plano de teste existe e faz o que `Plan.trial_days` promete."""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.models.plan import Plan
from app.services.plans import PlanService
from tests.factories import make_clinic


@pytest.mark.asyncio
async def test_migracao_semeia_o_plano_trial(session):
    plan = await session.scalar(sa.select(Plan).where(Plan.code == "trial"))
    assert plan is not None
    assert plan.trial_days == 14
    assert plan.bed_limit == 10
    assert plan.price_minor == 0
    assert plan.is_active is True


@pytest.mark.asyncio
async def test_aplicar_o_plano_trial_marca_o_fim_do_teste(session):
    clinic = await make_clinic(session)
    plan = await PlanService.get(session, "trial")
    agora = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

    PlanService.apply(clinic, plan, now=agora)

    assert clinic.plan_tier == "trial"
    assert clinic.bed_limit == 10
    assert clinic.subscription_status == "trial"
    assert clinic.trial_ends_at == datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_o_plano_trial_e_atribuivel(session):
    # Aposentado nao receberia clinica nova, e e por ele que todo mundo entra.
    plan = await PlanService.assignable(session, "trial")
    assert plan.code == "trial"
