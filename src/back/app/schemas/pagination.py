from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


async def paginate(
    session: AsyncSession,
    stmt: sa.Select,
    *,
    id_column: Any,
    limit: int,
    cursor: str | None,
    descending: bool = False,
) -> tuple[list[Any], str | None]:
    """Paginação por cursor: pede limit+1 linhas para saber se há próxima página."""
    if cursor is not None:
        stmt = stmt.where(id_column < cursor if descending else id_column > cursor)
    stmt = stmt.order_by(id_column.desc() if descending else id_column.asc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars())
    if len(rows) > limit:
        rows = rows[:limit]
        return rows, str(rows[-1].id)
    return rows, None
