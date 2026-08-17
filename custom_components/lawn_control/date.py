"""Date entities for Lawn Control."""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity, DateEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LawnControlCoordinator


DATE_ENTITIES: tuple[DateEntityDescription, ...] = (
    DateEntityDescription(
        key="last_fertilized_date",
        translation_key="last_fertilized_date",
        name="Lawn Last Fertilized Date",
        icon="mdi:calendar-edit",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lawn Control date entities."""
    coordinator: LawnControlCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LawnControlDateEntity(coordinator, entry, description)
        for description in DATE_ENTITIES
    )


class LawnControlDateEntity(CoordinatorEntity[LawnControlCoordinator], DateEntity):
    """Lawn Control date entity."""

    entity_description: DateEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LawnControlCoordinator,
        entry: ConfigEntry,
        description: DateEntityDescription,
    ) -> None:
        """Initialize the date entity."""
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
    def native_value(self) -> date | None:
        """Return the configured date."""
        return self.coordinator.last_fertilized_date

    async def async_set_value(self, value: date) -> None:
        """Set the latest fertilizer date."""
        await self.coordinator.async_set_last_fertilized_date(value)
