"""Tests for DynamoDB transactions."""

from decimal import Decimal

import pytest

from dkmio import PK, SK, DynamoDB, transaction
from dkmio.exceptions import TransactionError, ValidationError


@pytest.fixture
def setup(dynamodb, orders_table, users_table):
    db = DynamoDB(resource=dynamodb)

    class Orders(db.Table):
        __table_name__ = "orders"
        pk = PK("user_id")
        sk = SK("order_id")

    class Users(db.Table):
        __table_name__ = "users"
        pk = PK("user_id")

    orders = Orders()
    users = Users()
    return db, orders, users


class TestWriteTransaction:
    def test_transact_put(self, setup):
        db, orders, users = setup
        with transaction.write(db=db) as tx:
            tx.put(orders, user_id="usr_1", order_id="ord_1", total=100, status="NEW")
            tx.put(users, user_id="usr_1", name="Alice", status="ACTIVE")

        order = orders.get(user_id="usr_1", order_id="ord_1")
        assert order["total"] == 100
        user = users.get(user_id="usr_1")
        assert user["name"] == "Alice"

    def test_transact_update(self, setup):
        db, orders, users = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW", total=100)

        with transaction.write(db=db) as tx:
            tx.update(orders, user_id="usr_1", order_id="ord_1", set={"status": "SHIPPED"})

        order = orders.get(user_id="usr_1", order_id="ord_1")
        assert order["status"] == "SHIPPED"

    def test_transact_delete(self, setup):
        db, orders, users = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW")

        with transaction.write(db=db) as tx:
            tx.delete(orders, user_id="usr_1", order_id="ord_1")

        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item is None

    def test_transact_condition_check(self, setup):
        db, orders, users = setup
        users.put(user_id="usr_1", status="ACTIVE")

        with transaction.write(db=db) as tx:
            tx.put(orders, user_id="usr_1", order_id="ord_1", total=100)
            tx.condition_check(users, user_id="usr_1", condition={"status__eq": "ACTIVE"})

        order = orders.get(user_id="usr_1", order_id="ord_1")
        assert order is not None

    def test_transact_condition_check_fails(self, setup):
        db, orders, users = setup
        users.put(user_id="usr_1", status="INACTIVE")

        with pytest.raises(TransactionError):
            with transaction.write(db=db) as tx:
                tx.put(orders, user_id="usr_1", order_id="ord_1", total=100)
                tx.condition_check(users, user_id="usr_1", condition={"status__eq": "ACTIVE"})

        # Order should NOT have been created
        order = orders.get(user_id="usr_1", order_id="ord_1")
        assert order is None

    def test_transact_empty(self, setup):
        db, orders, users = setup
        with transaction.write(db=db) as _tx:
            pass  # No operations


class TestReadTransaction:
    def test_transact_read(self, setup):
        db, orders, users = setup
        orders.put(user_id="usr_1", order_id="ord_1", total=100)
        users.put(user_id="usr_1", name="Alice")

        with transaction.read(db=db) as tx:
            tx.get(orders, user_id="usr_1", order_id="ord_1")
            tx.get(users, user_id="usr_1")

        results = tx.execute()
        assert len(results) == 2
        assert results[0]["total"] == 100
        assert results[1]["name"] == "Alice"

    def test_transact_read_missing_item(self, setup):
        db, orders, users = setup
        users.put(user_id="usr_1", name="Alice")

        with transaction.read(db=db) as tx:
            tx.get(orders, user_id="usr_1", order_id="nonexistent")
            tx.get(users, user_id="usr_1")

        results = tx.execute()
        assert len(results) == 2
        assert results[0] is None
        assert results[1]["name"] == "Alice"

    def test_transact_read_empty(self, setup):
        db, orders, users = setup
        with transaction.read(db=db) as tx:
            pass
        results = tx.execute()
        assert results == []


