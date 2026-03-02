"""Type definitions for dkmio internals.

Provides structural typing protocols used across the package to
decouple modules without circular imports.
"""

from __future__ import annotations

from typing import Any, Protocol

from .fields import PK, SK


class TableProtocol(Protocol):
    """Structural typing protocol that defines the minimal interface
    internal modules require from a Table instance.

    This protocol enables type-safe interactions between modules
    (operations, query, transactions) and the Table class without
    creating circular import dependencies.

    Attributes:
        __table_name__: The DynamoDB table name.
        _pk: The partition key descriptor, or ``None`` if not defined.
        _sk: The sort key descriptor, or ``None`` if not defined.
        _db: The :class:`~dkmio.client.DynamoDB` instance bound to this table.
    """

    __table_name__: str
    _pk: PK | None
    _sk: SK | None
    _db: Any

    @property
    def _dynamo_table(self) -> Any:
        """Return the underlying boto3 ``dynamodb.Table`` resource."""
        ...

    def _extract_keys(
        self, kwargs: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Separate key attributes from non-key attributes in *kwargs*.

        Args:
            kwargs: Dictionary potentially containing PK/SK values
                along with other attributes.

        Returns:
            A tuple of ``(keys, remaining)`` where *keys* contains
            only the PK/SK attributes found and *remaining* holds
            everything else.
        """
        ...

    def _validate_full_key(
        self, keys: dict[str, Any], operation: str
    ) -> None:
        """Validate that *keys* contains the full primary key (PK + SK).

        Args:
            keys: Dictionary of key attributes to validate.
            operation: Name of the calling operation (used in error messages).

        Raises:
            MissingKeyError: If a required key attribute is missing.
            ValidationError: If the table has no PK defined.
        """
        ...
