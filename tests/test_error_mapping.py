"""Tests for map_boto3_error — direct unit tests for each error branch."""

import pytest
from botocore.exceptions import ClientError

from dkmio.exceptions import (
    CollectionSizeError,
    ConditionError,
    DkmioError,
    TableNotFoundError,
    ThrottlingError,
    ValidationError,
)
from dkmio.operations import map_boto3_error


def _make_client_error(code: str, message: str = "test") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "TestOperation",
    )


class TestMapBoto3Error:
    def test_conditional_check_failed(self):
        err = map_boto3_error(_make_client_error("ConditionalCheckFailedException"))
        assert isinstance(err, ConditionError)

    def test_resource_not_found(self):
        err = map_boto3_error(_make_client_error("ResourceNotFoundException"))
        assert isinstance(err, TableNotFoundError)

    def test_provisioned_throughput_exceeded(self):
        err = map_boto3_error(
            _make_client_error("ProvisionedThroughputExceededException")
        )
        assert isinstance(err, ThrottlingError)

    def test_throttling_exception(self):
        err = map_boto3_error(_make_client_error("ThrottlingException"))
        assert isinstance(err, ThrottlingError)

    def test_item_collection_size_limit(self):
        err = map_boto3_error(
            _make_client_error("ItemCollectionSizeLimitExceededException")
        )
        assert isinstance(err, CollectionSizeError)

    def test_validation_exception(self):
        err = map_boto3_error(_make_client_error("ValidationException"))
        assert isinstance(err, ValidationError)

    def test_unknown_error(self):
        err = map_boto3_error(_make_client_error("SomeUnknownError", "oops"))
        assert isinstance(err, DkmioError)
        assert "SomeUnknownError" in str(err)

    def test_message_preserved(self):
        err = map_boto3_error(
            _make_client_error("ConditionalCheckFailedException", "custom msg")
        )
        assert "custom msg" in str(err)

    def test_missing_message_falls_back(self):
        """When Message key is absent, falls back to str(e)."""
        exc = ClientError(
            {"Error": {"Code": "ValidationException"}},
            "TestOperation",
        )
        err = map_boto3_error(exc)
        assert isinstance(err, ValidationError)
