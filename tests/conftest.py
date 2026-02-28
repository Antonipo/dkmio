import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws_credentials():
    """Mock AWS credentials for moto."""
    import os
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def dynamodb(aws_credentials):
    """Create a mock DynamoDB resource."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield resource


@pytest.fixture
def orders_table(dynamodb):
    """Create a mock orders table with PK + SK and GSIs."""
    table = dynamodb.create_table(
        TableName="orders",
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"},
            {"AttributeName": "order_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "order_id", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "gsi-status-date",
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {
                    "ProjectionType": "INCLUDE",
                    "NonKeyAttributes": ["total", "items_count"],
                },
            },
            {
                "IndexName": "gsi-date",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.meta.client.get_waiter("table_exists").wait(TableName="orders")
    return table


@pytest.fixture
def users_table(dynamodb):
    """Create a mock users table with PK only (no SK)."""
    table = dynamodb.create_table(
        TableName="users",
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.meta.client.get_waiter("table_exists").wait(TableName="users")
    return table


@pytest.fixture
def sessions_table(dynamodb):
    """Create a mock sessions table with TTL."""
    table = dynamodb.create_table(
        TableName="sessions",
        KeySchema=[
            {"AttributeName": "session_id", "KeyType": "HASH"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "session_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.meta.client.get_waiter("table_exists").wait(TableName="sessions")
    return table
