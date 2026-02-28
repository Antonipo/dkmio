"""Tests for field descriptors: PK, SK, Index, TTL."""

import time

from dkmio.fields import PK, SK, TTL, Index, SKCondition


class TestPK:
    def test_attribute_name(self):
        pk = PK("user_id")
        assert pk.attribute_name == "user_id"

    def test_repr(self):
        pk = PK("user_id")
        assert repr(pk) == "PK('user_id')"


class TestSK:
    def test_attribute_name(self):
        sk = SK("order_id")
        assert sk.attribute_name == "order_id"

    def test_eq(self):
        sk = SK("order_id")
        cond = sk.eq("ord_123")
        assert isinstance(cond, SKCondition)
        assert cond.operator == "eq"
        assert cond.values == ("ord_123",)

    def test_gt(self):
        cond = SK("ts").gt(100)
        assert cond.operator == "gt"
        assert cond.values == (100,)

    def test_gte(self):
        cond = SK("ts").gte(100)
        assert cond.operator == "gte"

    def test_lt(self):
        cond = SK("ts").lt(100)
        assert cond.operator == "lt"

    def test_lte(self):
        cond = SK("ts").lte(100)
        assert cond.operator == "lte"

    def test_between(self):
        cond = SK("ts").between(100, 200)
        assert cond.operator == "between"
        assert cond.values == (100, 200)

    def test_begins_with(self):
        cond = SK("order_id").begins_with("ord_")
        assert cond.operator == "begins_with"
        assert cond.values == ("ord_",)

    def test_repr(self):
        assert repr(SK("order_id")) == "SK('order_id')"


class TestIndex:
    def test_basic(self):
        idx = Index("gsi-status", pk="status")
        assert idx.index_name == "gsi-status"
        assert idx.pk == "status"
        assert idx.sk is None
        assert idx.projection is None

    def test_with_sk_and_projection(self):
        idx = Index("gsi-status-date", pk="status", sk="created_at", projection=["total"])
        assert idx.sk == "created_at"
        assert idx.projection == ["total"]

    def test_projection_type_all(self):
        assert Index("i", pk="p").projection_type == "ALL"
        assert Index("i", pk="p", projection="ALL").projection_type == "ALL"

    def test_projection_type_keys_only(self):
        assert Index("i", pk="p", projection="KEYS_ONLY").projection_type == "KEYS_ONLY"

    def test_projection_type_include(self):
        assert Index("i", pk="p", projection=["a", "b"]).projection_type == "INCLUDE"

    def test_available_attributes_all(self):
        idx = Index("i", pk="status")
        attrs = idx.available_attributes("user_id", "order_id")
        assert attrs == set()  # empty means all available

    def test_available_attributes_keys_only(self):
        idx = Index("i", pk="status", sk="created_at", projection="KEYS_ONLY")
        attrs = idx.available_attributes("user_id", "order_id")
        assert attrs == {"status", "created_at", "user_id", "order_id"}

    def test_available_attributes_include(self):
        idx = Index("i", pk="status", sk="created_at", projection=["total", "items_count"])
        attrs = idx.available_attributes("user_id", "order_id")
        assert attrs == {"status", "created_at", "user_id", "order_id", "total", "items_count"}

    def test_repr(self):
        idx = Index("gsi-status-date", pk="status", sk="created_at", projection=["total"])
        r = repr(idx)
        assert "gsi-status-date" in r
        assert "status" in r


class TestTTL:
    def test_attribute_name(self):
        ttl = TTL("expires_at")
        assert ttl.attribute_name == "expires_at"

    def test_from_now_seconds(self):
        ttl = TTL("expires_at")
        before = int(time.time()) + 3600
        result = ttl.from_now(seconds=3600)
        after = int(time.time()) + 3600
        assert before <= result <= after

    def test_from_now_days(self):
        ttl = TTL("expires_at")
        result = ttl.from_now(days=7)
        expected = int(time.time()) + 7 * 86400
        assert abs(result - expected) <= 1

    def test_from_now_combined(self):
        ttl = TTL("expires_at")
        result = ttl.from_now(days=1, hours=6, minutes=30)
        expected = int(time.time()) + 86400 + 6 * 3600 + 30 * 60
        assert abs(result - expected) <= 1

    def test_repr(self):
        assert repr(TTL("expires_at")) == "TTL('expires_at')"
