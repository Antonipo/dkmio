"""Field descriptors for DynamoDB table definitions.

This module provides the building blocks for declaring DynamoDB table
schemas in Python classes:

- :class:`PK` — partition key descriptor
- :class:`SK` — sort key descriptor with comparison helpers
- :class:`Index` / :class:`LSI` — secondary index descriptors
- :class:`TTL` — time-to-live descriptor

Example::

    class Orders(db.Table):
        __table_name__ = "orders"
        pk = PK("user_id")
        sk = SK("order_id")
        by_status = Index("status-index", pk="status", sk="created_at")
        expires = TTL("ttl")
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


class PK:
    """Partition key descriptor.

    Declares which DynamoDB attribute serves as the partition key
    for a table definition.

    Args:
        attribute_name: The DynamoDB attribute name for the partition key.

    Example::

        class Users(db.Table):
            __table_name__ = "users"
            pk = PK("user_id")
    """

    def __init__(self, attribute_name: str) -> None:
        self.attribute_name = attribute_name

    def __repr__(self) -> str:
        return f"PK({self.attribute_name!r})"


@dataclass
class SKCondition:
    """Represents a condition on a sort key, produced by :class:`SK` comparison methods.

    This is an internal data structure passed to
    :meth:`~dkmio.expressions.ExpressionBuilder.build_key_condition`
    to generate a ``KeyConditionExpression``.

    Attributes:
        operator: The comparison operator name (e.g. ``"eq"``, ``"begins_with"``).
        values: Tuple of operand values for the condition.
    """

    operator: str
    values: tuple[Any, ...]


class SK:
    """Sort key descriptor with comparison methods for ``KeyConditionExpression``.

    Provides a fluent API for building sort key conditions used in
    :meth:`~dkmio.query.QueryBuilder.where`.

    Args:
        attribute_name: The DynamoDB attribute name for the sort key.

    Example::

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        # Query with sort key condition
        orders.query(user_id="u1").where(begins_with="ord_")
    """

    def __init__(self, attribute_name: str) -> None:
        self.attribute_name = attribute_name

    def eq(self, value: Any) -> SKCondition:
        """Sort key equals *value* (``sk = :v``)."""
        return SKCondition("eq", (value,))

    def gt(self, value: Any) -> SKCondition:
        """Sort key greater than *value* (``sk > :v``)."""
        return SKCondition("gt", (value,))

    def gte(self, value: Any) -> SKCondition:
        """Sort key greater than or equal to *value* (``sk >= :v``)."""
        return SKCondition("gte", (value,))

    def lt(self, value: Any) -> SKCondition:
        """Sort key less than *value* (``sk < :v``)."""
        return SKCondition("lt", (value,))

    def lte(self, value: Any) -> SKCondition:
        """Sort key less than or equal to *value* (``sk <= :v``)."""
        return SKCondition("lte", (value,))

    def between(self, low: Any, high: Any) -> SKCondition:
        """Sort key between *low* and *high* inclusive (``sk BETWEEN :lo AND :hi``)."""
        return SKCondition("between", (low, high))

    def begins_with(self, prefix: Any) -> SKCondition:
        """Sort key starts with *prefix* (``begins_with(sk, :prefix)``)."""
        return SKCondition("begins_with", (prefix,))

    def __repr__(self) -> str:
        return f"SK({self.attribute_name!r})"


class Index:
    """GSI/LSI descriptor for table definitions.

    Args:
        index_name: The name of the index in DynamoDB.
        pk: Attribute name for the index partition key.
        sk: Attribute name for the index sort key (optional).
        projection: Index projection configuration.
            - None or "ALL": all attributes projected.
            - "KEYS_ONLY": only key attributes projected.
            - list of str: INCLUDE projection with those attributes.
    """

    def __init__(
        self,
        index_name: str,
        pk: str,
        sk: str | None = None,
        projection: str | list[str] | None = None,
    ) -> None:
        self.index_name = index_name
        self.pk = pk
        self.sk = sk
        self.projection = projection
        # Will be set by TableMeta
        self._table_class: type | None = None
        self._field_name: str | None = None

    @property
    def projection_type(self) -> str:
        """Return the projection type: ALL, KEYS_ONLY, or INCLUDE."""
        if self.projection is None or self.projection == "ALL":
            return "ALL"
        if self.projection == "KEYS_ONLY":
            return "KEYS_ONLY"
        if isinstance(self.projection, list):
            return "INCLUDE"
        raise ValueError(f"Invalid projection: {self.projection!r}")

    def available_attributes(self, table_pk: str, table_sk: str | None) -> set[str]:
        """Return the set of attributes available in this index.

        For ALL projection, returns empty set (meaning all are available).
        For KEYS_ONLY and INCLUDE, returns the specific available attributes.
        """
        if self.projection_type == "ALL":
            return set()  # empty means "all available"

        attrs: set[str] = set()
        # Index keys are always available
        attrs.add(self.pk)
        if self.sk:
            attrs.add(self.sk)
        # Table keys are always available in any index
        attrs.add(table_pk)
        if table_sk:
            attrs.add(table_sk)

        if self.projection_type == "INCLUDE" and isinstance(self.projection, list):
            attrs.update(self.projection)

        return attrs

    def __repr__(self) -> str:
        parts = [repr(self.index_name), f"pk={self.pk!r}"]
        if self.sk:
            parts.append(f"sk={self.sk!r}")
        if self.projection:
            parts.append(f"projection={self.projection!r}")
        return f"Index({', '.join(parts)})"


class LSI(Index):
    """Local Secondary Index descriptor.

    LSIs share the table's partition key. Only the sort key needs to be specified.

    Args:
        index_name: The name of the index in DynamoDB.
        sk: Attribute name for the index sort key.
        projection: Index projection configuration (same as Index).
    """

    def __init__(
        self,
        index_name: str,
        sk: str,
        projection: str | list[str] | None = None,
    ) -> None:
        super().__init__(index_name, pk="", sk=sk, projection=projection)

    def __repr__(self) -> str:
        parts = [repr(self.index_name), f"sk={self.sk!r}"]
        if self.projection:
            parts.append(f"projection={self.projection!r}")
        return f"LSI({', '.join(parts)})"


class TTL:
    """Time-To-Live descriptor for automatic item expiration.

    Declares which DynamoDB attribute holds the TTL epoch timestamp.
    Provides a convenience method :meth:`from_now` to compute future
    expiration timestamps.

    Args:
        attribute_name: The DynamoDB attribute name for the TTL field.

    Example::

        class Sessions(db.Table):
            __table_name__ = "sessions"
            pk = PK("session_id")
            expires = TTL("ttl")

        sessions = Sessions()
        sessions.put(session_id="s1", ttl=sessions.ttl.from_now(hours=2))
    """

    def __init__(self, attribute_name: str) -> None:
        self.attribute_name = attribute_name

    def from_now(
        self,
        days: int = 0,
        hours: int = 0,
        minutes: int = 0,
        seconds: int = 0,
    ) -> int:
        """Calculate an epoch timestamp relative to the current time.

        All duration parameters are cumulative. For example,
        ``from_now(days=1, hours=6)`` returns a timestamp 30 hours
        from now.

        Args:
            days: Number of days to add.
            hours: Number of hours to add.
            minutes: Number of minutes to add.
            seconds: Number of seconds to add.

        Returns:
            Unix epoch timestamp (integer) representing the computed
            future point in time.
        """
        total_seconds = (
            days * 86400 + hours * 3600 + minutes * 60 + seconds
        )
        return int(time.time()) + total_seconds

    def __repr__(self) -> str:
        return f"TTL({self.attribute_name!r})"
