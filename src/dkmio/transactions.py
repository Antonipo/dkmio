"""DynamoDB transaction support — ``TransactWriteItems`` / ``TransactGetItems``.

Provides :class:`WriteTransaction` and :class:`ReadTransaction` as context
managers for atomic multi-item operations, plus the module-level
:data:`transaction` factory for convenient access.

Example::

    from dkmio import transaction

    with transaction.write() as tx:
        tx.put(orders, user_id="u1", order_id="o1", total=10)
        tx.update(inventory, product_id="p1", set={"stock": 99})
        tx.condition_check(users, user_id="u1",
                           condition={"status__eq": "active"})

.. note::
    ``resource.meta.client`` auto-serializes/deserializes via boto3's
    ``TransformationInjector``. Items use plain Python types
    (``str``, ``int``, ``Decimal``, ``dict``, ``list``, ``set``),
    **not** DynamoDB JSON format (``{"S": "..."}``).
"""

from __future__ import annotations

import logging
from typing import Any

from ._types import TableProtocol
from .conditions import parse_conditions
from .exceptions import TransactionError, ValidationError
from .expressions import ExpressionBuilder
from .operations import map_boto3_error
from .serialize import normalize_item

logger = logging.getLogger("dkmio")


class WriteTransaction:
    """Context manager for transactional writes (up to 100 operations).

    Supports ``put``, ``update``, ``delete``, and ``condition_check``
    operations. All operations execute atomically when the context
    manager exits successfully. If an exception occurs inside the
    ``with`` block, no writes are sent.

    Args:
        db: A :class:`~dkmio.client.DynamoDB` instance. Usually
            provided automatically via :meth:`transaction.write`.

    Example::

        with transaction.write() as tx:
            tx.put(orders, user_id="u1", order_id="o1", total=10)
            tx.delete(orders, user_id="u1", order_id="o2")
    """

    def __init__(self, db: Any = None) -> None:
        self._db = db
        self._items: list[dict[str, Any]] = []

    def put(self, table: TableProtocol, **kwargs: Any) -> None:
        """Add a ``PutItem`` operation to the transaction.

        Args:
            table: The table instance to put the item into.
            **kwargs: Item attributes. Optionally includes:
                - ``condition`` (dict): AND-joined condition expression.
                - ``condition_or`` (list[dict]): OR-joined condition groups.
        """
        logger.debug("tx add put on %s", table.__table_name__)
        condition = kwargs.pop("condition", None)
        condition_or = kwargs.pop("condition_or", None)

        item: dict[str, Any] = {
            "Put": {
                "TableName": table.__table_name__,
                "Item": kwargs,
            }
        }

        if condition or condition_or:
            builder = ExpressionBuilder()
            cond_expr = parse_conditions(builder, condition, condition_or)
            if cond_expr:
                item["Put"]["ConditionExpression"] = cond_expr
                names = builder.get_names()
                if names:
                    item["Put"]["ExpressionAttributeNames"] = names
                values = builder.get_values()
                if values:
                    item["Put"]["ExpressionAttributeValues"] = values

        self._items.append(item)

    def update(self, table: TableProtocol, **kwargs: Any) -> None:
        """Add an ``UpdateItem`` operation to the transaction.

        Args:
            table: The table instance to update on.
            **kwargs: Must include the full primary key, plus at least one of
                ``set``, ``remove``, ``append``, ``add``, ``delete``.
                Optionally includes ``condition`` and ``condition_or``.

        Raises:
            MissingKeyError: If the full primary key is not provided.
            ValidationError: If unexpected arguments are passed.
        """
        logger.debug("tx add update on %s", table.__table_name__)
        set_ = kwargs.pop("set", None)
        remove = kwargs.pop("remove", None)
        append = kwargs.pop("append", None)
        add = kwargs.pop("add", None)
        delete = kwargs.pop("delete", None)
        condition = kwargs.pop("condition", None)
        condition_or = kwargs.pop("condition_or", None)

        keys, extra = table._extract_keys(kwargs)
        table._validate_full_key(keys, "update")

        if extra:
            raise ValidationError(
                f"transaction update() received unexpected arguments: {', '.join(extra.keys())}"
            )

        builder = ExpressionBuilder()
        update_expr = builder.build_update(
            set_=set_, remove=remove, append=append, add=add, delete=delete
        )

        item: dict[str, Any] = {
            "Update": {
                "TableName": table.__table_name__,
                "Key": keys,
            }
        }

        if update_expr:
            item["Update"]["UpdateExpression"] = update_expr

        if condition or condition_or:
            cond_expr = parse_conditions(builder, condition, condition_or)
            if cond_expr:
                item["Update"]["ConditionExpression"] = cond_expr

        names = builder.get_names()
        if names:
            item["Update"]["ExpressionAttributeNames"] = names
        values = builder.get_values()
        if values:
            item["Update"]["ExpressionAttributeValues"] = values

        self._items.append(item)

    def delete(self, table: TableProtocol, **kwargs: Any) -> None:
        """Add a ``DeleteItem`` operation to the transaction.

        Args:
            table: The table instance to delete from.
            **kwargs: Must include the full primary key. Optionally
                includes ``condition`` and ``condition_or``.

        Raises:
            MissingKeyError: If the full primary key is not provided.
            ValidationError: If unexpected arguments are passed.
        """
        logger.debug("tx add delete on %s", table.__table_name__)
        condition = kwargs.pop("condition", None)
        condition_or = kwargs.pop("condition_or", None)

        keys, extra = table._extract_keys(kwargs)
        table._validate_full_key(keys, "delete")

        if extra:
            raise ValidationError(
                f"transaction delete() received unexpected arguments: {', '.join(extra.keys())}"
            )

        item: dict[str, Any] = {
            "Delete": {
                "TableName": table.__table_name__,
                "Key": keys,
            }
        }

        if condition or condition_or:
            builder = ExpressionBuilder()
            cond_expr = parse_conditions(builder, condition, condition_or)
            if cond_expr:
                item["Delete"]["ConditionExpression"] = cond_expr
                names = builder.get_names()
                if names:
                    item["Delete"]["ExpressionAttributeNames"] = names
                values = builder.get_values()
                if values:
                    item["Delete"]["ExpressionAttributeValues"] = values

        self._items.append(item)

    def condition_check(self, table: TableProtocol, **kwargs: Any) -> None:
        """Add a ``ConditionCheck`` to the transaction.

        Validates a condition against an item without modifying it.
        If the condition fails, the entire transaction is cancelled.

        Args:
            table: The table instance to check against.
            **kwargs: Must include the full primary key plus at least
                one of ``condition`` (dict) or ``condition_or`` (list[dict]).

        Raises:
            ValidationError: If neither ``condition`` nor ``condition_or``
                is provided, or if unexpected arguments are passed.
            MissingKeyError: If the full primary key is not provided.
        """
        logger.debug("tx add condition_check on %s", table.__table_name__)
        condition = kwargs.pop("condition", None)
        condition_or = kwargs.pop("condition_or", None)
        if not condition and not condition_or:
            raise ValidationError(
                "condition_check() requires a 'condition' or 'condition_or' argument"
            )

        keys, extra = table._extract_keys(kwargs)
        table._validate_full_key(keys, "condition_check")

        if extra:
            raise ValidationError(
                f"condition_check() received unexpected arguments: {', '.join(extra.keys())}"
            )

        builder = ExpressionBuilder()
        cond_expr = parse_conditions(builder, condition, condition_or)

        item: dict[str, Any] = {
            "ConditionCheck": {
                "TableName": table.__table_name__,
                "Key": keys,
            }
        }

        if cond_expr:
            item["ConditionCheck"]["ConditionExpression"] = cond_expr
            names = builder.get_names()
            if names:
                item["ConditionCheck"]["ExpressionAttributeNames"] = names
            values = builder.get_values()
            if values:
                item["ConditionCheck"]["ExpressionAttributeValues"] = values

        self._items.append(item)

    def _get_client(self) -> Any:
        """Get the boto3 DynamoDB client for transactions.

        Uses resource.meta.client which has TransformationInjector hooks
        for automatic serialization/deserialization of Python types.
        """
        return self._db.resource.meta.client

    def __enter__(self) -> WriteTransaction:
        """Enter the transaction context, returning self for chaining."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Execute all queued operations atomically on successful exit.

        Raises:
            TransactionError: If the transaction is cancelled by DynamoDB
                (e.g. condition check failure, conflict).
        """
        if exc_type is not None:
            return

        if not self._items:
            return

        from botocore.exceptions import ClientError

        logger.debug("transact_write_items (%d ops)", len(self._items))
        try:
            self._get_client().transact_write_items(TransactItems=self._items)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "TransactionCanceledException":
                raise TransactionError(
                    f"Transaction cancelled: {e.response['Error'].get('Message', str(e))}"
                ) from e
            raise map_boto3_error(e) from e


