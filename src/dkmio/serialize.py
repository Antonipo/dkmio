"""Normalize DynamoDB response types to standard Python types.

boto3's DynamoDB resource layer returns:
  - Decimal for numbers (N)
  - set for string/number/binary sets (SS, NS, BS)

These types are not JSON serializable and leak DynamoDB internals
into user code.  This module converts them to native Python types.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Recursively convert DynamoDB types in an item dict.

    - Decimal → int (if whole number) or float
    - set → sorted list (for deterministic output)
    - Recursion into nested dicts and lists
    """
    return {k: _normalize_value(v) for k, v in item.items()}


def _normalize_value(value: Any) -> Any:
    """Recursively convert a single DynamoDB value to a native Python type.

    Conversion rules:
        - ``Decimal`` with no fractional part -> ``int``
        - ``Decimal`` with fractional part -> ``float``
        - ``set`` -> sorted ``list`` (elements are also normalized)
        - ``dict`` -> recursively normalized ``dict``
        - ``list`` -> recursively normalized ``list``
        - All other types are returned unchanged.

    Args:
        value: A value as returned by boto3's DynamoDB resource layer.

    Returns:
        The equivalent native Python value.
    """
    if isinstance(value, Decimal):
        # Preserve int vs float distinction
        if value == int(value):
            return int(value)
        return float(value)

    if isinstance(value, set):
        # Convert set elements too (handles NS = set of Decimal)
        converted = [_normalize_value(v) for v in value]
        # Sort for deterministic output; mixed types fall back to unsorted
        try:
            return sorted(converted)
        except TypeError:
            return converted

    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_normalize_value(v) for v in value]

    return value


def normalize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a list of item dicts."""
    return [normalize_item(item) for item in items]
