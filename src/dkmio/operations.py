"""Write operations for DynamoDB: put, update, delete, and batch write.

This module contains the low-level execution functions for DynamoDB write
operations. Each function accepts a :class:`~dkmio._types.TableProtocol`
instance and a kwargs dict, builds the appropriate DynamoDB API parameters,
and handles error mapping to dkmio exceptions.

These functions are called internally by :class:`~dkmio.table.Table` methods.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ._types import TableProtocol
from .conditions import parse_conditions
from .exceptions import (
    CollectionSizeError,
    ConditionError,
    DkmioError,
    TableNotFoundError,
    ThrottlingError,
    ValidationError,
)
from .expressions import ExpressionBuilder
from .serialize import normalize_item

logger = logging.getLogger("dkmio")


def _get_logger(db: Any) -> logging.Logger:
    """Return the logger configured on *db*, falling back to the dkmio module logger."""
    if db is not None:
        db_logger = getattr(db, "_logger", None)
        if isinstance(db_logger, logging.Logger):
            return db_logger
    return logger


def _run(db: Any, fn: Any) -> Any:
    """Execute *fn()* through the circuit breaker attached to *db*.

    *fn* must be a zero-argument callable that raises :class:`~dkmio.exceptions.DkmioError`
    on failure (i.e. it should handle its own ``ClientError`` → ``DkmioError`` mapping).
    If *db* has no circuit breaker configured, *fn* is called directly.
    """
    cb = db._circuit_breaker if db is not None else None
    if cb is None:
        return fn()
    return cb.execute(fn)


def map_boto3_error(e: Any) -> DkmioError:
    """Map a boto3 ``ClientError`` to a dkmio exception.

    Inspects ``e.response["Error"]["Code"]`` and returns the
    appropriate :class:`~dkmio.exceptions.DkmioError` subclass:

    - ``ConditionalCheckFailedException`` -> :class:`ConditionError`
    - ``ResourceNotFoundException`` -> :class:`TableNotFoundError`
    - ``ProvisionedThroughputExceededException`` / ``ThrottlingException``
      -> :class:`ThrottlingError`
    - ``ItemCollectionSizeLimitExceededException`` -> :class:`CollectionSizeError`
    - ``ValidationException`` -> :class:`ValidationError`
    - Any other code -> :class:`DkmioError`

    Args:
        e: A ``botocore.exceptions.ClientError`` instance.

    Returns:
        The corresponding dkmio exception (not raised, just returned).
    """
    error_code = e.response["Error"]["Code"]
    message = e.response["Error"].get("Message", str(e))

    if error_code == "ConditionalCheckFailedException":
        return ConditionError(message)
    elif error_code == "ResourceNotFoundException":
        return TableNotFoundError(message)
    elif error_code in (
        "ProvisionedThroughputExceededException",
        "ThrottlingException",
    ):
        return ThrottlingError(message)
    elif error_code == "ItemCollectionSizeLimitExceededException":
        return CollectionSizeError(message)
    elif error_code == "ValidationException":
        return ValidationError(message)
    else:
        return DkmioError(f"{error_code}: {message}")


def execute_put(table: TableProtocol, kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Execute a DynamoDB ``PutItem`` operation.

    Supports conditional writes via ``condition`` / ``condition_or``
    kwargs, and returning old values via ``return_values``.

    Args:
        table: The table instance to write to.
        kwargs: Item attributes plus optional special keys:
            - ``condition`` (dict): AND-joined condition expression.
            - ``condition_or`` (list[dict]): OR-joined condition groups.
            - ``return_values`` (str): DynamoDB ``ReturnValues`` option
              (e.g. ``"ALL_OLD"``).

    Returns:
        The previous item attributes if ``return_values`` was specified
        and the item existed, otherwise ``None``.

    Raises:
        ConditionError: If the condition expression evaluates to false.
        TableNotFoundError: If the table does not exist.
    """
    from botocore.exceptions import ClientError

    # Extract condition, condition_or, return_values
    condition = kwargs.pop("condition", None)
    condition_or = kwargs.pop("condition_or", None)
    return_values = kwargs.pop("return_values", None)

    params: dict[str, Any] = {"Item": kwargs}

    if return_values:
        params["ReturnValues"] = return_values

    if condition or condition_or:
        builder = ExpressionBuilder()
        cond_expr = parse_conditions(builder, condition, condition_or)
        if cond_expr:
            params["ConditionExpression"] = cond_expr
            names = builder.get_names()
            if names:
                params["ExpressionAttributeNames"] = names
            values = builder.get_values()
            if values:
                params["ExpressionAttributeValues"] = values

    _get_logger(table._db).debug("put_item on %s", table.__table_name__)

    def _call() -> Any:
        try:
            return table._dynamo_table.put_item(**params)
        except ClientError as e:
            raise map_boto3_error(e) from e

    response = _run(table._db, _call)

    if return_values:
        raw = response.get("Attributes")
        return normalize_item(raw) if raw is not None else None
    return None


