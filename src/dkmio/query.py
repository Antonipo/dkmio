"""Query and Scan builders — fluent API for DynamoDB reads.

Provides :class:`QueryBuilder`, a chainable builder that constructs
DynamoDB ``Query`` or ``Scan`` parameters and executes them.

Example::

    # Fluent query with sort key condition, filter, and projection
    result = (
        orders.query(user_id="u1")
        .where(begins_with="ord_")
        .filter(status__eq="shipped")
        .select("order_id", "total")
        .limit(10)
        .execute()
    )
"""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from ._types import TableProtocol
from .exceptions import InvalidProjectionError, ValidationError
from .expressions import _SK_OPERATORS, ExpressionBuilder
from .fields import Index, SKCondition
from .operations import map_boto3_error
from .pagination import QueryResult
from .serialize import normalize_items

logger = logging.getLogger("dkmio")


class QueryBuilder:
    """Builder for DynamoDB Query and Scan operations with a fluent API.

    Constructed by :meth:`Table.query`, :meth:`Table.scan`, or
    :meth:`IndexAccessor.query`. Supports chained calls for sort key
    conditions, filters, projections, pagination, and ordering.

    The builder is lazy — no DynamoDB call is made until :meth:`execute`,
    :meth:`fetch_all`, :meth:`count`, or iteration is triggered.

    ``QueryBuilder`` is iterable, indexable, and supports ``len()`` and
    ``bool()``, all of which implicitly call :meth:`execute`.

    Args:
        table: The table instance to query.
        pk_name: Partition key attribute name (``None`` for scans).
        pk_value: Partition key value (``None`` for scans).
        index: Optional :class:`~dkmio.fields.Index` to query against.
        is_scan: If ``True``, perform a Scan instead of a Query.
    """

    def __init__(
        self,
        table: TableProtocol,
        pk_name: str | None,
        pk_value: Any = None,
        index: Index | None = None,
        is_scan: bool = False,
    ) -> None:
        self._table = table
        self._pk_name = pk_name
        self._pk_value = pk_value
        self._index = index
        self._is_scan = is_scan
        self._sk_condition: SKCondition | None = None
        self._filters: dict[str, Any] = {}
        self._projection: list[str] | None = None
        self._limit: int | None = None
        self._start_key: dict[str, Any] | None = None
        self._scan_forward: bool = True
        self._consistent: bool = False
        self._result: QueryResult | None = None

    def where(self, sk_condition: SKCondition | None = None, **kwargs: Any) -> QueryBuilder:
        """Add a sort key condition (KeyConditionExpression).

        Accepts either an SKCondition object or keyword arguments:
            .where(Orders.sk.gte("ord_100"))   # SKCondition (backward compat)
            .where(gte="ord_100")              # kwargs (recommended)
            .where(between=["ord_100", "ord_200"])
            .where(begins_with="ord_")
        """
        if sk_condition is not None and kwargs:
            raise ValidationError("where() accepts either an SKCondition or kwargs, not both")

        if sk_condition is not None:
            self._sk_condition = sk_condition
        elif kwargs:
            if len(kwargs) != 1:
                raise ValidationError("where() accepts exactly one sort key condition")
            op, value = next(iter(kwargs.items()))
            if op not in _SK_OPERATORS:
                raise ValidationError(
                    f"Invalid sort key operator: {op!r}. "
                    f"Valid: {', '.join(sorted(_SK_OPERATORS))}"
                )
            if op == "between":
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    raise ValidationError("between requires a list/tuple of [low, high]")
                self._sk_condition = SKCondition(op, (value[0], value[1]))
            else:
                self._sk_condition = SKCondition(op, (value,))
        return self

    def filter(self, **kwargs: Any) -> QueryBuilder:
        """Add filter conditions (FilterExpression).

        Duplicate keys across chained calls raise ValidationError
        to prevent silent overwrites.
        """
        dupes = set(kwargs) & set(self._filters)
        if dupes:
            raise ValidationError(
                f"Duplicate filter key(s): {', '.join(sorted(dupes))}. "
                f"Each condition key can only be specified once."
            )
        self._filters.update(kwargs)
        return self

    def select(self, *attrs: str) -> QueryBuilder:
        """Specify which attributes to retrieve."""
        self._projection = list(attrs)
        self._validate_projection()
        return self

    def limit(self, n: int) -> QueryBuilder:
        """Limit the number of items returned."""
        self._limit = n
        return self

    def start_from(self, last_key: dict[str, Any]) -> QueryBuilder:
        """Resume from a previous page's last_key."""
        self._start_key = last_key
        return self

    def scan_forward(self, forward: bool = True) -> QueryBuilder:
        """Set scan direction. False = descending order."""
        self._scan_forward = forward
        return self

    def consistent(self) -> QueryBuilder:
        """Use strongly consistent reads."""
        if self._index is not None:
            raise ValidationError(
                "ConsistentRead is not supported on Global Secondary Indexes. "
                f"Index: '{self._index.index_name}'"
            )
        self._consistent = True
        return self

    def explain(self) -> dict[str, Any]:
        """Return the DynamoDB operation parameters without executing.

        Useful for debugging, logging, or understanding what the builder
        will send to DynamoDB.

        Returns:
            A dict with keys like ``operation``, ``table``, ``index``,
            ``key_condition``, ``filter``, ``projection``, etc.
        """
        params = self._build_params()
        result: dict[str, Any] = {
            "operation": "Scan" if self._is_scan else "Query",
            "table": self._table.__table_name__,
        }
        if self._index:
            result["index"] = self._index.index_name
        if "KeyConditionExpression" in params:
            result["key_condition"] = params["KeyConditionExpression"]
        if "FilterExpression" in params:
            result["filter"] = params["FilterExpression"]
        if "ProjectionExpression" in params:
            result["projection"] = params["ProjectionExpression"]
        if "ExpressionAttributeNames" in params:
            result["expression_attribute_names"] = params["ExpressionAttributeNames"]
        if "ExpressionAttributeValues" in params:
            result["expression_attribute_values"] = params["ExpressionAttributeValues"]
        if "ConsistentRead" in params:
            result["consistent_read"] = True
        return result

    def execute(self) -> QueryResult:
        """Execute the query/scan and return a :class:`~dkmio.pagination.QueryResult`.

        Results are cached — calling ``execute()`` multiple times returns
        the same result without extra DynamoDB calls.

        Returns:
            A :class:`~dkmio.pagination.QueryResult` with the items,
            pagination key, and count metadata.
        """
        if self._result is not None:
            return self._result

        params = self._build_params()
        table = self._table._dynamo_table
        op = "scan" if self._is_scan else "query"
        idx = self._index.index_name if self._index else "table"
        logger.debug("%s on %s (%s)", op, self._table.__table_name__, idx)

        try:
            if self._is_scan:
                response = table.scan(**params)
            else:
                response = table.query(**params)
        except ClientError as e:
            raise map_boto3_error(e) from e

        self._result = QueryResult(
            items=normalize_items(response.get("Items", [])),
            last_key=response.get("LastEvaluatedKey"),
            count=response.get("Count", 0),
            scanned_count=response.get("ScannedCount", 0),
        )
        return self._result

    def fetch_all(self, max_items: int | None = None) -> QueryResult:
        """Fetch all pages and return a single QueryResult.

        Args:
            max_items: Maximum total items to return. None = no limit.
        """
        params = self._build_params()
        table = self._table._dynamo_table

        all_items: list[dict[str, Any]] = []
        total_scanned = 0
        last_key = None

        while True:
            try:
                if self._is_scan:
                    response = table.scan(**params)
                else:
                    response = table.query(**params)
            except ClientError as e:
                raise map_boto3_error(e) from e

            items = response.get("Items", [])
            total_scanned += response.get("ScannedCount", len(items))

            if max_items is not None:
                remaining = max_items - len(all_items)
                if len(items) >= remaining:
                    all_items.extend(items[:remaining])
                    last_key = response.get("LastEvaluatedKey")
                    break
            all_items.extend(items)

            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            params["ExclusiveStartKey"] = last_key

        return QueryResult(
            items=normalize_items(all_items),
            last_key=last_key if max_items is not None and len(all_items) >= max_items else None,
            count=len(all_items),
            scanned_count=total_scanned,
        )

    def count(self) -> int:
        """Return the total count of matching items across all pages.

        Executes the query/scan with ``Select=COUNT`` so DynamoDB
        only returns counts, not item data. Iterates through all
        pages automatically.

        Returns:
            The total number of items matching the query conditions.
        """
        # Build params without projection to avoid conflicts with SELECT=COUNT
        saved_projection = self._projection
        self._projection = None
        params = self._build_params()
        self._projection = saved_projection

        params["Select"] = "COUNT"
        # Remove limit — count should iterate all pages
        params.pop("Limit", None)

        table = self._table._dynamo_table
        total = 0

        while True:
            try:
                if self._is_scan:
                    response = table.scan(**params)
                else:
                    response = table.query(**params)
            except ClientError as e:
                raise map_boto3_error(e) from e

            total += response.get("Count", 0)

            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            params["ExclusiveStartKey"] = last_key

        return int(total)

    def _validate_projection(self) -> None:
        """Validate that requested attributes are available in the index projection."""
        if self._index is None or self._projection is None:
            return

        if self._index.projection_type == "ALL":
            return  # All attributes available

        assert self._table._pk is not None, "Table must have a PK defined"
        table_pk = self._table._pk.attribute_name
        table_sk = self._table._sk.attribute_name if self._table._sk else None
        available = self._index.available_attributes(table_pk, table_sk)

        for attr in self._projection:
            # Strip nested paths for validation (address.city -> address)
            root = attr.split(".")[0].split("[")[0]
            if root not in available:
                raise InvalidProjectionError(
                    f"Attribute '{attr}' is not available in index "
                    f"'{self._index.index_name}'. "
                    f"Available attributes: {', '.join(sorted(available))}."
                )

    def _build_params(self) -> dict[str, Any]:
        """Build the full DynamoDB API parameter dict from builder state."""
        builder = ExpressionBuilder()
        params: dict[str, Any] = {}

        if self._index:
            params["IndexName"] = self._index.index_name

        # Key condition (not for scans)
        if not self._is_scan and self._pk_name is not None:
            sk_name = None
            if self._sk_condition is not None:
                if self._index and self._index.sk:
                    sk_name = self._index.sk
                elif self._table._sk:
                    sk_name = self._table._sk.attribute_name

            key_expr = builder.build_key_condition(
                self._pk_name, self._pk_value, self._sk_condition, sk_name
            )
            params["KeyConditionExpression"] = key_expr

        # Filter
        if self._filters:
            filter_expr = builder.build_filter(self._filters)
            if filter_expr:
                params["FilterExpression"] = filter_expr

        # Projection
        if self._projection:
            proj = builder.build_projection(self._projection)
            if proj:
                params["ProjectionExpression"] = proj

        # Names and values
        names = builder.get_names()
        if names:
            params["ExpressionAttributeNames"] = names
        values = builder.get_values()
        if values:
            params["ExpressionAttributeValues"] = values

        # Limit
        if self._limit is not None:
            params["Limit"] = self._limit

        # Pagination
        if self._start_key is not None:
            params["ExclusiveStartKey"] = self._start_key

        # Scan direction
        if not self._is_scan and not self._scan_forward:
            params["ScanIndexForward"] = False

        # Consistent read
        if self._consistent:
            params["ConsistentRead"] = True

        return params

    # Delegate QueryResult properties (uses cached result, no extra calls)
    @property
    def last_key(self) -> dict[str, Any] | None:
        """LastEvaluatedKey for pagination, or None if no more pages."""
        return self.execute().last_key

    @property
    def scanned_count(self) -> int:
        """Number of items scanned (before filter)."""
        return self.execute().scanned_count

    # Make QueryBuilder iterable and indexable (delegates to execute)
    def __iter__(self):
        """Iterate over result items, executing the query if needed."""
        return iter(self.execute())

    def __len__(self) -> int:
        """Return the number of result items, executing the query if needed."""
        return len(self.execute())

    def __getitem__(self, index):
        """Access a result item by index, executing the query if needed."""
        return self.execute()[index]

    def __bool__(self) -> bool:
        """Return ``True`` if the query returned at least one item."""
        return bool(self.execute())
