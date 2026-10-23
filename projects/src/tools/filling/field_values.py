"""Resolve legacy labels and canonical field IDs consistently across writers."""
from collections.abc import Mapping
from typing import Any


def field_value(field: Mapping[str, Any], data: Mapping[str, Any]) -> Any:
    """Prefer an explicit ID, including an explicit None; preserve 0 and False."""
    field_id = field.get("field_id")
    if field_id and field_id in data:
        return data[field_id]
    return data.get(field["label"])
