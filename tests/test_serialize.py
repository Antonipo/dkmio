"""Tests for DynamoDB type normalization."""

import json
from decimal import Decimal

import pytest

from dkmio.serialize import normalize_item, normalize_items, _normalize_value


class TestNormalizeValue:
    """Unit tests for _normalize_value."""

    def test_decimal_integer(self):
        assert _normalize_value(Decimal("42")) == 42
        assert isinstance(_normalize_value(Decimal("42")), int)

    def test_decimal_float(self):
        assert _normalize_value(Decimal("3.14")) == 3.14
        assert isinstance(_normalize_value(Decimal("3.14")), float)

    def test_decimal_zero(self):
        assert _normalize_value(Decimal("0")) == 0
        assert isinstance(_normalize_value(Decimal("0")), int)

    def test_decimal_negative(self):
        assert _normalize_value(Decimal("-5")) == -5
        assert isinstance(_normalize_value(Decimal("-5")), int)

    def test_decimal_negative_float(self):
        assert _normalize_value(Decimal("-2.5")) == -2.5
        assert isinstance(_normalize_value(Decimal("-2.5")), float)

    def test_set_of_strings(self):
        result = _normalize_value({"b", "a", "c"})
        assert result == ["a", "b", "c"]
        assert isinstance(result, list)

    def test_set_of_decimals(self):
        """NS type — set of Decimal → sorted list of int/float."""
        result = _normalize_value({Decimal("3"), Decimal("1"), Decimal("2")})
        assert result == [1, 2, 3]
        assert all(isinstance(v, int) for v in result)

    def test_set_of_bytes(self):
        """BS type — set of bytes → list."""
        result = _normalize_value({b"hello", b"world"})
        assert isinstance(result, list)
        assert set(result) == {b"hello", b"world"}

    def test_empty_set(self):
        result = _normalize_value(set())
        assert result == []

    def test_nested_dict(self):
        result = _normalize_value({"city": "Lima", "pop": Decimal("1000000")})
        assert result == {"city": "Lima", "pop": 1000000}
        assert isinstance(result["pop"], int)

    def test_nested_list(self):
        result = _normalize_value([Decimal("1"), Decimal("2.5")])
        assert result == [1, 2.5]

    def test_passthrough_string(self):
        assert _normalize_value("hello") == "hello"

    def test_passthrough_bool(self):
        assert _normalize_value(True) is True

    def test_passthrough_none(self):
        assert _normalize_value(None) is None

    def test_passthrough_int(self):
        assert _normalize_value(42) == 42

    def test_passthrough_bytes(self):
        assert _normalize_value(b"data") == b"data"


class TestNormalizeItem:
    """Unit tests for normalize_item."""

    def test_full_dynamodb_item(self):
        """Simulates a realistic DynamoDB item with all special types."""
        raw = {
            "user_id": "usr_123",
            "order_id": "ord_456",
            "total": Decimal("250"),
            "price": Decimal("49.99"),
            "is_active": True,
            "tags": {"urgent", "priority"},
            "items": [
                {"product": "keyboard", "qty": Decimal("1"), "price": Decimal("50.00")},
            ],
            "address": {"city": "Lima", "zip": "15001"},
            "metadata": None,
        }
        result = normalize_item(raw)

        assert result["total"] == 250
        assert isinstance(result["total"], int)
        assert result["price"] == 49.99
        assert isinstance(result["price"], float)
        assert result["is_active"] is True
        assert isinstance(result["tags"], list)
        assert "urgent" in result["tags"]
        assert "priority" in result["tags"]
        assert result["items"][0]["qty"] == 1
        assert isinstance(result["items"][0]["qty"], int)
        assert result["address"]["city"] == "Lima"
        assert result["metadata"] is None

    def test_json_serializable(self):
        """Items must be JSON serializable."""
        raw = {
            "id": "test",
            "count": Decimal("42"),
            "price": Decimal("9.99"),
            "tags": {"a", "b"},
            "scores": {Decimal("1"), Decimal("2"), Decimal("3")},
            "nested": {
                "value": Decimal("100"),
                "items": [{"x": Decimal("0.5")}],
            },
        }
        result = normalize_item(raw)
        # This must not raise TypeError
        serialized = json.dumps(result)
        parsed = json.loads(serialized)
        assert parsed["count"] == 42
        assert parsed["price"] == 9.99
        assert sorted(parsed["tags"]) == ["a", "b"]
        assert sorted(parsed["scores"]) == [1, 2, 3]
        assert parsed["nested"]["value"] == 100
        assert parsed["nested"]["items"][0]["x"] == 0.5


class TestNormalizeItems:
    def test_list_of_items(self):
        raw = [
            {"id": "1", "val": Decimal("10")},
            {"id": "2", "val": Decimal("20.5")},
        ]
        result = normalize_items(raw)
        assert len(result) == 2
        assert result[0]["val"] == 10
        assert isinstance(result[0]["val"], int)
        assert result[1]["val"] == 20.5
        assert isinstance(result[1]["val"], float)

    def test_empty_list(self):
        assert normalize_items([]) == []
