"""Type definitions for dkmio internals."""

from __future__ import annotations

from typing import Any, Protocol

from .fields import PK, SK


class TableProtocol(Protocol):
    """Minimal interface that internal modules need from a Table instance."""

    __table_name__: str
    _pk: PK | None
    _sk: SK | None
    _db: Any

    @property
    def _dynamo_table(self) -> Any: ...

    def _extract_keys(
        self, kwargs: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def _validate_full_key(
        self, keys: dict[str, Any], operation: str
    ) -> None: ...
