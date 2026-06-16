"""Sensor entities for Lawn Control."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import UnitOfLength
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LawnControlCoordinator


@dataclass(frozen=True, kw_only=True)
class LawnSensorEntityDescription(SensorEntityDescription):
    """Description for a Lawn Control sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]]


SENSORS: tuple[LawnSensorEntityDescription, ...] = (
    LawnSensorEntityDescription(
        key="recommended_grass_height",
        translation_key="recommended_grass_height",
        name="Lawn Recommended Grass Height",
        icon="mdi:grass",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        value_fn=lambda data: data["recommended_grass_height"]["value"],
        attrs_fn=lambda data: data["recommended_grass_height"]["attributes"],
    ),
    LawnSensorEntityDescription(
        key="drought_risk",
        translation_key="drought_risk",
        name="Lawn Drought Risk",
        icon="mdi:weather-sunny-alert",
        value_fn=lambda data: data["drought_risk"]["value"],
        attrs_fn=lambda data: data["drought_risk"]["attributes"],
    ),
    LawnSensorEntityDescription(
        key="growth_rate",
        translation_key="growth_rate",
        name="Lawn Growth Rate",
        icon="mdi:sprout",
        value_fn=lambda data: data["growth_rate"]["value"],
        attrs_fn=lambda data: data["growth_rate"]["attributes"],
    ),
    LawnSensorEntityDescription(
        key="fertilizer_score",
        translation_key="fertilizer_score",
        name="Lawn Fertilizer Score",
        icon="mdi:sack",
        value_fn=lambda data: data["fertilizer_score"]["score"],
        attrs_fn=lambda data: data["fertilizer_score"]["attributes"],
    ),
    LawnSensorEntityDescription(
        key="care_recommendation",
        translation_key="care_recommendation",
        name="Lawn Care Recommendation",
        icon="mdi:clipboard-text-outline",
        value_fn=lambda data: data["care_recommendation"]["value"],
        attrs_fn=lambda data: data["care_recommendation"]["attributes"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lawn Control sensors."""
    coordinator: LawnControlCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LawnControlSensor(coordinator, entry, description) for description in SENSORS
    )


class LawnControlSensor(CoordinatorEntity[LawnControlCoordinator], SensorEntity):
    """Lawn Control sensor."""

    entity_description: LawnSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LawnControlCoordinator,
        entry: ConfigEntry,
        description: LawnSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Lawn Control",
            translation_key="lawn",
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return transparent rule details."""
        return self.entity_description.attrs_fn(self.coordinator.data)
