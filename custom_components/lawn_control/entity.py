"""Shared entity helpers for Lawn Control."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

AVAILABILITY_KEY = "availability"


def read_entity_value(
    data: dict[str, Any] | None,
    value_fn: Callable[[dict[str, Any]], Any],
) -> Any:
    """Read an entity value without leaking malformed coordinator data errors."""
    if not isinstance(data, dict):
        return None

    try:
        return value_fn(data)
    except (KeyError, TypeError):
        return None


def sources_available(data: dict[str, Any] | None) -> bool:
    """Return whether required source entities are available."""
    if not isinstance(data, dict):
        return False

    availability = data.get(AVAILABILITY_KEY, {})
    if not isinstance(availability, dict):
        return True

    return bool(availability.get("source_entities_available", True))


def merge_availability_attributes(
    data: dict[str, Any] | None,
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Return entity attributes with shared source availability details."""
    attrs: dict[str, Any] = {}
    if isinstance(data, dict):
        try:
            attrs.update(attrs_fn(data))
        except (KeyError, TypeError):
            pass

    availability_attrs = availability_attributes(data)
    if availability_attrs:
        attrs.update(availability_attrs)

    return attrs


def availability_attributes(data: dict[str, Any] | None) -> dict[str, Any]:
    """Return common source availability attributes for all entities."""
    if not isinstance(data, dict):
        return {}

    availability = data.get(AVAILABILITY_KEY, {})
    if not isinstance(availability, dict):
        return {}

    attrs: dict[str, Any] = {
        "source_entities_available": availability.get(
            "source_entities_available", True
        )
    }

    for key in (
        "unavailable_source_entities",
        "unavailable_required_entities",
        "unavailable_optional_entities",
    ):
        value = availability.get(key)
        if value:
            attrs[key] = value

    return attrs
