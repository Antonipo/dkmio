"""Pagination support for DynamoDB query/scan results.

Provides :class:`QueryResult`, the return type for all query and scan
operations. It wraps DynamoDB response data and exposes it through
a Pythonic iterable interface with pagination metadata.
"""

from __future__ import annotations

from typing import Any


class QueryResult:
    """Wraps DynamoDB query/scan results with pagination support.

    ``QueryResult`` is iterable, indexable, and supports ``len()`` and
    ``bool()``, so it can be used directly in ``for`` loops, list
    comprehensions, and truthiness checks.

    To paginate, check :attr:`last_key` and pass it to
    :meth:`~dkmio.query.QueryBuilder.start_from` on the next query::

        result = orders.query(user_id="u1").limit(10).execute()
        for item in result:
            print(item)

        if result.last_key:
            next_page = orders.query(user_id="u1").limit(10).start_from(result.last_key).execute()

    Attributes:
        items: List of result items as normalized Python dicts.
        last_key: The ``LastEvaluatedKey`` for pagination, or ``None``
            if there are no more pages.
        count: Number of items returned in this result.
        scanned_count: Number of items evaluated by DynamoDB before
            applying filter expressions.
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
        """Iterate over the result items."""
        return iter(self.items)

    def __len__(self) -> int:
        """Return the number of items in the result."""
        return len(self.items)

    def __getitem__(self, index):
        """Access a result item by index or slice."""
        return self.items[index]

    def __bool__(self) -> bool:
        """Return ``True`` if the result contains at least one item."""
        return len(self.items) > 0

    def __repr__(self) -> str:
        return f"QueryResult(count={self.count}, has_more={self.last_key is not None})"
