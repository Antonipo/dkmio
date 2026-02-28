"""Exception hierarchy for dkmio."""


class DkmioError(Exception):
    """Base exception for all OKM errors."""


class MissingKeyError(DkmioError):
    """Raised when a required key (PK or SK) is missing."""


class InvalidProjectionError(DkmioError):
    """Raised when requesting attributes not available in an index projection."""


class ConditionError(DkmioError):
    """Raised when a conditional write fails."""


class TableNotFoundError(DkmioError):
    """Raised when the DynamoDB table does not exist."""


class ValidationError(DkmioError):
    """Raised for invalid parameters or malformed keys."""


class ThrottlingError(DkmioError):
    """Raised when DynamoDB throughput capacity is exceeded."""


class CollectionSizeError(DkmioError):
    """Raised when a partition exceeds the 10GB limit."""


class TransactionError(DkmioError):
    """Raised when a transaction fails (conflict, condition, etc.)."""
