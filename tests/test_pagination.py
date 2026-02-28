"""Tests for QueryResult pagination wrapper."""

from dkmio.pagination import QueryResult


class TestQueryResult:
    def test_empty(self):
        r = QueryResult()
        assert len(r) == 0
        assert r.count == 0
        assert not r
        assert r.last_key is None
        assert list(r) == []

    def test_with_items(self):
        items = [{"id": 1}, {"id": 2}]
        r = QueryResult(items=items)
        assert len(r) == 2
        assert r.count == 2
        assert bool(r) is True
        assert r[0] == {"id": 1}
        assert r[1] == {"id": 2}

    def test_iteration(self):
        items = [{"a": 1}, {"a": 2}, {"a": 3}]
        r = QueryResult(items=items)
        collected = [item for item in r]
        assert collected == items

    def test_last_key(self):
        r = QueryResult(
            items=[{"id": 1}],
            last_key={"user_id": "usr_123", "order_id": "ord_999"},
        )
        assert r.last_key == {"user_id": "usr_123", "order_id": "ord_999"}

    def test_scanned_count(self):
        r = QueryResult(items=[{"id": 1}], count=1, scanned_count=10)
        assert r.count == 1
        assert r.scanned_count == 10

    def test_getitem_slice(self):
        items = [{"id": i} for i in range(5)]
        r = QueryResult(items=items)
        assert r[1:3] == [{"id": 1}, {"id": 2}]

    def test_repr(self):
        r = QueryResult(items=[{"id": 1}], last_key={"pk": "x"})
        assert "has_more=True" in repr(r)

        r2 = QueryResult(items=[{"id": 1}])
        assert "has_more=False" in repr(r2)
