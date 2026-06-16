"""Data coordinator for Lawn Control."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

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

LOCK_START_HOUR = 8
LOCK_END_HOUR = 18
STORAGE_VERSION = 1


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
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._stored_data: dict[str, Any] = {}
        self._unsub_refresh_times: list[Any] = []

    async def async_start(self) -> None:
        """Load stored snapshots and set up scheduled refreshes."""
        self._stored_data = await self._store.async_load() or {}
        self._unsub_refresh_times = [
            async_track_time_change(
                self.hass,
                self._async_refresh_from_schedule,
                hour=LOCK_START_HOUR,
                minute=0,
                second=0,
            ),
            async_track_time_change(
                self.hass,
                self._async_refresh_from_schedule,
                hour=LOCK_END_HOUR,
                minute=0,
                second=0,
            ),
        ]

    async def async_shutdown(self) -> None:
        """Stop scheduled refreshes."""
        for unsub in self._unsub_refresh_times:
            unsub()
        self._unsub_refresh_times = []

    async def _async_refresh_from_schedule(self, now: datetime) -> None:
        """Refresh when a lock window starts or ends."""
        await self.async_request_refresh()

    @property
    def config(self) -> dict[str, Any]:
        """Return merged config entry data and options."""
        return {**self.entry.data, **self.entry.options}

    async def _async_update_data(self) -> dict[str, Any]:
        """Update all calculated advice."""
        forecast = await self._async_get_forecast()
        weather_data = self._read_weather_data(forecast)
        language = getattr(self.hass.config, "language", "en")
        advice = build_advice(self.config, weather_data, language)
        advice, should_save = self._apply_locked_values(advice)
        if should_save:
            await self._store.async_save(self._stored_data)
        return advice

    def _apply_locked_values(
        self, advice: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Apply daily and daytime locks to selected advice values."""
        now = dt_util.now()
        date_key = now.date().isoformat()
        should_save = False

        advice, saved = self._apply_grass_height_lock(advice, now, date_key)
        should_save = should_save or saved

        advice = self._apply_robot_mower_window(advice, now)

        return advice, should_save

    def _apply_grass_height_lock(
        self, advice: dict[str, Any], now: datetime, date_key: str
    ) -> tuple[dict[str, Any], bool]:
        """Lock recommended grass height once per day from 08:00."""
        lock = self._stored_data.get("recommended_grass_height")
        should_save = False

        if now.hour >= LOCK_START_HOUR and (
            not lock or lock.get("date") != date_key
        ):
            lock = {
                "date": date_key,
                "lock_time": _iso_at(now, LOCK_START_HOUR),
                "locked_at": now.isoformat(),
                "data": deepcopy(advice["recommended_grass_height"]),
            }
            self._stored_data["recommended_grass_height"] = lock
            should_save = True

        if lock:
            live = deepcopy(advice["recommended_grass_height"])
            locked = deepcopy(lock["data"])
            locked["attributes"] = {
                **locked.get("attributes", {}),
                "locked": True,
                "lock_time": lock.get("lock_time", lock["locked_at"]),
                "locked_at": lock["locked_at"],
                "next_update": _next_lock_start(now).isoformat(),
                "live_value": live["value"],
                "live_min_height": live["attributes"]["min_height"],
                "live_max_height": live["attributes"]["max_height"],
                "live_reason": live["attributes"]["reason"],
            }
            advice["recommended_grass_height"] = locked

        return advice, should_save

    def _apply_robot_mower_window(
        self, advice: dict[str, Any], now: datetime
    ) -> dict[str, Any]:
        """Limit robot mower advice to 08:00-18:00 while keeping live factors."""
        in_lock_window = LOCK_START_HOUR <= now.hour < LOCK_END_HOUR
        robot = advice["robot_mower_should_run"]
        attrs = robot.get("attributes", {})

        if in_lock_window:
            robot["attributes"] = {
                **attrs,
                "time_window_active": True,
                "window_start": _iso_at(now, LOCK_START_HOUR),
                "window_end": _iso_at(now, LOCK_END_HOUR),
                "next_window_start": _next_lock_start(now).isoformat(),
            }
        else:
            blocking_factors = list(attrs.get("blocking_factors", []))
            blocking_factors.append("Robot mower window is 08:00-18:00.")
            robot["value"] = False
            robot["attributes"] = {
                **attrs,
                "blocking_factors": blocking_factors,
                "reason": "Robot mower is outside the configured 08:00-18:00 window.",
                "time_window_active": False,
                "window_start": _iso_at(now, LOCK_START_HOUR),
                "window_end": _iso_at(now, LOCK_END_HOUR),
                "next_window_start": _next_lock_start(now).isoformat(),
            }

        return advice

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
            except Exception as err:  # noqa: BLE001
                LOGGER.debug("Could not fetch %s forecast: %s", forecast_type, err)
                continue

            if not isinstance(response, dict):
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


def _iso_at(now: datetime, hour: int) -> str:
    """Return an ISO timestamp for today at the given hour."""
    return now.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _next_lock_start(now: datetime) -> datetime:
    """Return the next 08:00 local lock time."""
    next_start = now.replace(
        hour=LOCK_START_HOUR, minute=0, second=0, microsecond=0
    )
    if now >= next_start:
        next_start += timedelta(days=1)
    return next_start
