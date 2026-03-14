"""Table base class and metaclass for DynamoDB OKM.

Provides :class:`Table`, the base class users subclass to define
DynamoDB table schemas, along with the supporting metaclass
:class:`TableMeta` and descriptors for field binding.

Example::

    db = DynamoDB(region_name="us-east-1")

    class Orders(db.Table):
        __table_name__ = "orders"
        pk = PK("user_id")
        sk = SK("order_id")
        by_status = Index("status-index", pk="status", sk="created_at")

    orders = Orders()
    orders.put(user_id="u1", order_id="o1", status="pending", total=99)
"""

from __future__ import annotations

import logging
from typing import Any

from .exceptions import MissingKeyError, ValidationError
from .fields import LSI, PK, SK, TTL, Index
from .serialize import normalize_item

logger = logging.getLogger("dkmio")


class IndexAccessor:
    """Provides ``.query()`` on an index, bound to a table instance.

    Created automatically when accessing an :class:`~dkmio.fields.Index`
    attribute on a table instance (e.g. ``orders.by_status``).

    Args:
        index: The :class:`~dkmio.fields.Index` descriptor.
        table_instance: The :class:`Table` instance this accessor is bound to.
    """

    def __init__(self, index: Index, table_instance: Table) -> None:
        self.index = index
        self._table = table_instance

    def query(self, **kwargs: Any):
        """Start a query on this index.

        Args:
            **kwargs: Must include the index's partition key attribute
                as a keyword argument.

        Returns:
            A :class:`~dkmio.query.QueryBuilder` bound to this index.

        Raises:
            MissingKeyError: If the index partition key is not provided.
            ValidationError: If extra unexpected arguments are passed.

        Example::

            orders.by_status.query(status="shipped").where(gte="2024-01-01")
        """
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
    """Descriptor that exposes the :class:`~dkmio.fields.SK` instance
    at both class and instance level.

    This allows ``MyTable.sk.begins_with("prefix")`` to work for
    building sort key conditions.
    """

    def __init__(self, sk: SK) -> None:
        self.sk = sk

    def __get__(self, obj: Any, objtype: type | None = None) -> SK:
        """Return the SK instance regardless of class vs instance access."""
        return self.sk


class IndexDescriptor:
    """Descriptor that returns the raw :class:`~dkmio.fields.Index` at
    class level and an :class:`IndexAccessor` at instance level.

    At class level (``MyTable.by_status``), returns the ``Index``
    descriptor for introspection. At instance level
    (``my_table.by_status``), returns an ``IndexAccessor`` with
    ``.query()`` support.
    """

    def __init__(self, index: Index) -> None:
        self.index = index

    def __set_name__(self, owner: type, name: str) -> None:
        """Record the attribute name on the Index for debugging."""
        self.index._field_name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> Index | IndexAccessor:
        """Return Index (class access) or IndexAccessor (instance access)."""
        if obj is None:
            return self.index
        return IndexAccessor(self.index, obj)


