"""Binary sensor entities for Lawn Control."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LawnControlCoordinator


@dataclass(frozen=True, kw_only=True)
class LawnBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Description for a Lawn Control binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]]


BINARY_SENSORS: tuple[LawnBinarySensorEntityDescription, ...] = (
    LawnBinarySensorEntityDescription(
        key="good_day_for_fertilizer",
        translation_key="good_day_for_fertilizer",
        name="Lawn Good Day For Fertilizer",
        value_fn=lambda data: data["fertilizer_day"]["value"],
        attrs_fn=lambda data: data["fertilizer_day"]["attributes"],
    ),
    LawnBinarySensorEntityDescription(
        key="should_mow",
        translation_key="should_mow",
        name="Lawn Should Mow",
        value_fn=lambda data: data["should_mow"]["value"],
        attrs_fn=lambda data: data["should_mow"]["attributes"],
    ),
    LawnBinarySensorEntityDescription(
        key="robot_mower_should_run",
        translation_key="robot_mower_should_run",
        name="Lawn Robot Mower Should Run",
        value_fn=lambda data: data["robot_mower_should_run"]["value"],
        attrs_fn=lambda data: data["robot_mower_should_run"]["attributes"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lawn Control binary sensors."""
    coordinator: LawnControlCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LawnControlBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSORS
    )


class LawnControlBinarySensor(
    CoordinatorEntity[LawnControlCoordinator], BinarySensorEntity
):
    """Lawn Control binary sensor."""

    entity_description: LawnBinarySensorEntityDescription
    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: LawnControlCoordinator,
        entry: ConfigEntry,
        description: LawnBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Lawn Control",
        )

    @property
    def is_on(self) -> bool:
        """Return true if the binary sensor is on."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return transparent rule details."""
        return self.entity_description.attrs_fn(self.coordinator.data)
