"""Exception hierarchy for dkmio.

All exceptions inherit from :class:`DkmioError`, making it easy to
catch any library-specific error with a single ``except DkmioError``.

The hierarchy maps common DynamoDB ``ClientError`` codes to
semantically meaningful Python exceptions::

    DkmioError
    ├── MissingKeyError
    ├── InvalidProjectionError
    ├── ConditionError
    ├── TableNotFoundError
    ├── ValidationError
    ├── ThrottlingError
    ├── CollectionSizeError
    └── TransactionError
"""


class DkmioError(Exception):
    """Base exception for all dkmio errors.

    Catch this to handle any error raised by the library regardless
    of the specific subclass.
    """


class MissingKeyError(DkmioError):
    """Raised when a required key attribute (PK or SK) is missing.

    Typically occurs when calling ``get()``, ``update()``, or ``delete()``
    without providing the full primary key, or when calling ``query()``
    without the partition key.
    """


class InvalidProjectionError(DkmioError):
    """Raised when requesting attributes not available in an index projection.

    Occurs when using ``.select()`` on a query against a
    ``KEYS_ONLY`` or ``INCLUDE`` index and requesting attributes
    that are not part of the projection.
    """


class ConditionError(DkmioError):
    """Raised when a DynamoDB conditional write fails.

    Maps from ``ConditionalCheckFailedException``. This happens when a
    ``condition=`` expression evaluates to false during a put, update,
    or delete operation.
    """


class TableNotFoundError(DkmioError):
    """Raised when the DynamoDB table does not exist.

    Maps from ``ResourceNotFoundException``.
    """


class ValidationError(DkmioError):
    """Raised for invalid parameters, malformed keys, or unsupported operations.

    This covers both client-side validation (e.g. unknown operators,
    unexpected arguments) and DynamoDB ``ValidationException`` responses.
    """


class ThrottlingError(DkmioError):
    """Raised when DynamoDB throughput capacity is exceeded.

    Maps from ``ProvisionedThroughputExceededException`` and
    ``ThrottlingException``. Also raised when batch operations exhaust
    their retry budget for unprocessed items.
    """


class CollectionSizeError(DkmioError):
    """Raised when a partition's item collection exceeds the 10 GB limit.

    Maps from ``ItemCollectionSizeLimitExceededException``.
    Only applies to tables with Local Secondary Indexes.
    """


class TransactionError(DkmioError):
    """Raised when a DynamoDB transaction fails.

    Maps from ``TransactionCanceledException``. Common causes include
    conflicting operations on the same item, failed condition checks
    within the transaction, or exceeding the 100-item transaction limit.
    """
