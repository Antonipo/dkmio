"""Tests for ExpressionBuilder — the core expression engine."""

import pytest

from dkmio.exceptions import ValidationError
from dkmio.expressions import ExpressionBuilder, _escape_path, _parse_filter_key
from dkmio.fields import SKCondition


class TestEscapePath:
    def test_simple_attribute(self):
        escaped, names = _escape_path("status")
        assert escaped == "#status"
        assert names == {"#status": "status"}

    def test_dotted_path(self):
        escaped, names = _escape_path("address.city")
        assert escaped == "#address.#city"
        assert names == {"#address": "address", "#city": "city"}

    def test_bracket_index(self):
        escaped, names = _escape_path("items[0].qty")
        assert escaped == "#items[0].#qty"
        assert names == {"#items": "items", "#qty": "qty"}

    def test_multiple_brackets(self):
        escaped, names = _escape_path("data[0].nested[1].value")
        assert escaped == "#data[0].#nested[1].#value"

    def test_bracket_only(self):
        escaped, names = _escape_path("items[0]")
        assert escaped == "#items[0]"
        assert names == {"#items": "items"}


class TestParseFilterKey:
    def test_simple_eq(self):
        attr, op = _parse_filter_key("status__eq")
        assert attr == "status"
        assert op == "eq"

    def test_no_operator_defaults_to_eq(self):
        attr, op = _parse_filter_key("status")
        assert attr == "status"
        assert op == "eq"

    def test_nested_path(self):
        attr, op = _parse_filter_key("address.city__eq")
        assert attr == "address.city"
        assert op == "eq"

    def test_size_operator(self):
        attr, op = _parse_filter_key("items__size__gt")
        assert attr == "items"
        assert op == "size__gt"

    def test_unknown_operator(self):
        with pytest.raises(ValidationError, match="Unknown operator"):
            _parse_filter_key("status__invalid")

    def test_between(self):
        attr, op = _parse_filter_key("total__between")
        assert attr == "total"
        assert op == "between"

    def test_begins_with(self):
        attr, op = _parse_filter_key("name__begins_with")
        assert attr == "name"
        assert op == "begins_with"

    def test_contains(self):
        attr, op = _parse_filter_key("tags__contains")
        assert attr == "tags"
        assert op == "contains"

    def test_exists(self):
        attr, op = _parse_filter_key("email__exists")
        assert attr == "email"
        assert op == "exists"

    def test_not_exists(self):
        attr, op = _parse_filter_key("email__not_exists")
        assert attr == "email"
        assert op == "not_exists"

    def test_type(self):
        attr, op = _parse_filter_key("data__type")
        assert attr == "data"
        assert op == "type"


class TestBuildKeyCondition:
    def test_pk_only(self):
        b = ExpressionBuilder()
        expr = b.build_key_condition("user_id", "usr_123")
        assert expr == "#user_id = :v0"
        assert b.get_names() == {"#user_id": "user_id"}
        assert b.get_values() == {":v0": "usr_123"}

    def test_pk_and_sk_eq(self):
        b = ExpressionBuilder()
        sk_cond = SKCondition("eq", ("ord_456",))
        expr = b.build_key_condition("user_id", "usr_123", sk_cond, "order_id")
        assert expr == "#user_id = :v0 AND #order_id = :v1"
        assert b.get_values() == {":v0": "usr_123", ":v1": "ord_456"}

    def test_pk_and_sk_between(self):
        b = ExpressionBuilder()
        sk_cond = SKCondition("between", ("A", "Z"))
        expr = b.build_key_condition("user_id", "usr_123", sk_cond, "order_id")
        assert "BETWEEN" in expr
        assert b.get_values() == {":v0": "usr_123", ":v1": "A", ":v2": "Z"}

    def test_pk_and_sk_begins_with(self):
        b = ExpressionBuilder()
        sk_cond = SKCondition("begins_with", ("ord_",))
        expr = b.build_key_condition("user_id", "usr_123", sk_cond, "order_id")
        assert "begins_with(#order_id, :v1)" in expr

    def test_sk_condition_without_sk_name_raises(self):
        b = ExpressionBuilder()
        sk_cond = SKCondition("eq", ("val",))
        with pytest.raises(ValidationError, match="sk_name required"):
            b.build_key_condition("pk", "val", sk_cond)


