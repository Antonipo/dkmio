"""Tests for Fase 8 features: SKDescriptor, IN, NOT, LSI, where() kwargs."""

import boto3
import pytest
from moto import mock_aws

from dkmio import DynamoDB, PK, SK, Index, LSI, TTL
from dkmio.exceptions import ValidationError
from dkmio.expressions import ExpressionBuilder, _parse_filter_key
from dkmio.fields import LSI as FieldLSI


class TestSKDescriptor:
    """Test that SK is accessible as a public class attribute."""

    def test_sk_accessible_on_class(self):
        db = DynamoDB(region_name="us-east-1")

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        # SK should be accessible at class level
        assert isinstance(Orders.sk, SK)
        assert Orders.sk.attribute_name == "order_id"

    def test_sk_accessible_on_instance(self):
        db = DynamoDB(region_name="us-east-1")

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()
        assert isinstance(orders.sk, SK)
        assert orders.sk.attribute_name == "order_id"

    def test_sk_conditions_via_class(self):
        db = DynamoDB(region_name="us-east-1")

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        cond = Orders.sk.gte("ord_100")
        assert cond.operator == "gte"
        assert cond.values == ("ord_100",)

    def test_private_sk_still_works(self):
        db = DynamoDB(region_name="us-east-1")

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        # _sk should still be available for backward compat
        assert Orders._sk is not None
        assert Orders._sk.attribute_name == "order_id"

    @mock_aws
    def test_sk_in_query_where(self):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        db = DynamoDB(resource=resource)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

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

        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_100", total=100)
        orders.put(user_id="usr_1", order_id="ord_200", total=200)
        orders.put(user_id="usr_1", order_id="ord_300", total=300)

        # Use Orders.sk (public) instead of Orders._sk
        results = list(orders.query(user_id="usr_1").where(Orders.sk.gte("ord_200")))
        assert len(results) == 2


class TestINOperator:
    def test_parse_filter_key_in(self):
        attr, op = _parse_filter_key("status__in")
        assert attr == "status"
        assert op == "in"

    def test_build_filter_in(self):
        builder = ExpressionBuilder()
        result = builder.build_filter({"status__in": ["PENDING", "DRAFT", "NEW"]})
        assert result is not None
        assert "IN" in result
        assert ":v0" in result
        assert ":v1" in result
        assert ":v2" in result
        values = builder.get_values()
        assert values[":v0"] == "PENDING"
        assert values[":v1"] == "DRAFT"
        assert values[":v2"] == "NEW"

    def test_build_condition_in(self):
        builder = ExpressionBuilder()
        result = builder.build_condition({"status__in": ["A", "B"]})
        assert result is not None
        assert "IN" in result

    @mock_aws
    def test_filter_in_integration(self):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        db = DynamoDB(resource=resource)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

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

        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_1", status="PENDING")
        orders.put(user_id="usr_1", order_id="ord_2", status="SHIPPED")
        orders.put(user_id="usr_1", order_id="ord_3", status="DRAFT")

        results = list(
            orders.query(user_id="usr_1").filter(status__in=["PENDING", "DRAFT"])
        )
        assert len(results) == 2
        statuses = {r["status"] for r in results}
        assert statuses == {"PENDING", "DRAFT"}

    @mock_aws
    def test_condition_in_integration(self):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        db = DynamoDB(resource=resource)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

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

        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_1", status="PENDING")

        # Should succeed — status is in list
        orders.update(
            user_id="usr_1", order_id="ord_1",
            set={"status": "CANCELLED"},
            condition={"status__in": ["PENDING", "DRAFT"]},
        )

        result = orders.get(user_id="usr_1", order_id="ord_1")
        assert result["status"] == "CANCELLED"


