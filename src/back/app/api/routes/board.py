from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_session
from app.models import Clinic
from app.schemas.board import BoardAttention, BoardOut, BoardRow
from app.schemas.task import TaskOut
from app.services.board import BoardService

router = APIRouter(prefix="/api/v1/board", tags=["board"])


@router.get("", response_model=BoardOut)
async def board(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BoardOut:
    """O que precisa de atenção agora, do pior para o melhor.

    Uma ida ao servidor: turno em andamento, exceções com motivo, contadores e
    a fila em baldes de tempo. Antes eram três chamadas (`/board`,
    `/compliance/alerts`, `/shifts`) e a ordenação era por nome do paciente.
    """
    now = datetime.now(UTC)
    clinic = await session.get(Clinic, auth.clinic_id)
    data = await BoardService.build(
        session,
        clinic=clinic,
        now=now,
        viewer_membership_id=auth.membership.id if auth.membership else None,
    )
    return BoardOut(
        now=data["now"],
        timezone=data["timezone"],
        shifts=data["shifts"],
        totals=data["totals"],
        rows=[
            BoardRow(
                **{
                    key: value
                    for key, value in row.items()
                    if key not in ("next_task", "attention")
                },
                next_task=TaskOut.from_task(row["next_task"], now) if row["next_task"] else None,
                attention=(
                    BoardAttention.model_validate(row["attention"]) if row["attention"] else None
                ),
            )
            for row in data["rows"]
        ],
    )
