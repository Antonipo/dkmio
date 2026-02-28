"""Tests for write operations: put, update, delete, batch write."""

from unittest.mock import MagicMock, patch

import pytest

from dkmio import PK, SK, DynamoDB
from dkmio.exceptions import ConditionError, MissingKeyError, ThrottlingError, ValidationError


@pytest.fixture
def setup(dynamodb, orders_table):
    db = DynamoDB(resource=dynamodb)

    class Orders(db.Table):
        __table_name__ = "orders"
        pk = PK("user_id")
        sk = SK("order_id")

    orders = Orders()
    return orders


class TestPut:
    def test_put_and_get(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW", total=100)
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item["status"] == "NEW"
        assert item["total"] == 100

    def test_put_conditional_not_exists(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW")
        with pytest.raises(ConditionError):
            orders.put(
                user_id="usr_1", order_id="ord_1",
                status="OVERWRITE",
                condition={"user_id__not_exists": True},
            )

    def test_put_conditional_eq(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="DRAFT")
        # Should succeed because status is DRAFT
        orders.put(
            user_id="usr_1", order_id="ord_1",
            status="CONFIRMED",
            condition={"status__eq": "DRAFT"},
        )
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item["status"] == "CONFIRMED"


class TestUpdate:
    def test_update_set(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW", total=100)
        orders.update(
            user_id="usr_1", order_id="ord_1",
            set={"status": "SHIPPED", "shipped_at": "2025-02-24"},
        )
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item["status"] == "SHIPPED"
        assert item["shipped_at"] == "2025-02-24"

    def test_update_remove(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW", temp="data")
        orders.update(user_id="usr_1", order_id="ord_1", remove=["temp"])
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert "temp" not in item

    def test_update_conditional(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="SHIPPED")
        with pytest.raises(ConditionError):
            orders.update(
                user_id="usr_1", order_id="ord_1",
                set={"status": "DELIVERED"},
                condition={"status__eq": "PENDING"},
            )

    def test_update_no_operation_raises(self, setup):
        orders = setup
        with pytest.raises(ValidationError, match="at least one operation"):
            orders.update(user_id="usr_1", order_id="ord_1")

    def test_update_add_set_type(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", tags={"initial"})
        orders.update(
            user_id="usr_1", order_id="ord_1",
            add={"tags": {"urgent", "priority"}},
        )
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert "urgent" in item["tags"]
        assert "initial" in item["tags"]

    def test_update_delete_set_type(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", tags={"a", "b", "c"})
        orders.update(
            user_id="usr_1", order_id="ord_1",
            delete={"tags": {"b"}},
        )
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert "b" not in item["tags"]
        assert "a" in item["tags"]

    def test_update_append_list(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", items=[{"product": "keyboard"}])
        orders.update(
            user_id="usr_1", order_id="ord_1",
            append={"items": {"product": "mouse"}},
        )
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert len(item["items"]) == 2
        assert item["items"][1]["product"] == "mouse"

    def test_update_nested_path(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", address={"city": "Lima", "zip": "15001"})
        orders.update(
            user_id="usr_1", order_id="ord_1",
            set={"address.city": "Cusco"},
        )
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item["address"]["city"] == "Cusco"
        assert item["address"]["zip"] == "15001"

    def test_update_condition_or(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="DRAFT")
        orders.update(
            user_id="usr_1", order_id="ord_1",
            set={"status": "CANCELLED"},
            condition_or=[
                {"status__eq": "PENDING"},
                {"status__eq": "DRAFT"},
            ],
        )
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item["status"] == "CANCELLED"


class TestDelete:
    def test_delete(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW")
        orders.delete(user_id="usr_1", order_id="ord_1")
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item is None

    def test_delete_conditional(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="ACTIVE")
        with pytest.raises(ConditionError):
            orders.delete(
                user_id="usr_1", order_id="ord_1",
                condition={"status__eq": "CANCELLED"},
            )
        # Item should still exist
        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item is not None

    def test_delete_missing_sk_raises(self, setup):
        orders = setup
        with pytest.raises(MissingKeyError, match="order_id"):
            orders.delete(user_id="usr_1")


class TestBatchWrite:
    def test_batch_put_and_delete(self, setup):
        orders = setup
        with orders.batch_write() as batch:
            batch.put(user_id="usr_1", order_id="ord_1", total=100)
            batch.put(user_id="usr_1", order_id="ord_2", total=200)
            batch.put(user_id="usr_1", order_id="ord_3", total=300)

        # Verify items exist
        result = orders.query(user_id="usr_1").execute()
        assert len(result) == 3

        # Delete in batch
        with orders.batch_write() as batch:
            batch.delete(user_id="usr_1", order_id="ord_1")
            batch.delete(user_id="usr_1", order_id="ord_2")

        result = orders.query(user_id="usr_1").execute()
        assert len(result) == 1
        assert result[0]["order_id"] == "ord_3"

    def test_batch_empty(self, setup):
        orders = setup
        with orders.batch_write() as _batch:
            pass  # No operations — should not error

    def test_batch_exception_aborts(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", total=100)
        try:
            with orders.batch_write() as batch:
                batch.put(user_id="usr_1", order_id="ord_2", total=200)
                raise ValueError("abort!")
        except ValueError:
            pass
        # ord_2 should NOT have been written
        item = orders.get(user_id="usr_1", order_id="ord_2")
        assert item is None

    def test_batch_write_complex_types(self, setup):
        """Validate serialization of complex types (Decimal, sets, nested maps)."""
        from decimal import Decimal

        orders = setup
        with orders.batch_write() as batch:
            batch.put(
                user_id="usr_1",
                order_id="ord_1",
                total=Decimal("99.99"),
                tags={"urgent", "priority"},
                address={"city": "Lima", "zip": "15001"},
                items=[{"product": "keyboard", "qty": 2}],
            )

        item = orders.get(user_id="usr_1", order_id="ord_1")
        assert item is not None
        assert item["total"] == Decimal("99.99")
        assert "urgent" in item["tags"]
        assert item["address"]["city"] == "Lima"
        assert item["items"][0]["product"] == "keyboard"


class TestBatchRead:
    def test_batch_read_basic(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", total=100)
        orders.put(user_id="usr_1", order_id="ord_2", total=200)

        items = orders.batch_read([
            {"user_id": "usr_1", "order_id": "ord_1"},
            {"user_id": "usr_1", "order_id": "ord_2"},
        ])
        assert len(items) == 2
        assert items[0]["total"] == 100
        assert items[1]["total"] == 200

    def test_batch_read_with_projection(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", total=100, status="NEW")

        items = orders.batch_read(
            [{"user_id": "usr_1", "order_id": "ord_1"}],
            select=["total", "status"],
        )
        assert len(items) == 1
        assert items[0]["total"] == 100
        assert items[0]["status"] == "NEW"

    def test_batch_read_missing_items(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", total=100)

        items = orders.batch_read([
            {"user_id": "usr_1", "order_id": "ord_1"},
            {"user_id": "usr_1", "order_id": "nonexistent"},
        ])
        assert len(items) == 2
        assert items[0]["total"] == 100
        assert items[1] is None

    def test_batch_read_preserves_order(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", total=100)
        orders.put(user_id="usr_1", order_id="ord_2", total=200)
        orders.put(user_id="usr_1", order_id="ord_3", total=300)

        # Request in reverse order
        items = orders.batch_read([
            {"user_id": "usr_1", "order_id": "ord_3"},
            {"user_id": "usr_1", "order_id": "ord_1"},
            {"user_id": "usr_1", "order_id": "ord_2"},
        ])
        assert items[0]["total"] == 300
        assert items[1]["total"] == 100
        assert items[2]["total"] == 200

    def test_batch_read_empty(self, setup):
        orders = setup
        items = orders.batch_read([])
        assert items == []


class TestReturnValues:
    def test_put_return_values_all_old(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW", total=100)
        old = orders.put(
            user_id="usr_1", order_id="ord_1",
            status="UPDATED", total=200,
            return_values="ALL_OLD",
        )
        assert old is not None
        assert old["status"] == "NEW"
        assert old["total"] == 100

    def test_put_no_return_values_returns_none(self, setup):
        orders = setup
        result = orders.put(user_id="usr_1", order_id="ord_1", status="NEW")
        assert result is None

    def test_update_return_values_all_new(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW", total=100)
        updated = orders.update(
            user_id="usr_1", order_id="ord_1",
            set={"status": "SHIPPED"},
            return_values="ALL_NEW",
        )
        assert updated is not None
        assert updated["status"] == "SHIPPED"
        assert updated["total"] == 100

    def test_delete_return_values_all_old(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW", total=100)
        deleted = orders.delete(
            user_id="usr_1", order_id="ord_1",
            return_values="ALL_OLD",
        )
        assert deleted is not None
        assert deleted["status"] == "NEW"
        assert deleted["total"] == 100


class TestUpdateValidation:
    def test_update_extra_kwargs_raises(self, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", status="NEW")
        with pytest.raises(ValidationError, match="unexpected arguments"):
            orders.update(
                user_id="usr_1", order_id="ord_1",
                status="BAD",
                set={"total": 5},
            )


class TestDeleteValidation:
    def test_delete_extra_kwargs_raises(self, setup):
        orders = setup
        with pytest.raises(ValidationError, match="unexpected arguments"):
            orders.delete(user_id="usr_1", order_id="ord_1", extra="bad")


class TestBatchDeleteValidation:
    def test_batch_delete_extra_kwargs_raises(self, setup):
        orders = setup
        with pytest.raises(ValidationError, match="unexpected arguments"):
            with orders.batch_write() as batch:
                batch.delete(user_id="usr_1", order_id="ord_1", extra="bad")


class TestBatchWriteRetry:
    @patch("time.sleep")
    def test_batch_write_retries_unprocessed(self, mock_sleep, setup):
        orders = setup

        call_count = 0
        resource = orders._db.resource
        original_batch_write = resource.batch_write_item

        def mock_batch_write(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: return unprocessed items
                resp = original_batch_write(**kwargs)
                resp["UnprocessedItems"] = kwargs["RequestItems"]
                return resp
            # Second call: success
            return original_batch_write(**kwargs)

        with patch.object(resource, "batch_write_item", side_effect=mock_batch_write):
            with orders.batch_write() as batch:
                batch.put(user_id="usr_1", order_id="ord_1", total=100)

        assert call_count == 2
        mock_sleep.assert_called_once()

    @patch("time.sleep")
    def test_batch_write_max_retries_raises(self, mock_sleep, setup):
        orders = setup
        resource = orders._db.resource
        original_batch_write = resource.batch_write_item

        def always_unprocessed(**kwargs):
            resp = original_batch_write(**kwargs)
            resp["UnprocessedItems"] = kwargs["RequestItems"]
            return resp

        with patch.object(resource, "batch_write_item", side_effect=always_unprocessed):
            with pytest.raises(ThrottlingError, match="batch_write failed"):
                with orders.batch_write() as batch:
                    batch.put(user_id="usr_1", order_id="ord_1", total=100)


class TestBatchReadRetry:
    @patch("time.sleep")
    def test_batch_read_retries_unprocessed(self, mock_sleep, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", total=100)

        resource = orders._db.resource
        original_batch_get = resource.batch_get_item
        call_count = 0

        def mock_batch_get(**kwargs):
            nonlocal call_count
            call_count += 1
            resp = original_batch_get(**kwargs)
            if call_count == 1:
                resp["UnprocessedKeys"] = kwargs["RequestItems"]
            return resp

        with patch.object(resource, "batch_get_item", side_effect=mock_batch_get):
            items = orders.batch_read([
                {"user_id": "usr_1", "order_id": "ord_1"},
            ])

        assert call_count == 2
        assert items[0] is not None
        mock_sleep.assert_called_once()

    @patch("time.sleep")
    def test_batch_read_max_retries_raises(self, mock_sleep, setup):
        orders = setup
        orders.put(user_id="usr_1", order_id="ord_1", total=100)

        resource = orders._db.resource
        original_batch_get = resource.batch_get_item

        def always_unprocessed(**kwargs):
            resp = original_batch_get(**kwargs)
            resp["UnprocessedKeys"] = kwargs["RequestItems"]
            return resp

        with patch.object(resource, "batch_get_item", side_effect=always_unprocessed):
            with pytest.raises(ThrottlingError, match="batch_read failed"):
                orders.batch_read([
                    {"user_id": "usr_1", "order_id": "ord_1"},
                ])
