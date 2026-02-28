"""Write operations: put, update, delete, batch write."""

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

logger = logging.getLogger("dkmio")


def map_boto3_error(e: Any) -> DkmioError:
    """Map a boto3 ClientError to an OKM exception."""
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
    """Execute a PutItem operation."""
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

    logger.debug("put_item on %s", table.__table_name__)
    try:
        response = table._dynamo_table.put_item(**params)
    except ClientError as e:
        raise map_boto3_error(e) from e

    if return_values:
        result: dict[str, Any] | None = response.get("Attributes")
        return result
    return None


def execute_update(table: TableProtocol, kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Execute an UpdateItem operation."""
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

    logger.debug("update_item on %s", table.__table_name__)
    try:
        response = table._dynamo_table.update_item(**params)
    except ClientError as e:
        raise map_boto3_error(e) from e

    if return_values:
        result: dict[str, Any] | None = response.get("Attributes")
        return result
    return None


def execute_delete(table: TableProtocol, kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Execute a DeleteItem operation."""
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

    logger.debug("delete_item on %s", table.__table_name__)
    try:
        response = table._dynamo_table.delete_item(**params)
    except ClientError as e:
        raise map_boto3_error(e) from e

    if return_values:
        result: dict[str, Any] | None = response.get("Attributes")
        return result
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

    logger.debug("batch_get_item on %s (%d keys)", table.__table_name__, len(keys))
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
            try:
                response = resource.batch_get_item(RequestItems=request_items)
            except ClientError as e:
                raise map_boto3_error(e) from e

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
            logger.warning("batch_read retry %d on %s", retries, table_name)
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
        result_map[_make_key_tuple(item)] = item

    # Return in input order
    ordered: list[dict[str, Any] | None] = []
    for key in keys:
        ordered.append(result_map.get(_make_key_tuple(key)))

    return ordered


class BatchWriter:
    """Context manager for batch write operations (up to 25 per batch).

    Automatically handles unprocessed items with retries.
    """

    def __init__(self, table: TableProtocol) -> None:
        self._table = table
        self._operations: list[dict[str, Any]] = []

    def put(self, **kwargs: Any) -> None:
        """Queue a PutItem in the batch."""
        self._operations.append({"PutRequest": {"Item": kwargs}})

    def delete(self, **kwargs: Any) -> None:
        """Queue a DeleteItem in the batch."""
        keys, extra = self._table._extract_keys(kwargs)
        self._table._validate_full_key(keys, "delete")
        if extra:
            raise ValidationError(
                f"batch delete() received unexpected arguments: {', '.join(extra.keys())}"
            )
        self._operations.append({"DeleteRequest": {"Key": keys}})

    def __enter__(self) -> BatchWriter:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            return  # Don't execute on exception

        if not self._operations:
            return

        from botocore.exceptions import ClientError

        table_name = self._table.__table_name__
        resource = self._table._db.resource

        logger.debug("batch_write_item on %s (%d ops)", table_name, len(self._operations))
        # Process in chunks of 25
        for i in range(0, len(self._operations), 25):
            chunk = self._operations[i : i + 25]
            request_items = {table_name: chunk}

            retries = 0
            max_retries = 5

            while request_items:
                try:
                    response = resource.batch_write_item(RequestItems=request_items)
                except ClientError as e:
                    raise map_boto3_error(e) from e

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
                logger.warning("batch_write retry %d on %s", retries, table_name)
                # Exponential backoff
                time.sleep(2**retries * 0.1)
