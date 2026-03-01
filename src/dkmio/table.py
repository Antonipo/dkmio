"""Table base class and metaclass for DynamoDB OKM."""

from __future__ import annotations

import logging
from typing import Any

from .exceptions import MissingKeyError, ValidationError
from .fields import LSI, PK, SK, TTL, Index
from .serialize import normalize_item

logger = logging.getLogger("dkmio")


class IndexAccessor:
    """Provides .query() on an index, bound to a table instance."""

    def __init__(self, index: Index, table_instance: Table) -> None:
        self.index = index
        self._table = table_instance

    def query(self, **kwargs: Any):
        """Start a query on this index."""
        from .query import QueryBuilder

        # The kwargs should contain the index PK value
        pk_attr = self.index.pk
        if pk_attr not in kwargs:
            raise MissingKeyError(
                f"query() on index '{self.index.index_name}' requires "
                f"partition key: {pk_attr}"
            )

        pk_value = kwargs.pop(pk_attr)
        if kwargs:
            extra = ", ".join(kwargs.keys())
            raise ValidationError(
                f"Unexpected arguments for index query: {extra}. "
                f"Use .filter() for additional conditions."
            )

        return QueryBuilder(
            table=self._table,
            pk_name=pk_attr,
            pk_value=pk_value,
            index=self.index,
        )


class SKDescriptor:
    """Descriptor that exposes SK at class level for sort key conditions."""

    def __init__(self, sk: SK) -> None:
        self.sk = sk

    def __get__(self, obj: Any, objtype: type | None = None) -> SK:
        return self.sk


class IndexDescriptor:
    """Descriptor that returns an IndexAccessor when accessed on an instance."""

    def __init__(self, index: Index) -> None:
        self.index = index

    def __set_name__(self, owner: type, name: str) -> None:
        self.index._field_name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> Index | IndexAccessor:
        if obj is None:
            return self.index
        return IndexAccessor(self.index, obj)


class TableMeta(type):
    """Metaclass that collects PK, SK, Index, TTL from class body."""

    def __new__(
        mcs, name: str, bases: tuple[type, ...], namespace: dict[str, Any]
    ) -> TableMeta:
        pk = None
        sk = None
        indexes: dict[str, Index] = {}
        ttl = None

        # Collect from bases first
        for base in bases:
            if hasattr(base, "_pk") and base._pk is not None:
                pk = base._pk
            if hasattr(base, "_sk") and base._sk is not None:
                sk = base._sk
            if hasattr(base, "_indexes"):
                indexes.update(base._indexes)
            if hasattr(base, "_ttl") and base._ttl is not None:
                ttl = base._ttl

        # Collect from current class body (overrides bases)
        descriptors: dict[str, SKDescriptor | IndexDescriptor] = {}
        for attr_name, value in list(namespace.items()):
            if isinstance(value, PK):
                pk = value
            elif isinstance(value, SK):
                sk = value
                descriptors[attr_name] = SKDescriptor(value)
            elif isinstance(value, Index):
                indexes[attr_name] = value
                value._field_name = attr_name
                # Replace with descriptor
                descriptors[attr_name] = IndexDescriptor(value)
            elif isinstance(value, TTL):
                ttl = value

        # Set LSI pk to table pk automatically
        if pk is not None:
            for idx in indexes.values():
                if isinstance(idx, LSI):
                    idx.pk = pk.attribute_name

        namespace.update(descriptors)
        namespace["_pk"] = pk
        namespace["_sk"] = sk
        namespace["_indexes"] = indexes
        namespace["_ttl"] = ttl

        cls = super().__new__(mcs, name, bases, namespace)
        # Set table class reference on indexes
        for idx in indexes.values():
            idx._table_class = cls

        if namespace.get("__table_name__"):
            logger.debug("table class %s (%s)", name, namespace["__table_name__"])

        return cls


