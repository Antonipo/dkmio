"""Pagination support for DynamoDB query/scan results."""

from __future__ import annotations

from typing import Any


class QueryResult:
    """Wraps DynamoDB query/scan results with pagination support.

    Attributes:
        items: List of result items (dicts).
        last_key: LastEvaluatedKey for pagination, or None if no more pages.
        count: Number of items returned.
        scanned_count: Number of items scanned (before filter).
    """

    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        last_key: dict[str, Any] | None = None,
        count: int | None = None,
        scanned_count: int | None = None,
    ) -> None:
        self.items = items or []
        self.last_key = last_key
        self.count = count if count is not None else len(self.items)
        self.scanned_count = scanned_count if scanned_count is not None else self.count

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index):
        return self.items[index]

    def __bool__(self) -> bool:
        return len(self.items) > 0

    def __repr__(self) -> str:
        return f"QueryResult(count={self.count}, has_more={self.last_key is not None})"
