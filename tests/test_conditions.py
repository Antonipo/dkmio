"""Tests for condition expression parsing."""

from dkmio.conditions import parse_conditions
from dkmio.expressions import ExpressionBuilder


class TestParseConditions:
    def test_no_conditions(self):
        b = ExpressionBuilder()
        assert parse_conditions(b) is None

    def test_condition_only(self):
        b = ExpressionBuilder()
        expr = parse_conditions(b, condition={"status__eq": "PENDING"})
        assert expr == "#status = :v0"

    def test_condition_multiple_and(self):
        b = ExpressionBuilder()
        expr = parse_conditions(b, condition={"status__eq": "PENDING", "total__gt": 100})
        assert " AND " in expr

    def test_condition_or_only(self):
        b = ExpressionBuilder()
        expr = parse_conditions(
            b,
            condition_or=[
                {"status__eq": "PENDING"},
                {"status__eq": "DRAFT"},
            ],
        )
        assert " OR " in expr

    def test_both_condition_and_condition_or(self):
        b = ExpressionBuilder()
        expr = parse_conditions(
            b,
            condition={"total__gt": 100},
            condition_or=[
                {"status__eq": "PENDING"},
                {"status__eq": "DRAFT"},
            ],
        )
        assert "(" in expr
        assert " AND " in expr

    def test_condition_exists(self):
        b = ExpressionBuilder()
        expr = parse_conditions(b, condition={"user_id__not_exists": True})
        assert "attribute_not_exists(#user_id)" in expr

    def test_condition_nested(self):
        b = ExpressionBuilder()
        expr = parse_conditions(b, condition={"address.city__eq": "Lima"})
        assert "#address.#city = :v0" in expr
