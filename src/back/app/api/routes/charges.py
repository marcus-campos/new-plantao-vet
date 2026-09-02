import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_session,
    get_tenant_obj,
    require,
    require_read,
)
from app.models.charge_item import ChargeItem, ChargeSource
from app.models.hospitalization import Hospitalization
from app.models.price_list_item import PriceListItem
from app.permissions import CHARGES_READ, CHARGES_WRITE
from app.schemas.charge import ChargeItemOut, ManualChargeCreate, StatementOut
from app.services.audit import ActorInfo, AuditService
from app.services.charges import ChargeService

router = APIRouter(prefix="/api/v1", tags=["charges"])


@router.get("/hospitalizations/{hospitalization_id}/charges", response_model=StatementOut)
async def statement(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    # O extrato era leitura aberta: o técnico (a quem a tabela de papéis nega
    # `charges.read`) abria a conta inteira e exportava em CSV.
    _actor: Annotated[ActorInfo, Depends(require_read(CHARGES_READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StatementOut:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    data = await ChargeService.statement(
        session, hospitalization_id=hospitalization.id, clinic_id=auth.clinic_id
    )
    return StatementOut.model_validate(data)


@router.post(
    "/hospitalizations/{hospitalization_id}/charges",
    response_model=ChargeItemOut,
    status_code=201,
)
async def create_manual_charge(
    hospitalization_id: uuid.UUID,
    payload: ManualChargeCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(CHARGES_WRITE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChargeItemOut:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    description = payload.description
    unit_price_minor = payload.unit_price_minor
    if payload.price_list_item_id is not None:
        # Regra transversal 2: FK de body validada contra o tenant.
        item = await get_tenant_obj(
            session, PriceListItem, payload.price_list_item_id, auth.clinic_id
        )
        # Cópia, não referência: o extrato não pode mudar num reajuste futuro.
        description = description or item.name
        unit_price_minor = unit_price_minor if unit_price_minor is not None else item.price_minor

    charge = ChargeItem(
        clinic_id=auth.clinic_id,
        hospitalization_id=hospitalization.id,
        price_list_item_id=payload.price_list_item_id,
        description=description,
        quantity=payload.quantity,
        unit_price_minor=unit_price_minor,
        total_minor=ChargeService.total_minor(unit_price_minor, payload.quantity),
        charged_at=payload.charged_at or datetime.now(UTC),
        source=ChargeSource.manual,
    )
    session.add(charge)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="charge_recorded",
        entity_type="charge_item",
        entity_id=charge.id,
        after=AuditService.snapshot(charge),
        extra={"source": ChargeSource.manual.value},
    )
    await session.commit()
    return ChargeItemOut.model_validate(charge)
