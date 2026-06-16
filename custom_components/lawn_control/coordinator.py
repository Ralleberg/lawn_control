"""Data coordinator for Lawn Control."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_HUMIDITY_SENSOR,
    CONF_RAIN_SENSOR,
    CONF_SOIL_MOISTURE_SENSOR,
    CONF_TEMPERATURE_SENSOR,
    CONF_WEATHER_ENTITY,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .rules.care import build_advice

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LawnWeatherData:
    """Weather inputs used by the rule engine."""

    weather_state: str | None
    temperature: float | None
    humidity: float | None
    recent_rain: float | None
    soil_moisture: float | None
    forecast_rain: float | None
    forecast_condition: str | None
    month: int


class LawnControlCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch inputs and calculate lawn advice."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self.entry = entry

    @property
    def config(self) -> dict[str, Any]:
        """Return merged config entry data and options."""
        return {**self.entry.data, **self.entry.options}

    async def _async_update_data(self) -> dict[str, Any]:
        """Update all calculated advice."""
        forecast = await self._async_get_forecast()
        weather_data = self._read_weather_data(forecast)
        return build_advice(self.config, weather_data)

    async def _async_get_forecast(self) -> list[dict[str, Any]]:
        """Fetch forecast data from the configured weather entity."""
        entity_id = self.config[CONF_WEATHER_ENTITY]

        for forecast_type in ("hourly", "daily"):
            try:
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"type": forecast_type},
                    target={"entity_id": [entity_id]},
                    blocking=True,
                    return_response=True,
                )
            except HomeAssistantError as err:
                LOGGER.debug("Could not fetch %s forecast: %s", forecast_type, err)
                continue

            forecast = response.get(entity_id, {}).get("forecast", [])
            if forecast:
                return forecast

        return []

    def _read_weather_data(self, forecast: list[dict[str, Any]]) -> LawnWeatherData:
        """Read weather and optional sensor states from Home Assistant."""
        config = self.config
        weather_state = self.hass.states.get(config[CONF_WEATHER_ENTITY])
        weather_attrs = weather_state.attributes if weather_state else {}

        forecast = forecast or weather_attrs.get("forecast") or []
        first_forecast = forecast[0] if forecast else {}

        temperature = self._read_float_sensor(CONF_TEMPERATURE_SENSOR)
        humidity = self._read_float_sensor(CONF_HUMIDITY_SENSOR)

        return LawnWeatherData(
            weather_state=weather_state.state if weather_state else None,
            temperature=temperature
            if temperature is not None
            else _as_float(weather_attrs.get("temperature")),
            humidity=humidity
            if humidity is not None
            else _as_float(weather_attrs.get("humidity")),
            recent_rain=self._read_float_sensor(CONF_RAIN_SENSOR),
            soil_moisture=self._read_float_sensor(CONF_SOIL_MOISTURE_SENSOR),
            forecast_rain=_forecast_precipitation(forecast),
            forecast_condition=first_forecast.get("condition"),
            month=datetime.now().month,
        )

    def _read_float_sensor(self, config_key: str) -> float | None:
        """Read an optional numeric sensor configured by entity id."""
        entity_id = self.config.get(config_key)
        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)
        if state is None:
            return None

        return _as_float(state.state)


def _as_float(value: Any) -> float | None:
    """Convert a value to float when possible."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _forecast_precipitation(forecast: list[dict[str, Any]]) -> float | None:
    """Estimate near-term forecast precipitation from weather attributes."""
    if not forecast:
        return None

    forecast_window = 24 if len(forecast) > 8 else 3
    total = 0.0
    found = False
    for item in forecast[:forecast_window]:
        value = item.get("precipitation")
        rain = _as_float(value)
        if rain is not None:
            total += rain
            found = True

    return total if found else None