class ReadTransaction:
    """Context manager for transactional (consistent) reads of multiple items.

    All items are read atomically, ensuring a consistent snapshot across
    multiple tables and items.

    Can be used as a context manager (auto-executes on exit) or
    by calling :meth:`execute` directly. Results are iterable and indexable.

    Args:
        db: A :class:`~dkmio.client.DynamoDB` instance.

    Example::

        tx = transaction.read()
        tx.get(orders, user_id="u1", order_id="o1")
        tx.get(users, user_id="u1")
        order, user = tx.execute()
    """

    def __init__(self, db: Any = None) -> None:
        self._db = db
        self._items: list[dict[str, Any]] = []
        self._results: list[dict[str, Any] | None] | None = None

    def get(self, table: TableProtocol, **kwargs: Any) -> None:
        """Add a ``GetItem`` operation to the transactional read.

        Args:
            table: The table instance to read from.
            **kwargs: The full primary key of the item to retrieve.

        Raises:
            MissingKeyError: If the full primary key is not provided.
            ValidationError: If unexpected arguments are passed.
        """
        logger.debug("tx add get on %s", table.__table_name__)
        keys, extra = table._extract_keys(kwargs)
        table._validate_full_key(keys, "get")

        if extra:
            raise ValidationError(
                f"transaction get() received unexpected arguments: {', '.join(extra.keys())}"
            )

        self._items.append({
            "Get": {
                "TableName": table.__table_name__,
                "Key": keys,
            }
        })

    def execute(self) -> list[dict[str, Any] | None]:
        """Execute the transactional read and return results.

        Results are cached — calling ``execute()`` multiple times
        returns the same result without extra DynamoDB calls.

        Returns:
            A list of normalized item dicts (or ``None`` for items
            not found), in the same order as the ``get()`` calls.
        """
        if self._results is not None:
            return self._results

        if not self._items:
            self._results = []
            return self._results

        from botocore.exceptions import ClientError

        logger.debug("transact_get_items (%d ops)", len(self._items))
        try:
            response = self._get_client().transact_get_items(TransactItems=self._items)
        except ClientError as e:
            raise map_boto3_error(e) from e

        self._results = []
        for resp in response.get("Responses", []):
            item = resp.get("Item")
            if item:
                self._results.append(normalize_item(item))
            else:
                self._results.append(None)

        return self._results

    def _get_client(self) -> Any:
        """Get the boto3 DynamoDB client for transactions.

        Uses resource.meta.client which has TransformationInjector hooks
        for automatic serialization/deserialization of Python types.
        """
        return self._db.resource.meta.client

    # Make ReadTransaction iterable and indexable (delegates to execute)
    def __iter__(self):
        """Iterate over result items, executing the read if needed."""
        return iter(self.execute())

    def __len__(self) -> int:
        """Return the number of results, executing the read if needed."""
        return len(self.execute())

    def __getitem__(self, index):
        """Access a result by index, executing the read if needed."""
        return self.execute()[index]

    def __bool__(self) -> bool:
        """Return ``True`` if at least one item was retrieved."""
        return bool(self.execute())

    def __enter__(self) -> ReadTransaction:
        """Enter the transaction context, returning self."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Auto-execute the read on successful exit if not yet executed."""
        if exc_type is None and self._results is None:
            self.execute()


