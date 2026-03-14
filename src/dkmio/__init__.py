"""dkmio — Efficient OKM (Object-Key Mapper) for AWS DynamoDB.

A lightweight, Pythonic library that maps DynamoDB tables to Python classes
with a fluent API for queries, scans, batch operations, and transactions.

Quick start::

    from dkmio import DynamoDB, Table, PK, SK

    db = DynamoDB(region_name="us-east-1")

    class Orders(db.Table):
        __table_name__ = "orders"
        pk = PK("user_id")
        sk = SK("order_id")

    orders = Orders()
    orders.put(user_id="u1", order_id="o1", total=42.0)
    item = orders.get(user_id="u1", order_id="o1")

Exports:
    DynamoDB: Central connection manager.
    Table: Base class for table definitions.
    PK, SK: Partition and sort key descriptors.
    Index, LSI: Global/Local Secondary Index descriptors.
    TTL: Time-To-Live descriptor.
    QueryResult: Paginated query/scan result wrapper.
    transaction: Factory for read/write transactions.
    TableProtocol: Structural typing protocol for table instances.
    DkmioError and subclasses: Exception hierarchy.
"""

__version__ = "0.8.0"

from ._types import TableProtocol
from .circuit_breaker import CircuitBreakerConfig
from .client import DynamoDB
from .exceptions import (
    CircuitOpenError,
    CollectionSizeError,
    ConditionError,
    DkmioError,
    InvalidProjectionError,
    MissingKeyError,
    TableNotFoundError,
    ThrottlingError,
    TransactionError,
    ValidationError,
)
from .fields import LSI, PK, SK, TTL, Index
from .pagination import QueryResult
from .table import Table
from .transactions import transaction

__all__ = [
    "__version__",
    "DynamoDB",
    "CircuitBreakerConfig",
    "Table",
    "PK",
    "SK",
    "Index",
    "LSI",
    "TTL",
    "QueryResult",
    "transaction",
    "DkmioError",
    "MissingKeyError",
    "InvalidProjectionError",
    "ConditionError",
    "TableNotFoundError",
    "ValidationError",
    "ThrottlingError",
    "CollectionSizeError",
    "TransactionError",
    "CircuitOpenError",
    "TableProtocol",
]
