"""Condition expression parser for conditional writes.

Thin layer that parses `condition=` and `condition_or=` parameters
into DynamoDB ConditionExpression strings.
"""

from __future__ import annotations

from typing import Any

from .expressions import ExpressionBuilder


def parse_conditions(
    builder: ExpressionBuilder,
    condition: dict[str, Any] | None = None,
    condition_or: list[dict[str, Any]] | None = None,
) -> str | None:
    """Parse condition and condition_or into a ConditionExpression string.

    Args:
        builder: ExpressionBuilder to register names/values.
        condition: Dict of conditions joined with AND.
        condition_or: List of condition dicts joined with OR.

    Returns:
        The combined ConditionExpression string, or None if no conditions.
    """
    parts = []

    if condition:
        and_expr = builder.build_condition(condition)
        if and_expr:
            parts.append(and_expr)

    if condition_or:
        or_expr = builder.build_condition_or(condition_or)
        if or_expr:
            parts.append(or_expr)

    if not parts:
        return None

    if len(parts) == 1:
        return parts[0]

    # Both condition (AND) and condition_or (OR) provided — combine with AND
    return " AND ".join(f"({p})" for p in parts)
