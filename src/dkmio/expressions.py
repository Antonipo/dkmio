"""Expression builder for DynamoDB operations.

Generates KeyConditionExpression, FilterExpression, ProjectionExpression,
UpdateExpression, and ConditionExpression with automatic attribute name escaping.
"""

from __future__ import annotations

import re
from typing import Any

from .exceptions import ValidationError

# Regex to split attribute paths: handles dots and bracket indices
# e.g. "address.city" -> ["address", "city"]
# e.g. "items[0].qty" -> ["items[0]", "qty"]
_PATH_SEGMENT_RE = re.compile(r"(\[[0-9]+\])")

# Operators and their DynamoDB expression templates
_FILTER_OPERATORS = {
    "eq": "{name} = {value}",
    "neq": "{name} <> {value}",
    "gt": "{name} > {value}",
    "gte": "{name} >= {value}",
    "lt": "{name} < {value}",
    "lte": "{name} <= {value}",
    "between": "{name} BETWEEN {value} AND {value2}",
    "begins_with": "begins_with({name}, {value})",
    "contains": "contains({name}, {value})",
    "exists": "attribute_exists({name})",
    "not_exists": "attribute_not_exists({name})",
    "type": "attribute_type({name}, {value})",
    "size": None,  # special handling: size is a path modifier, not an operator
    "in": None,  # special handling: generates multiple value placeholders
    "not_contains": "NOT contains({name}, {value})",
    "not_begins_with": "NOT begins_with({name}, {value})",
}

_SK_OPERATORS = {
    "eq": "{name} = {value}",
    "gt": "{name} > {value}",
    "gte": "{name} >= {value}",
    "lt": "{name} < {value}",
    "lte": "{name} <= {value}",
    "between": "{name} BETWEEN {value} AND {value2}",
    "begins_with": "begins_with({name}, {value})",
}


def _escape_path(path: str) -> tuple[str, dict[str, str]]:
    """Escape an attribute path, handling dots and bracket indices.

    Returns the escaped path and a dict of name mappings.

    Examples:
        "status" -> ("#status", {"#status": "status"})
        "address.city" -> ("#address.#city", {"#address": "address", "#city": "city"})
        "items[0].qty" -> ("#items[0].#qty", {"#items": "items", "#qty": "qty"})
    """
    names: dict[str, str] = {}

    # Split by dots first
    dot_parts = path.split(".")
    escaped_parts = []

    for part in dot_parts:
        # Check for bracket index: e.g. "items[0]"
        segments = _PATH_SEGMENT_RE.split(part)
        escaped_segments = []
        for seg in segments:
            if not seg:
                continue
            if seg.startswith("["):
                # Bracket index, don't escape
                escaped_segments.append(seg)
            else:
                alias = f"#{seg}"
                names[alias] = seg
                escaped_segments.append(alias)
        escaped_parts.append("".join(escaped_segments))

    return ".".join(escaped_parts), names


def _parse_filter_key(key: str) -> tuple[str, str]:
    """Parse a filter key like 'attr__operator' or 'path__size__operator'.

    Returns (attribute_path, operator).

    Handles the special case where __size is a path modifier:
        "items__size__gt" -> ("items", "size__gt")  -> will be handled as size(items) > value
    """
    # Check for __size__operator pattern
    size_match = re.match(r"^(.+)__size__(\w+)$", key)
    if size_match:
        return size_match.group(1), f"size__{size_match.group(2)}"

    # Normal case: split by last __
    idx = key.rfind("__")
    if idx == -1:
        # No operator, default to eq
        return key, "eq"

    attr = key[:idx]
    op = key[idx + 2:]

    # Validate operator
    if op not in _FILTER_OPERATORS:
        raise ValidationError(f"Unknown operator: {op!r}")

    return attr, op


