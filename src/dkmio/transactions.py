"""DynamoDB transaction support — TransactWriteItems / TransactGetItems.

Note: resource.meta.client auto-serializes/deserializes via boto3's
TransformationInjector (registered when creating a DynamoDB resource).
Items use plain Python types (str, int, Decimal, dict, list, set, etc.),
NOT DynamoDB JSON format ({"S": "..."}).
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
    """Context manager for transactional writes.

    Supports put, update, delete, and condition_check operations.
    All operations execute atomically on exit.
    """

    def __init__(self, db: Any = None) -> None:
        self._db = db
        self._items: list[dict[str, Any]] = []

    def put(self, table: TableProtocol, **kwargs: Any) -> None:
        """Add a PutItem to the transaction."""
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
        """Add an UpdateItem to the transaction."""
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
        """Add a DeleteItem to the transaction."""
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
        """Add a ConditionCheck to the transaction (validates without modifying)."""
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
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
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
    """Transaction for consistent reads of multiple items."""

    def __init__(self, db: Any = None) -> None:
        self._db = db
        self._items: list[dict[str, Any]] = []
        self._results: list[dict[str, Any] | None] | None = None

    def get(self, table: TableProtocol, **kwargs: Any) -> None:
        """Add a GetItem to the transaction."""
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
        """Execute the transactional read and return results."""
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
        return iter(self.execute())

    def __len__(self) -> int:
        return len(self.execute())

    def __getitem__(self, index):
        return self.execute()[index]

    def __bool__(self) -> bool:
        return bool(self.execute())

    def __enter__(self) -> ReadTransaction:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None and self._results is None:
            self.execute()


class _TransactionFactory:
    """Module-level transaction factory."""

    def __init__(self) -> None:
        self._db: Any = None

    def _bind(self, db: Any) -> None:
        self._db = db

    def write(self, db: Any = None) -> WriteTransaction:
        """Create a write transaction context manager."""
        return WriteTransaction(db=db or self._db)

    def read(self, db: Any = None) -> ReadTransaction:
        """Create a read transaction context manager."""
        return ReadTransaction(db=db or self._db)


transaction = _TransactionFactory()
