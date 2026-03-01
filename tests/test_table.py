"""Tests for Table metaclass and base class."""

import pytest
from moto import mock_aws

from dkmio import PK, SK, TTL, DynamoDB, Index
from dkmio.exceptions import MissingKeyError, TableNotFoundError, ValidationError
from dkmio.table import Table


class TestTableMeta:
    def test_collects_pk(self):
        class T(Table):
            __table_name__ = "t"
            pk = PK("id")

        assert T._pk is not None
        assert T._pk.attribute_name == "id"

    def test_collects_sk(self):
        class T(Table):
            __table_name__ = "t"
            pk = PK("id")
            sk = SK("sort")

        assert T._sk is not None
        assert T._sk.attribute_name == "sort"

    def test_collects_index(self):
        class T(Table):
            __table_name__ = "t"
            pk = PK("id")
            by_status = Index("gsi-status", pk="status")

        assert "by_status" in T._indexes
        assert T._indexes["by_status"].index_name == "gsi-status"

    def test_collects_ttl(self):
        class T(Table):
            __table_name__ = "t"
            pk = PK("id")
            ttl = TTL("expires_at")

        assert T._ttl is not None
        assert T._ttl.attribute_name == "expires_at"

    def test_no_table_name_raises(self):
        class T(Table):
            __table_name__ = ""
            pk = PK("id")

        with pytest.raises(ValidationError, match="must define __table_name__"):
            T()


class TestTableOperations:
    @mock_aws
    def test_get_missing_sk_raises(self, dynamodb, orders_table):
        db = DynamoDB(resource=dynamodb)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()
        with pytest.raises(MissingKeyError, match="order_id"):
            orders.get(user_id="usr_123")

    @mock_aws
    def test_get_no_sk_table(self, dynamodb, users_table):
        db = DynamoDB(resource=dynamodb)

        class Users(db.Table):
            __table_name__ = "users"
            pk = PK("user_id")

        users = Users()
        result = users.get(user_id="nonexistent")
        assert result is None

    @mock_aws
    def test_get_extra_args_raises(self, dynamodb, orders_table):
        db = DynamoDB(resource=dynamodb)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()
        with pytest.raises(ValidationError, match="Unexpected"):
            orders.get(user_id="usr_123", order_id="ord_1", extra="bad")

    @mock_aws
    def test_query_missing_pk_raises(self, dynamodb, orders_table):
        db = DynamoDB(resource=dynamodb)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()
        with pytest.raises(MissingKeyError):
            orders.query(status="PENDING")

    @mock_aws
    def test_index_accessor(self, dynamodb, orders_table):
        db = DynamoDB(resource=dynamodb)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")
            by_status = Index(
                "gsi-status-date", pk="status", sk="created_at",
                projection=["total", "items_count"],
            )

        orders = Orders()
        # Accessing index on instance returns IndexAccessor
        accessor = orders.by_status
        from dkmio.table import IndexAccessor
        assert isinstance(accessor, IndexAccessor)

    @mock_aws
    def test_delete_missing_sk_raises(self, dynamodb, orders_table):
        db = DynamoDB(resource=dynamodb)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()
        with pytest.raises(MissingKeyError, match="order_id"):
            orders.delete(user_id="usr_123")

    @mock_aws
    def test_update_missing_sk_raises(self, dynamodb, orders_table):
        db = DynamoDB(resource=dynamodb)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()
        with pytest.raises(MissingKeyError, match="order_id"):
            orders.update(user_id="usr_123", set={"status": "X"})


class TestMetaclassInheritance:
    def test_child_inherits_pk_sk_indexes(self):
        class Base(Table):
            __table_name__ = "base"
            pk = PK("id")
            sk = SK("sort")
            by_status = Index("gsi-status", pk="status")

        class Child(Base):
            __table_name__ = "child"

        assert Child._pk is not None
        assert Child._pk.attribute_name == "id"
        assert Child._sk is not None
        assert Child._sk.attribute_name == "sort"
        assert "by_status" in Child._indexes
        assert Child._indexes["by_status"].index_name == "gsi-status"

    def test_child_can_override_fields(self):
        class Base(Table):
            __table_name__ = "base"
            pk = PK("id")

        class Child(Base):
            __table_name__ = "child"
            pk = PK("child_id")
            sk = SK("child_sort")

        assert Child._pk.attribute_name == "child_id"
        assert Child._sk is not None
        assert Child._sk.attribute_name == "child_sort"

    def test_child_inherits_ttl(self):
        class Base(Table):
            __table_name__ = "base"
            pk = PK("id")
            ttl_field = TTL("expires_at")

        class Child(Base):
            __table_name__ = "child"

        assert Child._ttl is not None
        assert Child._ttl.attribute_name == "expires_at"


class TestIndexAccessorErrors:
    @mock_aws
    def test_index_query_missing_pk_raises(self, dynamodb, orders_table):
        db = DynamoDB(resource=dynamodb)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")
            by_status = Index(
                "gsi-status-date", pk="status", sk="created_at",
                projection=["total", "items_count"],
            )

        orders = Orders()
        with pytest.raises(MissingKeyError, match="status"):
            orders.by_status.query(wrong_key="value")

    @mock_aws
    def test_index_query_extra_kwargs_raises(self, dynamodb, orders_table):
        db = DynamoDB(resource=dynamodb)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")
            by_status = Index(
                "gsi-status-date", pk="status", sk="created_at",
                projection=["total", "items_count"],
            )

        orders = Orders()
        with pytest.raises(ValidationError, match="Unexpected arguments"):
            orders.by_status.query(status="NEW", extra="bad")


class TestTableDirectResource:
    @mock_aws
    def test_table_with_resource_direct(self, dynamodb, users_table):
        class Users(Table):
            __table_name__ = "users"
            pk = PK("user_id")

        users = Users(resource=dynamodb)
        users.put(user_id="usr_1", name="Alice")
        result = users.get(user_id="usr_1")
        assert result is not None
        assert result["name"] == "Alice"

    @mock_aws
    def test_table_resource_overrides_class_db(self, dynamodb, users_table):
        db = DynamoDB(resource=dynamodb)

        class Users(db.Table):
            __table_name__ = "users"
            pk = PK("user_id")

        # Instance resource should override class-level _db
        users = Users(resource=dynamodb)
        users.put(user_id="usr_1", name="Bob")
        result = users.get(user_id="usr_1")
        assert result is not None
        assert result["name"] == "Bob"


class TestGetErrorHandling:
    def test_get_nonexistent_table_raises_table_not_found(self, dynamodb):
        db = DynamoDB(resource=dynamodb)

        class Nonexistent(db.Table):
            __table_name__ = "nonexistent_table"
            pk = PK("id")

        inst = Nonexistent()
        with pytest.raises(TableNotFoundError):
            inst.get(id="123")
