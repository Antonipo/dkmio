"""End-to-end integration tests matching the design doc examples."""

import time

import pytest

from dkmio import PK, SK, TTL, DynamoDB, Index, transaction
from dkmio.exceptions import (
    ConditionError,
    InvalidProjectionError,
    MissingKeyError,
    TransactionError,
)


@pytest.fixture
def db_setup(dynamodb, orders_table, users_table, sessions_table):
    """Full setup with all tables."""
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

    class Users(db.Table):
        __table_name__ = "users"
        pk = PK("user_id")

    class Sessions(db.Table):
        __table_name__ = "sessions"
        pk = PK("session_id")
        ttl = TTL("expires_at")

    orders = Orders()
    users = Users()
    sessions = Sessions()

    return db, orders, users, sessions, Orders


class TestDesignDocExamples:
    """Tests matching the examples from docs/base.md."""

    def test_basic_get(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_456", status="NEW", total=250)
        order = orders.get(user_id="usr_123", order_id="ord_456")
        assert order["total"] == 250

    def test_get_with_projection(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_456", status="NEW", total=250, extra="data")
        order = orders.get(user_id="usr_123", order_id="ord_456", select=["total", "status"])
        assert order["total"] == 250
        assert order["status"] == "NEW"

    def test_get_missing_sk_error(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        with pytest.raises(MissingKeyError, match="order_id"):
            orders.get(user_id="usr_123")

    def test_table_without_sk(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        users.put(user_id="usr_123", name="Alice", email="alice@example.com")
        user = users.get(user_id="usr_123")
        assert user["name"] == "Alice"

    def test_query_basic(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_1", status="NEW", total=100, created_at="2025-01-01")
        orders.put(user_id="usr_123", order_id="ord_2", status="SHIPPED", total=200, created_at="2025-01-15")
        orders.put(user_id="usr_123", order_id="ord_3", status="NEW", total=50, created_at="2025-02-01")

        result = orders.query(user_id="usr_123").execute()
        assert len(result) == 3

    def test_query_with_sk_condition(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_001", total=100, created_at="2025-01-01")
        orders.put(user_id="usr_123", order_id="ord_050", total=200, created_at="2025-01-15")
        orders.put(user_id="usr_123", order_id="ord_100", total=300, created_at="2025-02-01")

        sk = Orders._sk
        result = (
            orders.query(user_id="usr_123")
            .where(sk.between("ord_001", "ord_100"))
            .select("total", "status")
            .execute()
        )
        assert len(result) == 3

    def test_query_on_index(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_1", order_id="ord_1", status="PENDING", total=100, created_at="2025-01-01", items_count=2)
        orders.put(user_id="usr_1", order_id="ord_2", status="PENDING", total=200, created_at="2025-01-15", items_count=3)
        orders.put(user_id="usr_2", order_id="ord_3", status="SHIPPED", total=50, created_at="2025-02-01", items_count=1)

        from dkmio.fields import SK as SKField
        idx_sk = SKField("created_at")
        result = (
            orders.by_status
            .query(status="PENDING")
            .where(idx_sk.gte("2025-01-01"))
            .filter(total__gte=100)
            .select("user_id", "total")
            .limit(20)
            .execute()
        )
        assert len(result) == 2

    def test_index_invalid_projection(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        with pytest.raises(InvalidProjectionError, match="address"):
            orders.by_status.query(status="PENDING").select("user_id", "total", "address")

    def test_pagination(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        for i in range(5):
            orders.put(user_id="usr_123", order_id=f"ord_{i:03d}", total=i * 100)

        page1 = orders.query(user_id="usr_123").limit(3).execute()
        assert len(page1) == 3
        assert page1.last_key is not None

        page2 = orders.query(user_id="usr_123").limit(3).start_from(page1.last_key).execute()
        assert len(page2) == 2
        assert page2.last_key is None

    def test_scan(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_1", order_id="ord_1", status="PENDING", total=100)
        orders.put(user_id="usr_2", order_id="ord_2", status="SHIPPED", total=200)

        result = orders.scan().filter(status__eq="PENDING").execute()
        assert len(result) == 1

    def test_put_basic(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_789", status="NEW", total=250)
        item = orders.get(user_id="usr_123", order_id="ord_789")
        assert item["status"] == "NEW"
        assert item["total"] == 250

    def test_update_set_and_remove(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_789", status="NEW", total=250, temp_notes="temp")
        orders.update(
            user_id="usr_123", order_id="ord_789",
            set={"status": "SHIPPED", "shipped_at": "2025-02-24"},
            remove=["temp_notes"],
        )
        item = orders.get(user_id="usr_123", order_id="ord_789")
        assert item["status"] == "SHIPPED"
        assert "temp_notes" not in item

    def test_update_nested_map(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(
            user_id="usr_123", order_id="ord_456",
            address={"city": "Lima", "zip": "15001"},
        )
        orders.update(
            user_id="usr_123", order_id="ord_456",
            set={"address.city": "Cusco"},
        )
        item = orders.get(user_id="usr_123", order_id="ord_456")
        assert item["address"]["city"] == "Cusco"
        assert item["address"]["zip"] == "15001"

    def test_update_append_list(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(
            user_id="usr_123", order_id="ord_456",
            items=[{"product": "keyboard", "qty": 1}],
        )
        orders.update(
            user_id="usr_123", order_id="ord_456",
            append={"items": {"product": "mouse", "qty": 1}},
        )
        item = orders.get(user_id="usr_123", order_id="ord_456")
        assert len(item["items"]) == 2

    def test_update_add_delete_sets(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_456", tags={"pending"})
        orders.update(
            user_id="usr_123", order_id="ord_456",
            add={"tags": {"urgent", "priority"}},
        )
        item = orders.get(user_id="usr_123", order_id="ord_456")
        assert "urgent" in item["tags"]
        assert "pending" in item["tags"]

        orders.update(
            user_id="usr_123", order_id="ord_456",
            delete={"tags": {"pending"}},
        )
        item = orders.get(user_id="usr_123", order_id="ord_456")
        assert "pending" not in item["tags"]

    def test_delete_basic(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_789", status="NEW")
        orders.delete(user_id="usr_123", order_id="ord_789")
        item = orders.get(user_id="usr_123", order_id="ord_789")
        assert item is None

    def test_delete_missing_sk_error(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        with pytest.raises(MissingKeyError, match="order_id"):
            orders.delete(user_id="usr_123")

    def test_batch_write(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        with orders.batch_write() as batch:
            batch.put(user_id="usr_1", order_id="ord_1", total=100)
            batch.put(user_id="usr_2", order_id="ord_2", total=200)
            batch.delete(user_id="usr_1", order_id="ord_1")

        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item is None
        item = orders.get(user_id="usr_2", order_id="ord_2")
        assert item is not None

    def test_condition_put_not_exists(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_789", status="NEW", total=250)
        with pytest.raises(ConditionError):
            orders.put(
                user_id="usr_123", order_id="ord_789",
                status="NEW", total=250,
                condition={"user_id__not_exists": True},
            )

    def test_condition_update_eq(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_789", status="PENDING")
        orders.update(
            user_id="usr_123", order_id="ord_789",
            set={"status": "SHIPPED"},
            condition={"status__eq": "PENDING"},
        )
        item = orders.get(user_id="usr_123", order_id="ord_789")
        assert item["status"] == "SHIPPED"

    def test_condition_update_fails(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_789", status="SHIPPED")
        with pytest.raises(ConditionError):
            orders.update(
                user_id="usr_123", order_id="ord_789",
                set={"status": "DELIVERED"},
                condition={"status__eq": "PENDING"},
            )

    def test_condition_or(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_789", status="DRAFT")
        orders.update(
            user_id="usr_123", order_id="ord_789",
            set={"status": "CANCELLED"},
            condition_or=[
                {"status__eq": "PENDING"},
                {"status__eq": "DRAFT"},
            ],
        )
        item = orders.get(user_id="usr_123", order_id="ord_789")
        assert item["status"] == "CANCELLED"

    def test_condition_nested(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(
            user_id="usr_123", order_id="ord_789",
            address={"city": "Lima"},
        )
        orders.update(
            user_id="usr_123", order_id="ord_789",
            set={"address.city": "Cusco"},
            condition={"address.city__eq": "Lima"},
        )
        item = orders.get(user_id="usr_123", order_id="ord_789")
        assert item["address"]["city"] == "Cusco"

    def test_condition_delete(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_123", order_id="ord_789", status="CANCELLED")
        orders.delete(
            user_id="usr_123", order_id="ord_789",
            condition={"status__eq": "CANCELLED"},
        )
        item = orders.get(user_id="usr_123", order_id="ord_789")
        assert item is None

    def test_write_transaction(self, db_setup):
        db, orders, users, sessions, Orders = db_setup

        with transaction.write(db=db) as tx:
            tx.put(orders, user_id="usr_1", order_id="ord_1", total=100, status="NEW")
            tx.put(users, user_id="usr_1", name="Alice", status="ACTIVE")

        order = orders.get(user_id="usr_1", order_id="ord_1")
        assert order["total"] == 100
        user = users.get(user_id="usr_1")
        assert user["name"] == "Alice"

    def test_write_transaction_with_condition_check(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        users.put(user_id="usr_1", status="ACTIVE")

        with transaction.write(db=db) as tx:
            tx.put(orders, user_id="usr_1", order_id="ord_1", total=100, status="NEW")
            tx.condition_check(users, user_id="usr_1", condition={"status__eq": "ACTIVE"})

        order = orders.get(user_id="usr_1", order_id="ord_1")
        assert order is not None

    def test_write_transaction_condition_check_fails(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        users.put(user_id="usr_1", status="INACTIVE")

        with pytest.raises(TransactionError):
            with transaction.write(db=db) as tx:
                tx.put(orders, user_id="usr_1", order_id="ord_1", total=100)
                tx.condition_check(users, user_id="usr_1", condition={"status__eq": "ACTIVE"})

    def test_read_transaction(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(user_id="usr_1", order_id="ord_1", total=100)
        users.put(user_id="usr_1", name="Alice")

        with transaction.read(db=db) as tx:
            tx.get(orders, user_id="usr_1", order_id="ord_1")
            tx.get(users, user_id="usr_1")

        results = tx.execute()
        assert results[0]["total"] == 100
        assert results[1]["name"] == "Alice"

    def test_ttl_from_now(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        ttl_val = sessions.ttl.from_now(hours=24)
        sessions.put(session_id="sess_123", user_id="usr_456", expires_at=ttl_val)

        item = sessions.get(session_id="sess_123")
        assert item["expires_at"] == ttl_val
        # Should be roughly 24 hours from now
        expected = int(time.time()) + 86400
        assert abs(item["expires_at"] - expected) < 5

    def test_explain(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        result = (
            orders.by_status
            .query(status="PENDING")
            .filter(total__gte=100)
            .select("user_id", "total")
            .explain()
        )
        assert result["operation"] == "Query"
        assert result["table"] == "orders"
        assert result["index"] == "gsi-status-date"
        assert "key_condition" in result
        assert "filter" in result

    def test_data_types(self, db_setup):
        db, orders, users, sessions, Orders = db_setup
        orders.put(
            user_id="usr_123",
            order_id="ord_456",
            total=250,
            is_active=True,
            tags={"urgent", "priority"},
            items=[{"product": "keyboard", "qty": 1, "price": 50}],
            address={"city": "Lima", "zip": "15001"},
            metadata=None,
        )
        order = orders.get(user_id="usr_123", order_id="ord_456")
        assert order["total"] == 250
        assert order["is_active"] is True
        assert isinstance(order["tags"], list)
        assert "urgent" in order["tags"]
        assert isinstance(order["items"], list)
        assert order["items"][0]["product"] == "keyboard"
        assert order["address"]["city"] == "Lima"
        assert order["metadata"] is None
