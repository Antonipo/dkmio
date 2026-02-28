"""Field descriptors for DynamoDB table definitions."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


class PK:
    """Partition key descriptor."""

    def __init__(self, attribute_name: str) -> None:
        self.attribute_name = attribute_name

    def __repr__(self) -> str:
        return f"PK({self.attribute_name!r})"


@dataclass
class SKCondition:
    """Condition on a sort key, produced by SK comparison methods."""

    operator: str
    values: tuple[Any, ...]


class SK:
    """Sort key descriptor with comparison methods for KeyConditionExpression."""

    def __init__(self, attribute_name: str) -> None:
        self.attribute_name = attribute_name

    def eq(self, value: Any) -> SKCondition:
        return SKCondition("eq", (value,))

    def gt(self, value: Any) -> SKCondition:
        return SKCondition("gt", (value,))

    def gte(self, value: Any) -> SKCondition:
        return SKCondition("gte", (value,))

    def lt(self, value: Any) -> SKCondition:
        return SKCondition("lt", (value,))

    def lte(self, value: Any) -> SKCondition:
        return SKCondition("lte", (value,))

    def between(self, low: Any, high: Any) -> SKCondition:
        return SKCondition("between", (low, high))

    def begins_with(self, prefix: Any) -> SKCondition:
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
    """TTL (Time To Live) descriptor.

    Args:
        attribute_name: The name of the TTL attribute in DynamoDB.
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
        """Calculate an epoch timestamp from now + the given duration."""
        total_seconds = (
            days * 86400 + hours * 3600 + minutes * 60 + seconds
        )
        return int(time.time()) + total_seconds

    def __repr__(self) -> str:
        return f"TTL({self.attribute_name!r})"
