"""Tests for QueryBuilder and GetBuilder with moto."""

import pytest

from dkmio import PK, SK, DynamoDB, Index
from dkmio.exceptions import InvalidProjectionError, TableNotFoundError, ValidationError
from dkmio.pagination import QueryResult


@pytest.fixture
def setup(dynamodb, orders_table):
    """Set up db, Orders class, and populate test data."""
    db = DynamoDB(resource=dynamodb)

    class Orders(db.Table):
        __table_name__ = "orders"
        pk = PK("user_id")
        sk = SK("order_id")
        by_status = Index(
            "gsi-status-date", pk="status", sk="created_at",
            projection=["total", "items_count"],
        )
        by_date = Index("gsi-date", pk="user_id", sk="created_at")

    orders = Orders()

    # Populate data
    orders.put(user_id="usr_1", order_id="ord_1", status="PENDING", total=100, created_at="2025-01-01", items_count=2)
    orders.put(user_id="usr_1", order_id="ord_2", status="SHIPPED", total=200, created_at="2025-01-15", items_count=3)
    orders.put(user_id="usr_1", order_id="ord_3", status="PENDING", total=50, created_at="2025-02-01", items_count=1)
    orders.put(user_id="usr_2", order_id="ord_4", status="PENDING", total=300, created_at="2025-01-10", items_count=5)

    return orders, Orders


class TestGet:
    def test_get_item(self, setup):
        orders, _ = setup
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item is not None
        assert item["user_id"] == "usr_1"
        assert item["total"] == 100

    def test_get_item_not_found(self, setup):
        orders, _ = setup
        item = orders.get(user_id="usr_1", order_id="nonexistent")
        assert item is None

    def test_get_with_projection(self, setup):
        orders, _ = setup
        item = orders.get(user_id="usr_1", order_id="ord_1", select=["total", "status"])
        assert item is not None
        assert "total" in item
        assert "status" in item

    def test_get_with_consistent(self, setup):
        orders, _ = setup
        item = orders.get(user_id="usr_1", order_id="ord_1", consistent=True)
        assert item is not None
        assert item["total"] == 100

    def test_get_combined(self, setup):
        orders, _ = setup
        item = orders.get(
            user_id="usr_1", order_id="ord_1",
            select=["total"], consistent=True,
        )
        assert item is not None
        assert "total" in item


class TestQueryBuilder:
    def test_query_pk(self, setup):
        orders, _ = setup
        result = orders.query(user_id="usr_1").execute()
        assert isinstance(result, QueryResult)
        assert len(result) == 3

    def test_query_with_sk_condition(self, setup):
        orders, Orders = setup
        sk = Orders._sk
        result = orders.query(user_id="usr_1").where(sk.gt("ord_2")).execute()
        assert len(result) == 1
        assert result[0]["order_id"] == "ord_3"

    def test_query_with_filter(self, setup):
        orders, _ = setup
        result = orders.query(user_id="usr_1").filter(status__eq="PENDING").execute()
        assert len(result) == 2

    def test_query_with_limit(self, setup):
        orders, _ = setup
        result = orders.query(user_id="usr_1").limit(2).execute()
        assert len(result) == 2

    def test_query_pagination(self, setup):
        orders, _ = setup
        page1 = orders.query(user_id="usr_1").limit(2).execute()
        assert len(page1) == 2
        assert page1.last_key is not None

        page2 = orders.query(user_id="usr_1").limit(2).start_from(page1.last_key).execute()
        assert len(page2) == 1
        assert page2.last_key is None

    def test_query_with_select(self, setup):
        orders, _ = setup
        result = orders.query(user_id="usr_1").select("total", "status").execute()
        assert len(result) == 3
        for item in result:
            assert "total" in item
            assert "status" in item

    def test_query_explain(self, setup):
        orders, _ = setup
        result = orders.query(user_id="usr_1").filter(total__gt=100).explain()
        assert result["operation"] == "Query"
        assert result["table"] == "orders"
        assert "key_condition" in result
        assert "filter" in result

    def test_query_iterable(self, setup):
        orders, _ = setup
        items = list(orders.query(user_id="usr_1"))
        assert len(items) == 3

    def test_query_getitem(self, setup):
        orders, _ = setup
        builder = orders.query(user_id="usr_1")
        assert builder[0]["user_id"] == "usr_1"

    def test_query_bool(self, setup):
        orders, _ = setup
        assert bool(orders.query(user_id="usr_1"))
        assert not bool(orders.query(user_id="nonexistent"))

    def test_query_between_sk(self, setup):
        orders, Orders = setup
        sk = Orders._sk
        result = orders.query(user_id="usr_1").where(sk.between("ord_1", "ord_2")).execute()
        assert len(result) == 2

    def test_query_begins_with_sk(self, setup):
        orders, Orders = setup
        sk = Orders._sk
        result = orders.query(user_id="usr_1").where(sk.begins_with("ord_")).execute()
        assert len(result) == 3