class ExpressionBuilder:
    """Builds DynamoDB expressions with automatic attribute name escaping.

    Each operation should create a new ExpressionBuilder to avoid
    placeholder collisions.
    """

    def __init__(self) -> None:
        self._names: dict[str, str] = {}
        self._values: dict[str, Any] = {}
        self._value_counter = 0

    def add_name(self, attr_path: str) -> str:
        """Escape an attribute path and register name mappings.

        Returns the escaped path string.
        """
        escaped, names = _escape_path(attr_path)
        self._names.update(names)
        return escaped

    def add_value(self, value: Any) -> str:
        """Register a value and return its placeholder."""
        placeholder = f":v{self._value_counter}"
        self._value_counter += 1
        self._values[placeholder] = value
        return placeholder

    def build_key_condition(
        self,
        pk_name: str,
        pk_value: Any,
        sk_condition: Any | None = None,
        sk_name: str | None = None,
    ) -> str:
        """Build KeyConditionExpression for a query.

        Args:
            pk_name: Partition key attribute name.
            pk_value: Partition key value.
            sk_condition: Optional SKCondition for sort key filtering.
            sk_name: Sort key attribute name (required if sk_condition given).
        """
        pk_escaped = self.add_name(pk_name)
        pk_placeholder = self.add_value(pk_value)
        expr = f"{pk_escaped} = {pk_placeholder}"

        if sk_condition is not None:
            if sk_name is None:
                raise ValidationError("sk_name required when sk_condition is provided")

            sk_escaped = self.add_name(sk_name)
            op = sk_condition.operator

            if op not in _SK_OPERATORS:
                raise ValidationError(f"Invalid sort key operator: {op!r}")

            template = _SK_OPERATORS[op]

            if op == "between":
                v1 = self.add_value(sk_condition.values[0])
                v2 = self.add_value(sk_condition.values[1])
                sk_expr = template.format(name=sk_escaped, value=v1, value2=v2)
            else:
                v = self.add_value(sk_condition.values[0])
                sk_expr = template.format(name=sk_escaped, value=v)

            expr = f"{expr} AND {sk_expr}"

        return expr

    def build_filter(self, kwargs: dict[str, Any]) -> str | None:
        """Build FilterExpression from keyword arguments.

        Args:
            kwargs: Dict of 'attr__operator=value' pairs.

        Returns:
            The filter expression string, or None if kwargs is empty.
        """
        if not kwargs:
            return None

        parts = []
        for key, value in kwargs.items():
            attr, op = _parse_filter_key(key)
            expr = self._build_single_condition(attr, op, value)
            parts.append(expr)

        return " AND ".join(parts)

    def _build_single_condition(self, attr: str, op: str, value: Any) -> str:
        """Build a single condition expression."""
        # Handle size modifier: size__gt, size__lt, etc.
        if op.startswith("size__"):
            real_op = op[len("size__"):]
            if real_op not in _FILTER_OPERATORS:
                raise ValidationError(f"Unknown operator after size: {real_op!r}")
            name_escaped = self.add_name(attr)
            size_expr = f"size({name_escaped})"
            template = _FILTER_OPERATORS[real_op]
            assert template is not None, f"Operator {real_op!r} cannot be used with size"
            if real_op == "between":
                v1 = self.add_value(value[0])
                v2 = self.add_value(value[1])
                return template.format(name=size_expr, value=v1, value2=v2)
            else:
                v = self.add_value(value)
                return template.format(name=size_expr, value=v)

        name_escaped = self.add_name(attr)

        if op in ("exists", "not_exists"):
            template = _FILTER_OPERATORS[op]
            assert template is not None
            return template.format(name=name_escaped)

        if op == "between":
            template = _FILTER_OPERATORS[op]
            assert template is not None
            v1 = self.add_value(value[0])
            v2 = self.add_value(value[1])
            return template.format(name=name_escaped, value=v1, value2=v2)

        if op == "in":
            placeholders = [self.add_value(v) for v in value]
            return f"{name_escaped} IN ({', '.join(placeholders)})"

        template = _FILTER_OPERATORS[op]
        assert template is not None, f"Operator {op!r} has no template"
        v = self.add_value(value)
        return template.format(name=name_escaped, value=v)

    def build_projection(self, attrs: list[str]) -> str | None:
        """Build ProjectionExpression from a list of attribute names."""
        if not attrs:
            return None
        escaped = [self.add_name(a) for a in attrs]
        return ", ".join(escaped)

    def build_update(
        self,
        set_: dict[str, Any] | None = None,
        remove: list[str] | None = None,
        append: dict[str, Any] | None = None,
        add: dict[str, Any] | None = None,
        delete: dict[str, Any] | None = None,
    ) -> str | None:
        """Build UpdateExpression with SET, REMOVE, ADD, DELETE clauses.

        Args:
            set_: Attributes to set (SET action).
            remove: Attributes to remove (REMOVE action).
            append: Attributes to append to lists (SET with list_append).
            add: Attributes to add to sets or increment numbers (ADD action).
            delete: Attributes to delete from sets (DELETE action).
        """
        clauses = []

        # SET clause: regular set + append (list_append)
        set_parts = []
        if set_:
            for attr, value in set_.items():
                name = self.add_name(attr)
                val = self.add_value(value)
                set_parts.append(f"{name} = {val}")

        if append:
            for attr, value in append.items():
                name = self.add_name(attr)
                val = self.add_value([value] if not isinstance(value, list) else value)
                set_parts.append(f"{name} = list_append({name}, {val})")

        if set_parts:
            clauses.append("SET " + ", ".join(set_parts))

        # REMOVE clause
        if remove:
            remove_parts = [self.add_name(attr) for attr in remove]
            clauses.append("REMOVE " + ", ".join(remove_parts))

        # ADD clause
        if add:
            add_parts = []
            for attr, value in add.items():
                name = self.add_name(attr)
                val = self.add_value(value)
                add_parts.append(f"{name} {val}")
            clauses.append("ADD " + ", ".join(add_parts))

        # DELETE clause
        if delete:
            del_parts = []
            for attr, value in delete.items():
                name = self.add_name(attr)
                val = self.add_value(value)
                del_parts.append(f"{name} {val}")
            clauses.append("DELETE " + ", ".join(del_parts))

        if not clauses:
            return None

        return " ".join(clauses)

    def build_condition(self, condition_dict: dict[str, Any]) -> str | None:
        """Build ConditionExpression from a dict (AND logic)."""
        if not condition_dict:
            return None
        return self.build_filter(condition_dict)

    def build_condition_or(self, conditions_list: list[dict[str, Any]]) -> str | None:
        """Build ConditionExpression with OR logic.

        Each dict in the list is a group of AND conditions.
        Groups are joined with OR.
        """
        if not conditions_list:
            return None

        or_parts = []
        for cond_dict in conditions_list:
            and_expr = self.build_filter(cond_dict)
            if and_expr:
                # Wrap in parens if multiple ANDs
                if " AND " in and_expr:
                    or_parts.append(f"({and_expr})")
                else:
                    or_parts.append(and_expr)

        if not or_parts:
            return None

        return " OR ".join(or_parts)

    def get_names(self) -> dict[str, str] | None:
        """Return ExpressionAttributeNames, or None if empty."""
        return self._names if self._names else None

    def get_values(self) -> dict[str, Any] | None:
        """Return ExpressionAttributeValues, or None if empty."""
        return self._values if self._values else None