class TestBuildFilter:
    def test_empty(self):
        b = ExpressionBuilder()
        assert b.build_filter({}) is None

    def test_single_eq(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"status__eq": "PENDING"})
        assert expr == "#status = :v0"
        assert b.get_values() == {":v0": "PENDING"}

    def test_multiple_conditions(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"status__eq": "PENDING", "total__gt": 100})
        assert "#status = :v0" in expr
        assert "#total > :v1" in expr
        assert " AND " in expr

    def test_neq(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"status__neq": "CANCELLED"})
        assert "#status <> :v0" in expr

    def test_gte(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"total__gte": 100})
        assert "#total >= :v0" in expr

    def test_lte(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"total__lte": 500})
        assert "#total <= :v0" in expr

    def test_between(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"total__between": [100, 500]})
        assert "BETWEEN" in expr
        assert b.get_values() == {":v0": 100, ":v1": 500}

    def test_begins_with(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"name__begins_with": "John"})
        assert "begins_with(#name, :v0)" in expr

    def test_contains(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"tags__contains": "urgent"})
        assert "contains(#tags, :v0)" in expr

    def test_exists(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"email__exists": True})
        assert "attribute_exists(#email)" in expr
        # exists doesn't add a value
        assert b.get_values() is None

    def test_not_exists(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"email__not_exists": True})
        assert "attribute_not_exists(#email)" in expr

    def test_type(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"data__type": "M"})
        assert "attribute_type(#data, :v0)" in expr

    def test_size_gt(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"items__size__gt": 0})
        assert "size(#items) > :v0" in expr

    def test_size_eq(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"items__size__eq": 0})
        assert "size(#items) = :v0" in expr

    def test_size_in_raises_validation_error(self):
        """Size__in should raise ValidationError, not AssertionError."""
        b = ExpressionBuilder()
        with pytest.raises(ValidationError, match="cannot be used with size"):
            b.build_filter({"items__size__in": [1, 2]})

    def test_size_exists_raises_validation_error(self):
        """Size__exists should raise ValidationError, not AssertionError."""
        b = ExpressionBuilder()
        with pytest.raises(ValidationError, match="cannot be used with size"):
            b.build_filter({"items__size__exists": True})

    def test_size_not_exists_raises_validation_error(self):
        """Size__not_exists should raise ValidationError."""
        b = ExpressionBuilder()
        with pytest.raises(ValidationError, match="cannot be used with size"):
            b.build_filter({"items__size__not_exists": True})

    def test_nested_path(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"address.city__eq": "Lima"})
        assert "#address.#city = :v0" in expr

    def test_no_operator_defaults_eq(self):
        b = ExpressionBuilder()
        expr = b.build_filter({"status": "PENDING"})
        assert "#status = :v0" in expr


class TestBuildProjection:
    def test_empty(self):
        b = ExpressionBuilder()
        assert b.build_projection([]) is None

    def test_single(self):
        b = ExpressionBuilder()
        proj = b.build_projection(["status"])
        assert proj == "#status"

    def test_multiple(self):
        b = ExpressionBuilder()
        proj = b.build_projection(["status", "total", "created_at"])
        assert "#status" in proj
        assert "#total" in proj
        assert "#created_at" in proj

    def test_nested_attribute(self):
        b = ExpressionBuilder()
        proj = b.build_projection(["address.city"])
        assert proj == "#address.#city"


