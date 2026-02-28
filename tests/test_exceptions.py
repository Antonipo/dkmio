"""Tests for the exception hierarchy."""

import pytest

from dkmio.exceptions import (
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


class TestExceptionHierarchy:
    """All exceptions should inherit from DkmioError."""

    @pytest.mark.parametrize(
        "exc_class",
        [
            MissingKeyError,
            InvalidProjectionError,
            ConditionError,
            TableNotFoundError,
            ValidationError,
            ThrottlingError,
            CollectionSizeError,
            TransactionError,
        ],
    )
    def test_inherits_from_base(self, exc_class):
        assert issubclass(exc_class, DkmioError)
        assert issubclass(exc_class, Exception)

    @pytest.mark.parametrize(
        "exc_class",
        [
            MissingKeyError,
            InvalidProjectionError,
            ConditionError,
            TableNotFoundError,
            ValidationError,
            ThrottlingError,
            CollectionSizeError,
            TransactionError,
        ],
    )
    def test_can_be_raised_and_caught(self, exc_class):
        with pytest.raises(DkmioError):
            raise exc_class("test message")

    def test_message_preserved(self):
        err = MissingKeyError("falta order_id")
        assert str(err) == "falta order_id"
