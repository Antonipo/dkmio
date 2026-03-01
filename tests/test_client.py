"""Tests for DynamoDB client."""

import boto3
import pytest
from moto import mock_aws

from dkmio import DynamoDB, PK, SK, transaction


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
        db = DynamoDB(region_name="us-east-1")

        class MyTable(db.Table):
            __table_name__ = "test"
            pk = PK("id")

        instance = MyTable()
        assert isinstance(instance, db.Table)

    @mock_aws
    def test_set_default_binds_transaction(self, aws_credentials):
        """set_default enables transaction.write()/read() without db=."""
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="orders",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "order_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "order_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        db = DynamoDB(resource=resource)
        db.set_default()

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()

        # transaction.write() without db= should work after set_default()
        with transaction.write() as tx:
            tx.put(orders, user_id="usr_1", order_id="ord_1", status="NEW")

        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item is not None
        assert item["status"] == "NEW"

        # transaction.read() without db= should also work
        with transaction.read() as tx:
            tx.get(orders, user_id="usr_1", order_id="ord_1")

        assert len(tx) == 1

        # Clean up: unbind to not affect other tests
        transaction._bind(None)