class TestQueryOnIndex:
    def test_index_query(self, setup):
        orders, _ = setup
        result = orders.by_status.query(status="PENDING").execute()
        assert len(result) == 3

    def test_index_query_with_sk(self, setup):
        orders, Orders = setup
        idx_sk = SK_for_index(Orders)
        result = (
            orders.by_status
            .query(status="PENDING")
            .where(idx_sk.gte("2025-02-01"))
            .execute()
        )
        assert len(result) == 1

    def test_index_select_valid(self, setup):
        orders, _ = setup
        result = (
            orders.by_status
            .query(status="PENDING")
            .select("total", "items_count")
            .execute()
        )
        assert len(result) == 3

    def test_index_select_invalid_raises(self, setup):
        orders, _ = setup
        with pytest.raises(InvalidProjectionError, match="address"):
            orders.by_status.query(status="PENDING").select("total", "address")

    def test_index_all_projection(self, setup):
        orders, _ = setup
        result = (
            orders.by_date
            .query(user_id="usr_1")
            .select("total", "status", "items_count")
            .execute()
        )
        assert len(result) == 3


class TestScan:
    def test_scan_all(self, setup):
        orders, _ = setup
        result = orders.scan().execute()
        assert len(result) == 4

    def test_scan_with_filter(self, setup):
        orders, _ = setup
        result = orders.scan().filter(status__eq="PENDING").execute()
        assert len(result) == 3

    def test_scan_with_limit(self, setup):
        orders, _ = setup
        result = orders.scan().limit(2).execute()
        assert len(result) == 2

    def test_scan_explain(self, setup):
        orders, _ = setup
        result = orders.scan().explain()
        assert result["operation"] == "Scan"


class TestFetchAll:
    def test_fetch_all_basic(self, setup):
        orders, _ = setup
        result = orders.query(user_id="usr_1").fetch_all()
        assert len(result) == 3
        assert result.last_key is None

    def test_fetch_all_with_filter(self, setup):
        orders, _ = setup
        result = orders.query(user_id="usr_1").filter(status__eq="PENDING").fetch_all()
        assert len(result) == 2

    def test_fetch_all_with_max_items(self, setup):
        orders, _ = setup
        result = orders.query(user_id="usr_1").fetch_all(max_items=2)
        assert len(result) == 2

    def test_fetch_all_scan(self, setup):
        orders, _ = setup
        result = orders.scan().fetch_all()
        assert len(result) == 4

    def test_fetch_all_max_items_zero(self, setup):
        """Max_items=0 should return empty items but preserve last_key."""
        orders, _ = setup
        result = orders.query(user_id="usr_1").fetch_all(max_items=0)
        assert len(result) == 0
        assert result.items == []


class TestCount:
    def test_query_count(self, setup):
        orders, _ = setup
        count = orders.query(user_id="usr_1").count()
        assert count == 3

    def test_scan_count(self, setup):
        orders, _ = setup
        count = orders.scan().count()
        assert count == 4

    def test_query_count_with_filter(self, setup):
        orders, _ = setup
        count = orders.query(user_id="usr_1").filter(status__eq="PENDING").count()
        assert count == 2

    def test_query_count_empty(self, setup):
        orders, _ = setup
        count = orders.query(user_id="nonexistent").count()
        assert count == 0


