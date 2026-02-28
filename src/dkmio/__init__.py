"""dkmio — Efficient OKM for AWS DynamoDB."""

__version__ = "0.6.0"

from ._types import TableProtocol
from .client import DynamoDB
from .exceptions import (
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
    "TableProtocol",
]
