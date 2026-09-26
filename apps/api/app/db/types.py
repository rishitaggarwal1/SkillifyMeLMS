"""Custom column types."""

from typing import Any

from sqlalchemy.types import UserDefinedType


class LTree(UserDefinedType[str]):
    """PostgreSQL `ltree` (extension): dotted label paths like `dsa.arrays.two_pointers`, with
    index-backed ancestor/descendant queries (`path <@ 'dsa'`). Values travel as plain strings."""

    cache_ok = True

    def get_col_spec(self, **_kw: Any) -> str:
        return "LTREE"