class Table(metaclass=TableMeta):
    """Base class for DynamoDB table definitions.

    Subclass and define PK, SK, Index, TTL fields:

        class Orders(Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")
    """

    __table_name__: str = ""
    _db: Any = None  # Set by DynamoDB.Table binding
    _pk: PK | None = None
    _sk: SK | None = None
    _indexes: dict[str, Index] = {}
    _ttl: TTL | None = None

    def __init__(self) -> None:
        if not self.__table_name__:
            raise ValidationError(
                f"{self.__class__.__name__} must define __table_name__"
            )

    @property
    def _dynamo_table(self) -> Any:
        """Get the boto3 Table resource for this table."""
        if self._db is None:
            raise ValidationError(
                "Table is not bound to a DynamoDB instance. "
                "Use db.Table as base class or set _db."
            )
        if not hasattr(self, "_cached_dynamo_table"):
            self._cached_dynamo_table = self._db.resource.Table(self.__table_name__)
        return self._cached_dynamo_table

    @property
    def ttl(self) -> TTL | None:
        return self._ttl

    def _extract_keys(self, kwargs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Extract key attributes from kwargs. Returns (keys, remaining)."""
        keys = {}
        remaining = dict(kwargs)

        if self._pk is not None:
            pk_name = self._pk.attribute_name
            if pk_name in remaining:
                keys[pk_name] = remaining.pop(pk_name)

        if self._sk is not None:
            sk_name = self._sk.attribute_name
            if sk_name in remaining:
                keys[sk_name] = remaining.pop(sk_name)

        return keys, remaining

    def _validate_full_key(self, keys: dict[str, Any], operation: str) -> None:
        """Validate that the full key (PK + SK if exists) is provided."""
        if self._pk is None:
            raise ValidationError("Table has no PK defined")

        pk_name = self._pk.attribute_name
        if pk_name not in keys:
            raise MissingKeyError(
                f"{operation}() requires the full key. "
                f"Missing: {pk_name}."
            )

        if self._sk is not None:
            sk_name = self._sk.attribute_name
            if sk_name not in keys:
                hint = " Use .query() to search by partition key." if operation == "get" else ""
                raise MissingKeyError(
                    f"{operation}() requires the full key. "
                    f"Missing: {sk_name}.{hint}"
                )

    def get(self, **kwargs: Any) -> dict[str, Any] | None:
        """Get an item by its full key. Returns dict or None.

        Args:
            **kwargs: Key attributes (PK + SK if table has SK).
                Special kwargs:
                - select: list of attribute names to project.
                - consistent: if True, use strongly consistent read.
        """
        from botocore.exceptions import ClientError

        from .expressions import ExpressionBuilder
        from .operations import map_boto3_error

        select = kwargs.pop("select", None)
        consistent = kwargs.pop("consistent", False)

        keys, extra = self._extract_keys(kwargs)
        self._validate_full_key(keys, "get")

        if extra:
            raise ValidationError(
                f"get() only accepts key attributes. Unexpected: {', '.join(extra.keys())}"
            )

        params: dict[str, Any] = {"Key": keys}

        if select:
            builder = ExpressionBuilder()
            proj = builder.build_projection(select)
            params["ProjectionExpression"] = proj
            names = builder.get_names()
            if names:
                params["ExpressionAttributeNames"] = names

        if consistent:
            params["ConsistentRead"] = True

        try:
            response = self._dynamo_table.get_item(**params)
        except ClientError as e:
            raise map_boto3_error(e) from e
        raw: dict[str, Any] | None = response.get("Item")
        return normalize_item(raw) if raw is not None else None

    def query(self, **kwargs: Any):
        """Start a Query operation on the table's primary key."""
        from .query import QueryBuilder

        if self._pk is None:
            raise ValidationError("Table has no PK defined")

        pk_name = self._pk.attribute_name
        if pk_name not in kwargs:
            raise MissingKeyError(
                f"query() requires partition key: {pk_name}"
            )

        pk_value = kwargs.pop(pk_name)
        if kwargs:
            extra = ", ".join(kwargs.keys())
            raise ValidationError(
                f"Unexpected arguments for query: {extra}. "
                f"Use .filter() for additional conditions."
            )

        return QueryBuilder(table=self, pk_name=pk_name, pk_value=pk_value)

    def scan(self):
        """Start a Scan operation."""
        from .query import QueryBuilder

        return QueryBuilder(table=self, pk_name=None, pk_value=None, is_scan=True)

    def put(self, **kwargs: Any) -> dict[str, Any] | None:
        """Put an item into the table."""
        from .operations import execute_put

        return execute_put(self, kwargs)

    def update(self, **kwargs: Any) -> dict[str, Any] | None:
        """Update an item in the table."""
        from .operations import execute_update

        return execute_update(self, kwargs)

    def delete(self, **kwargs: Any) -> dict[str, Any] | None:
        """Delete an item from the table."""
        from .operations import execute_delete

        return execute_delete(self, kwargs)

    def batch_read(
        self,
        keys: list[dict[str, Any]],
        select: list[str] | None = None,
        consistent: bool = False,
    ) -> list[dict[str, Any] | None]:
        """Batch get multiple items by key.

        Returns items in the same order as input keys.
        Items not found are returned as None.
        """
        from .operations import execute_batch_read

        return execute_batch_read(self, keys, select=select, consistent=consistent)

    def batch_write(self):
        """Return a BatchWriter context manager."""
        from .operations import BatchWriter

        return BatchWriter(self)
