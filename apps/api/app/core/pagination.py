"""Cursor (keyset) pagination, used by every list endpoint.

Offset pagination degrades linearly with depth and skips/duplicates rows under concurrent writes.
Keyset pagination is O(log n) per page via the index. Because our IDs are UUIDv7 (time-ordered and
unique), `id` alone is a stable sort key for "newest first" / "oldest first" listings.

Usage in a repository:

    items, next_cursor = await paginate_by_id(session, select(Course), Course.id, params)
    return CursorPage(items=items, next_cursor=next_cursor)
"""

import base64
import binascii
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import InvalidCursorError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
_CURSOR_VERSION = 1


class CursorPage[T](BaseModel):
    items: list[T]
    next_cursor: str | None = Field(
        description="Opaque cursor for the next page; null when there are no more items."
    )


class CursorParams(BaseModel):
    limit: int = DEFAULT_PAGE_SIZE
    cursor: str | None = None


def _cursor_params(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> CursorParams:
    return CursorParams(limit=limit, cursor=cursor)


PageParams = Annotated[CursorParams, Depends(_cursor_params)]


def encode_cursor(values: dict[str, Any]) -> str:
    raw = json.dumps({"v": _CURSOR_VERSION, **values}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursorError from exc
    if not isinstance(data, dict) or data.pop("v", None) != _CURSOR_VERSION:
        raise InvalidCursorError
    return data


async def paginate_by_id[M](
    session: AsyncSession,
    stmt: Select[M],
    id_column: InstrumentedAttribute[UUID],
    params: CursorParams,
    *,
    descending: bool = True,
) -> tuple[list[M], str | None]:
    """Fetch one page of `stmt` ordered by the UUIDv7 `id_column`. Fetches limit+1 rows to know
    whether a next page exists without a COUNT query."""
    if params.cursor is not None:
        try:
            after = UUID(str(decode_cursor(params.cursor)["id"]))
        except (KeyError, ValueError) as exc:
            raise InvalidCursorError from exc
        stmt = stmt.where(id_column < after if descending else id_column > after)

    order = id_column.desc() if descending else id_column.asc()
    rows = list((await session.scalars(stmt.order_by(order).limit(params.limit + 1))).all())

    if len(rows) <= params.limit:
        return rows, None
    page = rows[: params.limit]
    last_id = getattr(page[-1], id_column.key)
    return page, encode_cursor({"id": str(last_id)})