class TestBuildUpdate:
    def test_empty(self):
        b = ExpressionBuilder()
        assert b.build_update() is None

    def test_set_only(self):
        b = ExpressionBuilder()
        expr = b.build_update(set_={"status": "SHIPPED"})
        assert expr == "SET #status = :v0"
        assert b.get_values() == {":v0": "SHIPPED"}

    def test_set_multiple(self):
        b = ExpressionBuilder()
        expr = b.build_update(set_={"status": "SHIPPED", "shipped_at": "2025-02-24"})
        assert "SET" in expr
        assert "#status = :v0" in expr
        assert "#shipped_at = :v1" in expr

    def test_remove_only(self):
        b = ExpressionBuilder()
        expr = b.build_update(remove=["temp_notes"])
        assert expr == "REMOVE #temp_notes"

    def test_remove_nested(self):
        b = ExpressionBuilder()
        expr = b.build_update(remove=["address.zipcode", "tags[2]"])
        assert "REMOVE #address.#zipcode, #tags[2]" in expr

    def test_append(self):
        b = ExpressionBuilder()
        expr = b.build_update(append={"items": {"product": "mouse"}})
        assert "SET #items = list_append(#items, :v0)" in expr
        assert b.get_values() == {":v0": [{"product": "mouse"}]}

    def test_add(self):
        b = ExpressionBuilder()
        expr = b.build_update(add={"tags": {"urgent", "priority"}})
        assert "ADD #tags :v0" in expr

    def test_delete(self):
        b = ExpressionBuilder()
        expr = b.build_update(delete={"tags": {"old_tag"}})
        assert "DELETE #tags :v0" in expr

    def test_combined(self):
        b = ExpressionBuilder()
        expr = b.build_update(
            set_={"status": "PROCESSING"},
            remove=["temp_data"],
            add={"tags": {"processed"}},
            delete={"tags": {"pending"}},
        )
        assert "SET" in expr
        assert "REMOVE" in expr
        assert "ADD" in expr
        assert "DELETE" in expr

    def test_set_nested_path(self):
        b = ExpressionBuilder()
        expr = b.build_update(set_={"address.city": "Lima"})
        assert "SET #address.#city = :v0" in expr

    def test_set_bracket_path(self):
        b = ExpressionBuilder()
        expr = b.build_update(set_={"items[0].quantity": 5})
        assert "SET #items[0].#quantity = :v0" in expr


class TestBuildCondition:
    def test_empty(self):
        b = ExpressionBuilder()
        assert b.build_condition({}) is None

    def test_single(self):
        b = ExpressionBuilder()
        expr = b.build_condition({"status__eq": "PENDING"})
        assert expr == "#status = :v0"

    def test_multiple_and(self):
        b = ExpressionBuilder()
        expr = b.build_condition({"status__eq": "PENDING", "total__gt": 100})
        assert " AND " in expr


class TestBuildConditionOr:
    def test_empty(self):
        b = ExpressionBuilder()
        assert b.build_condition_or([]) is None

    def test_single_group(self):
        b = ExpressionBuilder()
        expr = b.build_condition_or([{"status__eq": "PENDING"}])
        assert expr == "#status = :v0"

    def test_multiple_groups(self):
        b = ExpressionBuilder()
        expr = b.build_condition_or([
            {"status__eq": "PENDING"},
            {"status__eq": "DRAFT"},
        ])
        assert " OR " in expr
        assert "#status = :v0" in expr
        assert "#status = :v1" in expr

    def test_complex_groups(self):
        b = ExpressionBuilder()
        expr = b.build_condition_or([
            {"status__eq": "PENDING", "total__gt": 100},
            {"status__eq": "DRAFT"},
        ])
        assert " OR " in expr
        # First group has AND, should be wrapped in parens
        assert "(" in expr


class TestGetNamesValues:
    def test_none_when_empty(self):
        b = ExpressionBuilder()
        assert b.get_names() is None
        assert b.get_values() is None

    def test_incremental_values(self):
        b = ExpressionBuilder()
        p1 = b.add_value("a")
        p2 = b.add_value("b")
        assert p1 == ":v0"
        assert p2 == ":v1"
        assert b.get_values() == {":v0": "a", ":v1": "b"}