class _TransactionFactory:
    """Module-level transaction factory.

    Provides ``transaction.write()`` and ``transaction.read()`` as
    convenient entry points. The default DynamoDB connection is set
    via :meth:`DynamoDB.set_default`.
    """

    def __init__(self) -> None:
        self._db: Any = None

    def _bind(self, db: Any) -> None:
        """Bind a default :class:`~dkmio.client.DynamoDB` instance.

        Called internally by :meth:`DynamoDB.set_default`.
        """
        self._db = db

    def write(self, db: Any = None) -> WriteTransaction:
        """Create a :class:`WriteTransaction` context manager.

        Args:
            db: Optional :class:`~dkmio.client.DynamoDB` instance.
                Falls back to the default set via ``DynamoDB.set_default()``.

        Returns:
            A new :class:`WriteTransaction`.
        """
        return WriteTransaction(db=db or self._db)

    def read(self, db: Any = None) -> ReadTransaction:
        """Create a :class:`ReadTransaction` context manager.

        Args:
            db: Optional :class:`~dkmio.client.DynamoDB` instance.
                Falls back to the default set via ``DynamoDB.set_default()``.

        Returns:
            A new :class:`ReadTransaction`.
        """
        return ReadTransaction(db=db or self._db)


transaction = _TransactionFactory()
"""Module-level transaction factory instance.

Use ``transaction.write()`` and ``transaction.read()`` to create
transactional contexts::

    from dkmio import transaction

    with transaction.write() as tx:
        tx.put(table, key="value", data="hello")
"""