def execute_update(table: TableProtocol, kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Execute a DynamoDB ``UpdateItem`` operation.

    Builds an ``UpdateExpression`` from the provided update operations
    (``set``, ``remove``, ``append``, ``add``, ``delete``) and applies
    it to the item identified by the key attributes in *kwargs*.

    Args:
        table: The table instance to update on.
        kwargs: Must include the full primary key, plus at least one of:
            - ``set`` (dict): Attributes to set (``SET`` action).
            - ``remove`` (list[str]): Attributes to remove (``REMOVE``).
            - ``append`` (dict): Values to append to lists (``SET list_append``).
            - ``add`` (dict): Values to add to numbers/sets (``ADD``).
            - ``delete`` (dict): Values to delete from sets (``DELETE``).
            - ``condition`` (dict): AND-joined condition expression.
            - ``condition_or`` (list[dict]): OR-joined condition groups.
            - ``return_values`` (str): DynamoDB ``ReturnValues`` option.

    Returns:
        The item attributes if ``return_values`` was specified, otherwise ``None``.

    Raises:
        MissingKeyError: If the full primary key is not provided.
        ValidationError: If no update operation is specified or unexpected args are present.
        ConditionError: If the condition expression evaluates to false.
    """
    from botocore.exceptions import ClientError

    # Extract update operations
    set_ = kwargs.pop("set", None)
    remove = kwargs.pop("remove", None)
    append = kwargs.pop("append", None)
    add = kwargs.pop("add", None)
    delete = kwargs.pop("delete", None)
    condition = kwargs.pop("condition", None)
    condition_or = kwargs.pop("condition_or", None)
    return_values = kwargs.pop("return_values", None)

    # Remaining kwargs should be the key
    keys, extra = table._extract_keys(kwargs)
    table._validate_full_key(keys, "update")

    if extra:
        raise ValidationError(
            f"update() received unexpected arguments: {', '.join(extra.keys())}. "
            f"Use set={{}} to modify attributes."
        )

    if not any([set_, remove, append, add, delete]):
        raise ValidationError(
            "update() requires at least one operation: set, remove, append, add, or delete"
        )

    builder = ExpressionBuilder()
    update_expr = builder.build_update(
        set_=set_, remove=remove, append=append, add=add, delete=delete
    )

    params: dict[str, Any] = {"Key": keys}

    if update_expr:
        params["UpdateExpression"] = update_expr

    if return_values:
        params["ReturnValues"] = return_values

    # Condition expression
    if condition or condition_or:
        cond_expr = parse_conditions(builder, condition, condition_or)
        if cond_expr:
            params["ConditionExpression"] = cond_expr

    names = builder.get_names()
    if names:
        params["ExpressionAttributeNames"] = names
    values = builder.get_values()
    if values:
        params["ExpressionAttributeValues"] = values

    _get_logger(table._db).debug("update_item on %s", table.__table_name__)

    def _call() -> Any:
        try:
            return table._dynamo_table.update_item(**params)
        except ClientError as e:
            raise map_boto3_error(e) from e

    response = _run(table._db, _call)

    if return_values:
        raw = response.get("Attributes")
        return normalize_item(raw) if raw is not None else None
    return None


def execute_delete(table: TableProtocol, kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Execute a DynamoDB ``DeleteItem`` operation.

    Args:
        table: The table instance to delete from.
        kwargs: Must include the full primary key, plus optional:
            - ``condition`` (dict): AND-joined condition expression.
            - ``condition_or`` (list[dict]): OR-joined condition groups.
            - ``return_values`` (str): DynamoDB ``ReturnValues`` option
              (e.g. ``"ALL_OLD"`` to get the deleted item).

    Returns:
        The deleted item's attributes if ``return_values`` was specified,
        otherwise ``None``.

    Raises:
        MissingKeyError: If the full primary key is not provided.
        ValidationError: If unexpected arguments are present.
        ConditionError: If the condition expression evaluates to false.
    """
    from botocore.exceptions import ClientError

    condition = kwargs.pop("condition", None)
    condition_or = kwargs.pop("condition_or", None)
    return_values = kwargs.pop("return_values", None)

    keys, extra = table._extract_keys(kwargs)
    table._validate_full_key(keys, "delete")

    if extra:
        raise ValidationError(
            f"delete() received unexpected arguments: {', '.join(extra.keys())}"
        )

    params: dict[str, Any] = {"Key": keys}

    if return_values:
        params["ReturnValues"] = return_values

    if condition or condition_or:
        builder = ExpressionBuilder()
        cond_expr = parse_conditions(builder, condition, condition_or)
        if cond_expr:
            params["ConditionExpression"] = cond_expr
            names = builder.get_names()
            if names:
                params["ExpressionAttributeNames"] = names
            values = builder.get_values()
            if values:
                params["ExpressionAttributeValues"] = values

    _get_logger(table._db).debug("delete_item on %s", table.__table_name__)

    def _call() -> Any:
        try:
            return table._dynamo_table.delete_item(**params)
        except ClientError as e:
            raise map_boto3_error(e) from e

    response = _run(table._db, _call)

    if return_values:
        raw = response.get("Attributes")
        return normalize_item(raw) if raw is not None else None
    return None


def execute_batch_read(
    table: TableProtocol,
    keys: list[dict[str, Any]],
    select: list[str] | None = None,
    consistent: bool = False,
) -> list[dict[str, Any] | None]:
    """Execute a BatchGetItem operation.

    Returns items in the same order as the input keys.
    Items not found are returned as None.
    """
    from botocore.exceptions import ClientError

    if not keys:
        return []

    _get_logger(table._db).debug("batch_get_item on %s (%d keys)", table.__table_name__, len(keys))
    table_name = table.__table_name__
    resource = table._db.resource

    # Build projection params (always include key attributes for matching)
    pk_name = table._pk.attribute_name if table._pk else None
    sk_name = table._sk.attribute_name if table._sk else None
    proj_params: dict[str, Any] = {}
    if select:
        proj_attrs = list(select)
        # Ensure key attributes are included for order-matching
        if pk_name and pk_name not in proj_attrs:
            proj_attrs.append(pk_name)
        if sk_name and sk_name not in proj_attrs:
            proj_attrs.append(sk_name)
        builder = ExpressionBuilder()
        proj = builder.build_projection(proj_attrs)
        proj_params["ProjectionExpression"] = proj
        names = builder.get_names()
        if names:
            proj_params["ExpressionAttributeNames"] = names

    if consistent:
        proj_params["ConsistentRead"] = True

    # Collect all results
    all_results: list[dict[str, Any]] = []

    # Process in chunks of 100
    for i in range(0, len(keys), 100):
        chunk = keys[i : i + 100]
        request_items = {
            table_name: {
                "Keys": chunk,
                **proj_params,
            }
        }

        retries = 0
        max_retries = 5

        while request_items:
            _req = request_items

            def _call(_r=_req) -> Any:
                try:
                    return resource.batch_get_item(RequestItems=_r)
                except ClientError as e:
                    raise map_boto3_error(e) from e

            response = _run(table._db, _call)

            all_results.extend(response.get("Responses", {}).get(table_name, []))

            unprocessed = response.get("UnprocessedKeys", {})
            if not unprocessed:
                break

            request_items = unprocessed
            retries += 1
            if retries >= max_retries:
                raise ThrottlingError(
                    f"batch_read failed after {max_retries} retries "
                    f"with unprocessed keys"
                )
            _get_logger(table._db).warning("batch_read retry %d on %s", retries, table_name)
            time.sleep(2**retries * 0.1)

    # Build lookup index from results to preserve input order
    # DynamoDB doesn't guarantee order, so we match by key
    def _make_key_tuple(item: dict[str, Any]) -> tuple:
        parts = []
        if pk_name:
            parts.append(item.get(pk_name))
        if sk_name:
            parts.append(item.get(sk_name))
        return tuple(parts)

    result_map: dict[tuple, dict[str, Any]] = {}
    for item in all_results:
        result_map[_make_key_tuple(item)] = normalize_item(item)

    # Return in input order
    ordered: list[dict[str, Any] | None] = []
    for key in keys:
        ordered.append(result_map.get(_make_key_tuple(key)))

    return ordered


class BatchWriter:
    """Context manager for batch write operations.

    Queues ``PutItem`` and ``DeleteItem`` requests, then flushes them
    in chunks of 25 (the DynamoDB ``BatchWriteItem`` limit) when the
    context manager exits. Automatically retries unprocessed items with
    exponential backoff (up to 5 retries).

    Example::

        with orders.batch_write() as batch:
            batch.put(user_id="u1", order_id="o1", total=10)
            batch.put(user_id="u1", order_id="o2", total=20)
            batch.delete(user_id="u1", order_id="o3")

    Args:
        table: The table instance this batch writer operates on.
    """

    def __init__(self, table: TableProtocol) -> None:
        self._table = table
        self._operations: list[dict[str, Any]] = []

    def put(self, **kwargs: Any) -> None:
        """Queue a ``PutItem`` request in the batch.

        Args:
            **kwargs: The item attributes to put.
        """
        self._operations.append({"PutRequest": {"Item": kwargs}})

    def delete(self, **kwargs: Any) -> None:
        """Queue a ``DeleteItem`` request in the batch.

        Args:
            **kwargs: The key attributes (PK + SK) of the item to delete.

        Raises:
            MissingKeyError: If the full primary key is not provided.
            ValidationError: If unexpected (non-key) arguments are present.
        """
        keys, extra = self._table._extract_keys(kwargs)
        self._table._validate_full_key(keys, "delete")
        if extra:
            raise ValidationError(
                f"batch delete() received unexpected arguments: {', '.join(extra.keys())}"
            )
        self._operations.append({"DeleteRequest": {"Key": keys}})

    def __enter__(self) -> BatchWriter:
        """Enter the context manager, returning self for chaining."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Flush all queued operations on successful exit.

        If an exception occurred inside the ``with`` block, the batch
        is abandoned (no writes are sent to DynamoDB).

        Raises:
            ThrottlingError: If unprocessed items remain after 5 retries.
        """
        if exc_type is not None:
            return  # Don't execute on exception

        if not self._operations:
            return

        from botocore.exceptions import ClientError

        table_name = self._table.__table_name__
        resource = self._table._db.resource

        _get_logger(self._table._db).debug(
            "batch_write_item on %s (%d ops)", table_name, len(self._operations)
        )
        # Process in chunks of 25
        for i in range(0, len(self._operations), 25):
            chunk = self._operations[i : i + 25]
            request_items = {table_name: chunk}

            retries = 0
            max_retries = 5

            while request_items:
                _req = request_items

                def _call(_r=_req) -> Any:
                    try:
                        return resource.batch_write_item(RequestItems=_r)
                    except ClientError as e:
                        raise map_boto3_error(e) from e

                response = _run(self._table._db, _call)

                unprocessed = response.get("UnprocessedItems", {})
                if not unprocessed:
                    break

                request_items = unprocessed
                retries += 1
                if retries >= max_retries:
                    raise ThrottlingError(
                        f"batch_write failed after {max_retries} retries "
                        f"with {len(unprocessed.get(table_name, []))} unprocessed items"
                    )
                _get_logger(self._table._db).warning(
                    "batch_write retry %d on %s", retries, table_name
                )
                # Exponential backoff
                time.sleep(2**retries * 0.1)