class TestNOTOperators:
    def test_parse_not_contains(self):
        attr, op = _parse_filter_key("tags__not_contains")
        assert attr == "tags"
        assert op == "not_contains"

    def test_parse_not_begins_with(self):
        attr, op = _parse_filter_key("name__not_begins_with")
        assert attr == "name"
        assert op == "not_begins_with"

    def test_build_filter_not_contains(self):
        builder = ExpressionBuilder()
        result = builder.build_filter({"tags__not_contains": "urgent"})
        assert result is not None
        assert "NOT contains" in result

    def test_build_filter_not_begins_with(self):
        builder = ExpressionBuilder()
        result = builder.build_filter({"name__not_begins_with": "test_"})
        assert result is not None
        assert "NOT begins_with" in result

    @mock_aws
    def test_not_contains_integration(self):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        db = DynamoDB(resource=resource)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

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

        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_1", name="hello world")
        orders.put(user_id="usr_1", order_id="ord_2", name="goodbye world")
        orders.put(user_id="usr_1", order_id="ord_3", name="hello there")

        results = list(
            orders.query(user_id="usr_1").filter(name__not_contains="hello")
        )
        assert len(results) == 1
        assert results[0]["name"] == "goodbye world"


class TestLSI:
    def test_lsi_creation(self):
        lsi = FieldLSI("lsi-amount", sk="total")
        assert lsi.index_name == "lsi-amount"
        assert lsi.sk == "total"
        assert lsi.pk == ""  # Before TableMeta sets it

    def test_lsi_repr(self):
        lsi = FieldLSI("lsi-amount", sk="total")
        assert "LSI" in repr(lsi)
        assert "lsi-amount" in repr(lsi)

    def test_lsi_with_projection(self):
        lsi = FieldLSI("lsi-amount", sk="total", projection=["status"])
        assert lsi.projection == ["status"]
        assert lsi.projection_type == "INCLUDE"

    def test_lsi_pk_set_by_metaclass(self):
        db = DynamoDB(region_name="us-east-1")

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")
            by_amount = LSI("lsi-amount", sk="total")

        # TableMeta should have set the pk automatically
        assert Orders.by_amount.pk == "user_id"

    def test_lsi_is_subclass_of_index(self):
        lsi = FieldLSI("lsi-amount", sk="total")
        assert isinstance(lsi, Index)

    @mock_aws
    def test_lsi_query_integration(self):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        db = DynamoDB(resource=resource)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")
            by_amount = LSI("lsi-amount", sk="total")

        resource.create_table(
            TableName="orders",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "order_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "order_id", "AttributeType": "S"},
                {"AttributeName": "total", "AttributeType": "N"},
            ],
            LocalSecondaryIndexes=[
                {
                    "IndexName": "lsi-amount",
                    "KeySchema": [
                        {"AttributeName": "user_id", "KeyType": "HASH"},
                        {"AttributeName": "total", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_1", total=100)
        orders.put(user_id="usr_1", order_id="ord_2", total=500)
        orders.put(user_id="usr_1", order_id="ord_3", total=200)

        # Query LSI — pk is user_id (auto-set from table)
        results = list(orders.by_amount.query(user_id="usr_1"))
        assert len(results) == 3

        # Query LSI with sort key condition using .where(kwargs)
        results = list(
            orders.by_amount.query(user_id="usr_1").where(gte=200)
        )
        assert len(results) == 2
        totals = {r["total"] for r in results}
        assert totals == {200, 500}


class TestLSIExport:
    def test_lsi_exported_from_dkmio(self):
        from dkmio import LSI
        assert LSI is FieldLSI


class TestWhereKwargs:
    """Test .where() with keyword arguments instead of SKCondition objects."""

    def _make_orders(self):
        """Helper: create DynamoDB resource, table, and Orders class."""
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        db = DynamoDB(resource=resource)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")
            by_status = Index(
                "gsi-status-date", pk="status", sk="created_at", projection="ALL"
            )

        resource.create_table(
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
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        return Orders

    @mock_aws
    def test_where_eq(self):
        Orders = self._make_orders()
        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_100", total=100)
        orders.put(user_id="usr_1", order_id="ord_200", total=200)

        results = list(orders.query(user_id="usr_1").where(eq="ord_100"))
        assert len(results) == 1
        assert results[0]["order_id"] == "ord_100"

    @mock_aws
    def test_where_gte(self):
        Orders = self._make_orders()
        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_100", total=100)
        orders.put(user_id="usr_1", order_id="ord_200", total=200)
        orders.put(user_id="usr_1", order_id="ord_300", total=300)

        results = list(orders.query(user_id="usr_1").where(gte="ord_200"))
        assert len(results) == 2

    @mock_aws
    def test_where_lt(self):
        Orders = self._make_orders()
        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_100", total=100)
        orders.put(user_id="usr_1", order_id="ord_200", total=200)
        orders.put(user_id="usr_1", order_id="ord_300", total=300)

        results = list(orders.query(user_id="usr_1").where(lt="ord_200"))
        assert len(results) == 1
        assert results[0]["order_id"] == "ord_100"

    @mock_aws
    def test_where_between(self):
        Orders = self._make_orders()
        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_100", total=100)
        orders.put(user_id="usr_1", order_id="ord_200", total=200)
        orders.put(user_id="usr_1", order_id="ord_300", total=300)

        results = list(
            orders.query(user_id="usr_1").where(between=["ord_100", "ord_200"])
        )
        assert len(results) == 2

    @mock_aws
    def test_where_begins_with(self):
        Orders = self._make_orders()
        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_100", total=100)
        orders.put(user_id="usr_1", order_id="ord_200", total=200)
        orders.put(user_id="usr_1", order_id="abc_999", total=999)

        results = list(orders.query(user_id="usr_1").where(begins_with="ord_"))
        assert len(results) == 2

    @mock_aws
    def test_where_kwargs_on_index(self):
        """The key benefit: .where() on an index without needing SK object."""
        Orders = self._make_orders()
        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_1", status="PENDING", created_at="2025-01-15")
        orders.put(user_id="usr_1", order_id="ord_2", status="PENDING", created_at="2025-06-01")
        orders.put(user_id="usr_1", order_id="ord_3", status="PENDING", created_at="2025-12-01")

        # .where() on index — uses index SK (created_at) automatically
        results = list(
            orders.by_status.query(status="PENDING").where(gte="2025-06-01")
        )
        assert len(results) == 2

    @mock_aws
    def test_where_kwargs_between_on_index(self):
        Orders = self._make_orders()
        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_1", status="PENDING", created_at="2025-01-15")
        orders.put(user_id="usr_1", order_id="ord_2", status="PENDING", created_at="2025-06-01")
        orders.put(user_id="usr_1", order_id="ord_3", status="PENDING", created_at="2025-12-01")

        results = list(
            orders.by_status.query(status="PENDING")
            .where(between=["2025-01-01", "2025-07-01"])
        )
        assert len(results) == 2

    @mock_aws
    def test_where_backward_compat_sk_condition(self):
        """SKCondition objects still work."""
        Orders = self._make_orders()
        orders = Orders()
        orders.put(user_id="usr_1", order_id="ord_100", total=100)
        orders.put(user_id="usr_1", order_id="ord_200", total=200)

        # Old style still works
        results = list(orders.query(user_id="usr_1").where(Orders.sk.gte("ord_200")))
        assert len(results) == 1

    def test_where_error_both_args(self):
        db = DynamoDB(region_name="us-east-1")

        class T(db.Table):
            __table_name__ = "t"
            pk = PK("pk")
            sk = SK("sk")

        t = T()
        with pytest.raises(ValidationError, match="not both"):
            t.query(pk="x").where(T.sk.eq("y"), eq="y")

    def test_where_error_multiple_kwargs(self):
        db = DynamoDB(region_name="us-east-1")

        class T(db.Table):
            __table_name__ = "t"
            pk = PK("pk")
            sk = SK("sk")

        t = T()
        with pytest.raises(ValidationError, match="exactly one"):
            t.query(pk="x").where(gte="a", lte="z")

    def test_where_error_between_bad_value(self):
        db = DynamoDB(region_name="us-east-1")

        class T(db.Table):
            __table_name__ = "t"
            pk = PK("pk")
            sk = SK("sk")

        t = T()
        with pytest.raises(ValidationError, match="between"):
            t.query(pk="x").where(between="not_a_list")