class TableMeta(type):
    """Metaclass that collects PK, SK, Index, and TTL descriptors from the class body.

    When a :class:`Table` subclass is defined, ``TableMeta`` scans its
    namespace for :class:`~dkmio.fields.PK`, :class:`~dkmio.fields.SK`,
    :class:`~dkmio.fields.Index`, and :class:`~dkmio.fields.TTL` instances,
    stores them as ``_pk``, ``_sk``, ``_indexes``, and ``_ttl`` class
    attributes, and installs Python descriptors for runtime access.

    For :class:`~dkmio.fields.LSI` instances, the partition key is
    automatically set to match the table's PK.
    """

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

    def __init__(self, resource=None) -> None:
        """Initialize a Table instance.

        Args:
            resource: Optional boto3 DynamoDB resource. If provided,
                a :class:`~dkmio.client.DynamoDB` instance is created
                automatically, enabling standalone usage without
                ``db.Table`` inheritance.

        Raises:
            ValidationError: If ``__table_name__`` is not defined.

        Example::

            # Via db.Table (recommended)
            class Orders(db.Table):
                __table_name__ = "orders"
                pk = PK("user_id")

            # Via direct resource binding
            orders = Orders(resource=boto3.resource("dynamodb"))
        """
        if not self.__table_name__:
            raise ValidationError(
                f"{self.__class__.__name__} must define __table_name__"
            )
        if resource is not None:
            from .client import DynamoDB

            self._db = DynamoDB(resource=resource)

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
        """Return the :class:`~dkmio.fields.TTL` descriptor, or ``None`` if not defined."""
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
        """Get a single item by its full primary key.

        Args:
            **kwargs: Key attributes (PK, and SK if the table has one).
                Special keyword arguments:
                - ``select`` (list[str]): Attribute names to project.
                    Only these attributes are returned.
                - ``consistent`` (bool): If ``True``, use a strongly
                    consistent read. Defaults to ``False``.

        Returns:
            A normalized Python dict of the item, or ``None`` if no
            item exists with the given key.

        Raises:
            MissingKeyError: If the full primary key is not provided.
            ValidationError: If unexpected non-key arguments are passed.
            TableNotFoundError: If the table does not exist.

        Example::

            item = orders.get(user_id="u1", order_id="o1")
            item = orders.get(user_id="u1", order_id="o1", select=["total"])
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

        from .operations import _run

        def _call() -> Any:
            try:
                return self._dynamo_table.get_item(**params)
            except ClientError as e:
                raise map_boto3_error(e) from e

        response = _run(self._db, _call)
        raw: dict[str, Any] | None = response.get("Item")
        return normalize_item(raw) if raw is not None else None

    def query(self, **kwargs: Any):
        """Start a Query operation on the table's primary key.

        Args:
            **kwargs: Must include the partition key attribute as a
                keyword argument.

        Returns:
            A :class:`~dkmio.query.QueryBuilder` for chaining
            ``.where()``, ``.filter()``, ``.select()``, etc.

        Raises:
            MissingKeyError: If the partition key is not provided.
            ValidationError: If extra unexpected arguments are passed.

        Example::

            results = orders.query(user_id="u1").where(begins_with="ord_").execute()
        """
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
        """Start a Scan operation that reads every item in the table.

        Returns:
            A :class:`~dkmio.query.QueryBuilder` configured for scanning.
            Chain ``.filter()``, ``.select()``, ``.limit()``, etc.

        Example::

            all_items = orders.scan().fetch_all()
            filtered = orders.scan().filter(status__eq="shipped").execute()
        """
        from .query import QueryBuilder

        return QueryBuilder(table=self, pk_name=None, pk_value=None, is_scan=True)

    def put(self, **kwargs: Any) -> dict[str, Any] | None:
        """Put (create or replace) an item in the table.

        Args:
            **kwargs: Item attributes. Must include the full primary key.
                Special keyword arguments:
                - ``condition`` (dict): AND-joined condition expression
                    for conditional writes.
                - ``condition_or`` (list[dict]): OR-joined condition groups.
                - ``return_values`` (str): DynamoDB ``ReturnValues`` option
                    (e.g. ``"ALL_OLD"``).

        Returns:
            The previous item if ``return_values`` was specified and the
            item existed, otherwise ``None``.

        Raises:
            ConditionError: If the condition expression evaluates to false.
            TableNotFoundError: If the table does not exist.

        Example::

            orders.put(user_id="u1", order_id="o1", total=42.0)
            # Conditional put (only if item doesn't exist)
            orders.put(user_id="u1", order_id="o1", total=42.0,
                       condition={"user_id__not_exists": True})
        """
        from .operations import execute_put

        return execute_put(self, kwargs)

    def update(self, **kwargs: Any) -> dict[str, Any] | None:
        """Update an existing item's attributes.

        Args:
            **kwargs: Must include the full primary key, plus at least one
                update operation:
                - ``set`` (dict): Attributes to set.
                - ``remove`` (list[str]): Attributes to remove.
                - ``append`` (dict): Values to append to list attributes.
                - ``add`` (dict): Values to add to number/set attributes.
                - ``delete`` (dict): Values to delete from set attributes.
                - ``condition`` (dict): AND-joined condition expression.
                - ``condition_or`` (list[dict]): OR-joined condition groups.
                - ``return_values`` (str): DynamoDB ``ReturnValues`` option.

        Returns:
            The item attributes if ``return_values`` was specified,
            otherwise ``None``.

        Raises:
            MissingKeyError: If the full primary key is not provided.
            ValidationError: If no update operation is specified.

        Example::

            orders.update(user_id="u1", order_id="o1",
                          set={"status": "shipped"})
        """
        from .operations import execute_update

        return execute_update(self, kwargs)

    def delete(self, **kwargs: Any) -> dict[str, Any] | None:
        """Delete an item from the table by its full primary key.

        Args:
            **kwargs: Must include the full primary key. Optional:
                - ``condition`` (dict): AND-joined condition expression.
                - ``condition_or`` (list[dict]): OR-joined condition groups.
                - ``return_values`` (str): E.g. ``"ALL_OLD"`` to return
                    the deleted item.

        Returns:
            The deleted item if ``return_values`` was specified,
            otherwise ``None``.

        Raises:
            MissingKeyError: If the full primary key is not provided.
            ConditionError: If the condition expression evaluates to false.

        Example::

            orders.delete(user_id="u1", order_id="o1")
        """
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
        """Return a :class:`~dkmio.operations.BatchWriter` context manager.

        Use the returned context manager to queue multiple put/delete
        operations that are flushed in efficient batches of 25.

        Returns:
            A :class:`~dkmio.operations.BatchWriter` instance.

        Example::

            with orders.batch_write() as batch:
                for item in items:
                    batch.put(**item)
        """
        from .operations import BatchWriter

        return BatchWriter(self)