class TestConditionalTransactions:
    def test_put_with_condition(self, setup):
        db, orders, users = setup
        # Put initial item
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW")

        # Put with condition should succeed
        with transaction.write(db=db) as tx:
            tx.put(
                orders,
                user_id="usr_1", order_id="ord_2", total=200, status="NEW",
                condition={"user_id__not_exists": True},
            )

        item = orders.get(user_id="usr_1", order_id="ord_2")
        assert item is not None
        assert item["total"] == 200

    def test_update_with_condition(self, setup):
        db, orders, users = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="PENDING")

        with transaction.write(db=db) as tx:
            tx.update(
                orders,
                user_id="usr_1", order_id="ord_1",
                set={"status": "SHIPPED"},
                condition={"status__eq": "PENDING"},
            )

        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item["status"] == "SHIPPED"

    def test_delete_with_condition(self, setup):
        db, orders, users = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="CANCELLED")

        with transaction.write(db=db) as tx:
            tx.delete(
                orders,
                user_id="usr_1", order_id="ord_1",
                condition={"status__eq": "CANCELLED"},
            )

        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item is None

    def test_condition_check_end_to_end(self, setup):
        db, orders, users = setup
        users.put(user_id="usr_1", status="ACTIVE")

        with transaction.write(db=db) as tx:
            tx.put(orders, user_id="usr_1", order_id="ord_1", total=100)
            tx.condition_check(users, user_id="usr_1", condition={"status__eq": "ACTIVE"})

        order = orders.get(user_id="usr_1", order_id="ord_1")
        assert order is not None

    def test_condition_check_without_condition_raises(self, setup):
        db, orders, users = setup
        with pytest.raises(ValidationError, match="requires a 'condition'"):
            with transaction.write(db=db) as tx:
                tx.condition_check(users, user_id="usr_1")

    def test_update_with_condition_or(self, setup):
        db, orders, users = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="DRAFT")

        with transaction.write(db=db) as tx:
            tx.update(
                orders,
                user_id="usr_1", order_id="ord_1",
                set={"status": "CANCELLED"},
                condition_or=[
                    {"status__eq": "PENDING"},
                    {"status__eq": "DRAFT"},
                ],
            )

        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item["status"] == "CANCELLED"

    def test_delete_with_condition_or(self, setup):
        db, orders, users = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="EXPIRED")

        with transaction.write(db=db) as tx:
            tx.delete(
                orders,
                user_id="usr_1", order_id="ord_1",
                condition_or=[
                    {"status__eq": "CANCELLED"},
                    {"status__eq": "EXPIRED"},
                ],
            )

        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item is None


class TestTransactionSerialization:
    """Validate serialization of complex types in transactions."""

    def test_write_transaction_complex_types(self, setup):
        db, orders, users = setup
        with transaction.write(db=db) as tx:
            tx.put(
                orders,
                user_id="usr_1",
                order_id="ord_1",
                total=Decimal("199.99"),
                tags={"urgent", "priority"},
                address={"city": "Lima", "zip": "15001"},
                items=[{"product": "keyboard", "qty": 2}],
            )

        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item is not None
        assert item["total"] == Decimal("199.99")
        assert "urgent" in item["tags"]
        assert item["address"]["city"] == "Lima"
        assert item["items"][0]["product"] == "keyboard"

    def test_read_transaction_complex_types(self, setup):
        db, orders, users = setup
        orders.put(
            user_id="usr_1",
            order_id="ord_1",
            total=Decimal("299.99"),
            tags={"vip"},
            metadata={"source": "web", "version": 2},
        )

        with transaction.read(db=db) as tx:
            tx.get(orders, user_id="usr_1", order_id="ord_1")

        results = tx.execute()
        assert len(results) == 1
        item = results[0]
        assert item["total"] == Decimal("299.99")
        assert "vip" in item["tags"]
        assert item["metadata"]["source"] == "web"

    def test_update_transaction_complex_types(self, setup):
        db, orders, users = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW", total=Decimal("50"))

        with transaction.write(db=db) as tx:
            tx.update(
                orders,
                user_id="usr_1",
                order_id="ord_1",
                set={"total": Decimal("75.50"), "status": "UPDATED"},
            )

        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item["total"] == Decimal("75.50")
        assert item["status"] == "UPDATED"
