import uuid
from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_session,
    get_tenant_obj,
    require,
    require_any,
    require_read_any,
)
from app.core.errors import AppError
from app.models.dose_rule import DoseRule
from app.models.price_list_item import PriceListItem
from app.permissions import CHARGES_WRITE, PRESCRIPTION_CREATE, PRICE_LIST_MANAGE, can
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.schemas.price_list_item import (
    DoseRuleIn,
    DoseRuleOut,
    PriceListItemCreate,
    PriceListItemOut,
    PriceListItemUpdate,
)
from app.services.audit import ActorInfo, AuditService

router = APIRouter(prefix="/api/v1/price-list", tags=["price-list"])


@router.get("", response_model=Page[PriceListItemOut])
async def list_items(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    # Três papéis, um dado: o administrador curadoria a tabela, quem prescreve
    # lê o preço para preencher a prescrição, quem lança conta lê para saber o
    # que está lançando. Fecha para o técnico e para a estação sem PIN.
    _actor: Annotated[
        ActorInfo, Depends(require_read_any(PRICE_LIST_MANAGE, PRESCRIPTION_CREATE, CHARGES_WRITE))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
    include_inactive: bool = False,
) -> Page[PriceListItemOut]:
    stmt = sa.select(PriceListItem).where(PriceListItem.clinic_id == auth.clinic_id)
    if not include_inactive:
        stmt = stmt.where(PriceListItem.is_active.is_(True))
    rows, next_cursor = await paginate(
        session, stmt, id_column=PriceListItem.id, limit=limit, cursor=cursor
    )

    # Quantas posologias conferidas cada item tem, numa consulta só. Uma por
    # item seria N+1 numa tela que lista o catálogo inteiro; e sem o número, a
    # lista não responde "quais fármacos ainda não calculam dose", que é o
    # trabalho pendente de quem administra o catálogo.
    conferidas: dict[uuid.UUID, int] = {}
    if rows:
        contagem = await session.execute(
            sa.select(DoseRule.price_list_item_id, sa.func.count())
            .where(
                DoseRule.clinic_id == auth.clinic_id,
                DoseRule.price_list_item_id.in_([row.id for row in rows]),
                DoseRule.is_active.is_(True),
                DoseRule.reviewed_at.is_not(None),
            )
            .group_by(DoseRule.price_list_item_id)
        )
        conferidas = dict(contagem.all())

    items = []
    for row in rows:
        out = PriceListItemOut.model_validate(row)
        out.reviewed_dose_rules = conferidas.get(row.id, 0)
        items.append(out)
    return Page[PriceListItemOut](items=items, next_cursor=next_cursor)


@router.get("/{item_id}", response_model=PriceListItemOut)
async def get_item(
    item_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    _actor: Annotated[
        ActorInfo, Depends(require_read_any(PRICE_LIST_MANAGE, PRESCRIPTION_CREATE, CHARGES_WRITE))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PriceListItemOut:
    item = await get_tenant_obj(session, PriceListItem, item_id, auth.clinic_id)
    return PriceListItemOut.model_validate(item)


@router.post("", response_model=PriceListItemOut, status_code=201)
async def create_item(
    payload: PriceListItemCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PRICE_LIST_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PriceListItemOut:
    item = PriceListItem(clinic_id=auth.clinic_id, **payload.model_dump())
    session.add(item)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="price_list_item_created",
        entity_type="price_list_item",
        entity_id=item.id,
        after=AuditService.snapshot(item),
    )
    await session.commit()
    return PriceListItemOut.model_validate(item)


@router.patch("/{item_id}", response_model=PriceListItemOut)
async def update_item(
    item_id: uuid.UUID,
    payload: PriceListItemUpdate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(require(PRICE_LIST_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PriceListItemOut:
    """Reajuste. NÃO toca em conta já lançada: quem cobra é a cópia gravada na
    prescrição e no charge_item (spec: preço copiado, nunca referenciado)."""
    item = await get_tenant_obj(session, PriceListItem, item_id, auth.clinic_id)
    before = AuditService.snapshot(item)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="price_list_item_updated",
        entity_type="price_list_item",
        entity_id=item.id,
        before=before,
        after=AuditService.snapshot(item),
    )
    await session.commit()
    return PriceListItemOut.model_validate(item)


# --- Posologia --------------------------------------------------------------
#
# Mora junto do item de preço porque é lá que o fármaco já é definido uma vez.
# Uma tela separada de "posologias" seria um segundo cadastro do mesmo remédio,
# e a divergência entre os dois é só questão de tempo.


@router.get("/{item_id}/dose-rules", response_model=list[DoseRuleOut])
async def list_dose_rules(
    item_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[DoseRuleOut]:
    await get_tenant_obj(session, PriceListItem, item_id, auth.clinic_id)
    rows = list(
        (
            await session.execute(
                sa.select(DoseRule)
                .where(
                    DoseRule.clinic_id == auth.clinic_id,
                    DoseRule.price_list_item_id == item_id,
                )
                .order_by(DoseRule.species.asc().nulls_last())
            )
        ).scalars()
    )
    return [DoseRuleOut.model_validate(row) for row in rows]


@router.put("/{item_id}/dose-rules", response_model=DoseRuleOut)
async def upsert_dose_rule(
    item_id: uuid.UUID,
    payload: DoseRuleIn,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    # A tabela é do administrador; a dose é de quem prescreve. Os dois podem
    # cadastrar; só o segundo pode dizer que conferiu.
    actor: Annotated[ActorInfo, Depends(require_any(PRICE_LIST_MANAGE, PRESCRIPTION_CREATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DoseRuleOut:
    """Cadastra ou atualiza a posologia de (item, espécie, via).

    `reviewed` só pega quando quem marca tem registro no conselho: dizer que uma
    dose foi conferida é um ato clínico, e uma regra não conferida não
    pré-preenche nada na prescrição."""
    await get_tenant_obj(session, PriceListItem, item_id, auth.clinic_id)
    changes = payload.model_dump(exclude={"reviewed"})

    rule = (
        await session.execute(
            sa.select(DoseRule).where(
                DoseRule.clinic_id == auth.clinic_id,
                DoseRule.price_list_item_id == item_id,
                DoseRule.species.is_(payload.species)
                if payload.species is None
                else DoseRule.species == payload.species,
                DoseRule.route.is_(payload.route)
                if payload.route is None
                else DoseRule.route == payload.route,
            )
        )
    ).scalar_one_or_none()

    before = AuditService.snapshot(rule) if rule is not None else None
    if rule is None:
        rule = DoseRule(clinic_id=auth.clinic_id, price_list_item_id=item_id, **changes)
        session.add(rule)
    else:
        for field, value in changes.items():
            setattr(rule, field, value)

    if payload.reviewed:
        # Conferir uma dose é ato de quem tem registro: o técnico e o
        # administrador cadastram a tabela, mas não assinam a posologia.
        if not can(actor.role, PRESCRIPTION_CREATE):
            raise AppError("forbidden", 403, capability=PRESCRIPTION_CREATE, role=actor.role)
        rule.reviewed_at = datetime.now(UTC)
        rule.reviewed_by = actor.membership_id
        rule.reviewed_by_name = actor.name
    elif rule.reviewed_at is not None and _muda_a_dose(before, changes):
        # Mexeu no número: a conferência anterior não vale mais. Manter o selo
        # faria o sistema afirmar que alguém conferiu um valor que nunca viu.
        rule.reviewed_at = None
        rule.reviewed_by = None
        rule.reviewed_by_name = None

    await session.flush()
    await AuditService.record(
        session,
        clinic_id=auth.clinic_id,
        actor=actor,
        action="dose_rule_saved",
        entity_type="dose_rule",
        entity_id=rule.id,
        before=before,
        after=AuditService.snapshot(rule),
    )
    await session.commit()
    return DoseRuleOut.model_validate(rule)


_CAMPOS_DE_DOSE = (
    "dose_min_per_kg",
    "dose_max_per_kg",
    "dose_default_per_kg",
    "fixed_dose_mg",
    "max_total_mg",
)


def _muda_a_dose(before: dict | None, changes: dict) -> bool:
    if before is None:
        return False
    return any(str(before.get(campo)) != str(changes.get(campo)) for campo in _CAMPOS_DE_DOSE)