class TestConsistentRead:
    def test_query_consistent_explain(self, setup):
        orders, _ = setup
        result = orders.query(user_id="usr_1").consistent().explain()
        assert result["consistent_read"] is True

    def test_query_consistent_execute(self, setup):
        orders, _ = setup
        result = orders.query(user_id="usr_1").consistent().execute()
        assert len(result) == 3

    def test_scan_consistent(self, setup):
        orders, _ = setup
        result = orders.scan().consistent().execute()
        assert len(result) == 4

    def test_query_consistent_chained(self, setup):
        orders, _ = setup
        result = (
            orders.query(user_id="usr_1")
            .consistent()
            .filter(status__eq="PENDING")
            .limit(10)
            .execute()
        )
        assert len(result) == 2


def SK_for_index(OrdersClass):
    """Helper to get a usable SK for the by_status index."""
    from dkmio.fields import SK
    return SK("created_at")


class TestQueryErrorHandling:
    def test_query_nonexistent_table_raises(self, dynamodb):
        db = DynamoDB(resource=dynamodb)

        class Nonexistent(db.Table):
            __table_name__ = "nonexistent_table"
            pk = PK("id")

        inst = Nonexistent()
        with pytest.raises(TableNotFoundError):
            inst.query(id="123").execute()

    def test_scan_nonexistent_table_raises(self, dynamodb):
        db = DynamoDB(resource=dynamodb)

        class Nonexistent(db.Table):
            __table_name__ = "nonexistent_table"
            pk = PK("id")

        inst = Nonexistent()
        with pytest.raises(TableNotFoundError):
            inst.scan().execute()

    def test_fetch_all_nonexistent_table_raises(self, dynamodb):
        db = DynamoDB(resource=dynamodb)

        class Nonexistent(db.Table):
            __table_name__ = "nonexistent_table"
            pk = PK("id")

        inst = Nonexistent()
        with pytest.raises(TableNotFoundError):
            inst.query(id="123").fetch_all()

    def test_count_nonexistent_table_raises(self, dynamodb):
        db = DynamoDB(resource=dynamodb)

        class Nonexistent(db.Table):
            __table_name__ = "nonexistent_table"
            pk = PK("id")

        inst = Nonexistent()
        with pytest.raises(TableNotFoundError):
            inst.query(id="123").count()


class TestWhereValidation:
    """Where() kwargs should validate SK operators early."""

    def test_where_invalid_operator_raises(self, setup):
        orders, _ = setup
        with pytest.raises(ValidationError, match="Invalid sort key operator"):
            orders.query(user_id="usr_1").where(contains="foo")

    def test_where_not_exists_raises(self, setup):
        orders, _ = setup
        with pytest.raises(ValidationError, match="Invalid sort key operator"):
            orders.query(user_id="usr_1").where(not_exists=True)

    def test_where_valid_operators_pass(self, setup):
        orders, _ = setup
        # These should not raise
        orders.query(user_id="usr_1").where(eq="ord_1")
        orders.query(user_id="usr_1").where(gte="ord_1")
        orders.query(user_id="usr_1").where(begins_with="ord_")
        orders.query(user_id="usr_1").where(between=["a", "z"])


class TestConsistentOnIndex:
    """.consistent() on GSI should raise ValidationError."""

    def test_consistent_on_gsi_raises(self, setup):
        orders, Orders = setup
        with pytest.raises(ValidationError, match="not supported on Global Secondary"):
            orders.by_status.query(status="PENDING").consistent()

    def test_consistent_on_table_query_ok(self, setup):
        orders, _ = setup
        # Table query — should work fine
        result = orders.query(user_id="usr_1").consistent().execute()
        assert len(result) == 3

    def test_consistent_on_scan_ok(self, setup):
        orders, _ = setup
        # Scan on base table — should work fine
        result = orders.scan().consistent().execute()
        assert len(result) == 4


class TestFilterDuplicateKeys:
    """Chained .filter() with same key should raise, not overwrite."""

    def test_duplicate_filter_key_raises(self, setup):
        orders, _ = setup
        with pytest.raises(ValidationError, match="Duplicate filter key"):
            (
                orders.query(user_id="usr_1")
                .filter(status__eq="PENDING")
                .filter(status__eq="SHIPPED")
            )

    def test_different_filter_keys_ok(self, setup):
        orders, _ = setup
        # Different keys — should work fine
        result = (
            orders.query(user_id="usr_1")
            .filter(status__eq="PENDING")
            .filter(total__gte=50)
            .execute()
        )
        assert len(result) == 2
