"""Number entities for Lawn Control."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_FERTILIZER_K_PERCENT,
    CONF_FERTILIZER_N_PERCENT,
    CONF_FERTILIZER_P_PERCENT,
    DOMAIN,
)
from .coordinator import LawnControlCoordinator


@dataclass(frozen=True, kw_only=True)
class LawnNumberEntityDescription(NumberEntityDescription):
    """Description for a Lawn Control number entity."""

    config_key: str


NUMBER_ENTITIES: tuple[LawnNumberEntityDescription, ...] = (
    LawnNumberEntityDescription(
        key=CONF_FERTILIZER_N_PERCENT,
        translation_key=CONF_FERTILIZER_N_PERCENT,
        name="Lawn Fertilizer N Percent",
        icon="mdi:alpha-n-circle",
        native_min_value=0,
        native_max_value=40,
        native_step=0.5,
        native_unit_of_measurement="%",
        mode="box",
        config_key=CONF_FERTILIZER_N_PERCENT,
    ),
    LawnNumberEntityDescription(
        key=CONF_FERTILIZER_P_PERCENT,
        translation_key=CONF_FERTILIZER_P_PERCENT,
        name="Lawn Fertilizer P Percent",
        icon="mdi:alpha-p-circle",
        native_min_value=0,
        native_max_value=40,
        native_step=0.5,
        native_unit_of_measurement="%",
        mode="box",
        config_key=CONF_FERTILIZER_P_PERCENT,
    ),
    LawnNumberEntityDescription(
        key=CONF_FERTILIZER_K_PERCENT,
        translation_key=CONF_FERTILIZER_K_PERCENT,
        name="Lawn Fertilizer K Percent",
        icon="mdi:alpha-k-circle",
        native_min_value=0,
        native_max_value=40,
        native_step=0.5,
        native_unit_of_measurement="%",
        mode="box",
        config_key=CONF_FERTILIZER_K_PERCENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lawn Control number entities."""
    coordinator: LawnControlCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LawnControlNumberEntity(coordinator, entry, description)
        for description in NUMBER_ENTITIES
    )


class LawnControlNumberEntity(
    CoordinatorEntity[LawnControlCoordinator], NumberEntity
):
    """Lawn Control number entity."""

    entity_description: LawnNumberEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LawnControlCoordinator,
        entry: ConfigEntry,
        description: LawnNumberEntityDescription,
    ) -> None:
        """Initialize the number entity."""
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
    def native_value(self) -> float:
        """Return the configured percentage."""
        return self.coordinator.fertilizer_percent(self.entity_description.config_key)

    async def async_set_native_value(self, value: float) -> None:
        """Set the fertilizer percentage."""
        await self.coordinator.async_set_fertilizer_percent(
            self.entity_description.config_key,
            value,
        )
