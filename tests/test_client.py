"""Tests for DynamoDB client."""

import boto3
from moto import mock_aws

from dkmio import DynamoDB


class TestDynamoDB:
    @mock_aws
    def test_resource_from_explicit(self):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        db = DynamoDB(resource=resource)
        assert db.resource is resource

    @mock_aws
    def test_resource_from_session(self):
        session = boto3.Session(region_name="us-east-1")
        db = DynamoDB(session=session)
        assert db.resource is not None

    @mock_aws
    def test_resource_auto_created(self, aws_credentials):
        db = DynamoDB(region_name="us-east-1")
        assert db.resource is not None

    @mock_aws
    def test_resource_with_endpoint(self, aws_credentials):
        db = DynamoDB(endpoint_url="http://localhost:8000", region_name="us-east-1")
        assert db.resource is not None

    @mock_aws
    def test_resource_lazy_cached(self, aws_credentials):
        db = DynamoDB(region_name="us-east-1")
        r1 = db.resource
        r2 = db.resource
        assert r1 is r2

    @mock_aws
    def test_table_property(self, aws_credentials):
        db = DynamoDB(region_name="us-east-1")
        TableClass = db.Table
        assert TableClass.__name__ == "Table"

    @mock_aws
    def test_table_property_is_cached(self, aws_credentials):
        db = DynamoDB(region_name="us-east-1")
        assert db.Table is db.Table

    @mock_aws
    def test_isinstance_with_bound_table(self, aws_credentials):
        from dkmio import PK

        db = DynamoDB(region_name="us-east-1")

        class MyTable(db.Table):
            __table_name__ = "test"
            pk = PK("id")

        instance = MyTable()
        assert isinstance(instance, db.Table)

