"""Data coordinator for Lawn Control."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DAILY_UPDATE_HOUR,
    CONF_DAYS_SINCE_FERTILIZER,
    CONF_HUMIDITY_SENSOR,
    CONF_LAST_FERTILIZED_DATE,
    CONF_RAIN_SENSOR,
    CONF_SOIL_MOISTURE_SENSOR,
    CONF_TEMPERATURE_SENSOR,
    CONF_WEATHER_ENTITY,
    DEFAULT_DAILY_UPDATE_HOUR,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .rules.care import build_advice

LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
WEATHER_HISTORY_HOURS = 24
RAIN_HISTORY_HOURS = 120
WEATHER_HISTORY_KEEP_HOURS = 120


@dataclass(slots=True)
class LawnWeatherData:
    """Weather inputs used by the rule engine."""

    weather_state: str | None
    temperature: float | None
    humidity: float | None
    recent_rain: float | None
    soil_moisture: float | None
    forecast_rain: float | None
    forecast_rain_5_days: float | None
    forecast_condition: str | None
    historical_temperature: float | None
    historical_humidity: float | None
    historical_rain: float | None
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
                hour=self.daily_update_hour,
                minute=0,
                second=0,
            )
        ]

    async def async_shutdown(self) -> None:
        """Stop scheduled refreshes."""
        for unsub in self._unsub_refresh_times:
            unsub()
        self._unsub_refresh_times = []

    async def _async_refresh_from_schedule(self, now: datetime) -> None:
        """Refresh when the daily lock starts."""
        await self.async_request_refresh()

    @property
    def config(self) -> dict[str, Any]:
        """Return merged config entry data and options."""
        config = {**self.entry.data, **self.entry.options}
        days_since_fertilizer = _days_since_date(
            config.get(CONF_LAST_FERTILIZED_DATE), dt_util.now()
        )
        if days_since_fertilizer is not None:
            config[CONF_DAYS_SINCE_FERTILIZER] = days_since_fertilizer
        return config

    @property
    def daily_update_hour(self) -> int:
        """Return the configured daily update hour."""
        return _valid_hour(
            self.config.get(CONF_DAILY_UPDATE_HOUR), DEFAULT_DAILY_UPDATE_HOUR
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Update all calculated advice."""
        forecast = await self._async_get_forecast()
        weather_data = self._read_weather_data(forecast)
        weather_data, history_saved = self._update_weather_history(weather_data)
        language = getattr(self.hass.config, "language", "en")
        advice = build_advice(self.config, weather_data, language)
        advice, should_save = self._apply_locked_values(advice)
        should_save = should_save or history_saved
        if should_save:
            await self._store.async_save(self._stored_data)
        return advice

    def _apply_locked_values(
        self, advice: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Apply daily locks to selected advice values."""
        now = dt_util.now()
        date_key = now.date().isoformat()
        lock_hour = self.daily_update_hour
        should_save = False

        advice, saved = self._apply_should_mow_lock(
            advice, now, date_key, lock_hour
        )
        should_save = should_save or saved

        return advice, should_save

    def _apply_should_mow_lock(
        self, advice: dict[str, Any], now: datetime, date_key: str, lock_hour: int
    ) -> tuple[dict[str, Any], bool]:
        """Lock the mowing decision once per day from the configured hour."""
        lock = self._stored_data.get("should_mow")
        should_save = False

        if now.hour >= lock_hour and (
            not lock or lock.get("date") != date_key
            or lock.get("lock_hour") != lock_hour
        ):
            lock = {
                "date": date_key,
                "lock_hour": lock_hour,
                "lock_time": _iso_at(now, lock_hour),
                "locked_at": now.isoformat(),
                "data": deepcopy(advice["should_mow"]),
            }
            self._stored_data["should_mow"] = lock
            should_save = True

        if lock:
            live = deepcopy(advice["should_mow"])
            locked = deepcopy(lock["data"])
            locked["attributes"] = {
                **locked.get("attributes", {}),
                "locked": True,
                "lock_time": lock.get("lock_time", lock["locked_at"]),
                "next_update": _next_lock_start(now, lock_hour).isoformat(),
                "live_value": live["value"],
            }
            advice["should_mow"] = locked
        else:
            advice["should_mow"]["attributes"] = {
                **advice["should_mow"].get("attributes", {}),
                "locked": False,
                "next_update": _next_lock_start(now, lock_hour).isoformat(),
            }

        return advice, should_save

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
        recent_rain = self._read_observed_rain(weather_attrs)

        return LawnWeatherData(
            weather_state=weather_state.state if weather_state else None,
            temperature=temperature
            if temperature is not None
            else _as_float(weather_attrs.get("temperature")),
            humidity=humidity
            if humidity is not None
            else _as_float(weather_attrs.get("humidity")),
            recent_rain=recent_rain,
            soil_moisture=self._read_float_sensor(CONF_SOIL_MOISTURE_SENSOR),
            forecast_rain=_forecast_precipitation(forecast),
            forecast_rain_5_days=_forecast_precipitation_5_days(forecast),
            forecast_condition=first_forecast.get("condition"),
            historical_temperature=None,
            historical_humidity=None,
            historical_rain=None,
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

    def _read_observed_rain(self, weather_attrs: dict[str, Any]) -> float | None:
        """Read observed rain, preferring a real rain sensor over weather attributes."""
        rain_sensor = self._read_float_sensor(CONF_RAIN_SENSOR)
        if rain_sensor is not None:
            return rain_sensor

        for key in (
            "precipitation",
            "precipitation_today",
            "rain",
            "rainfall",
        ):
            rain = _as_float(weather_attrs.get(key))
            if rain is not None:
                return rain

        return None

    def _update_weather_history(
        self, weather_data: LawnWeatherData
    ) -> tuple[LawnWeatherData, bool]:
        """Store recent inputs and add a simple 24-hour history summary."""
        now = dt_util.now()
        history = self._stored_data.get("weather_history", [])
        cutoff_keep = now - timedelta(hours=WEATHER_HISTORY_KEEP_HOURS)

        history = [
            item
            for item in history
            if _parse_datetime(item.get("time")) is not None
            and _parse_datetime(item["time"]) >= cutoff_keep
        ]
        history.append(
            {
                "time": now.isoformat(),
                "temperature": weather_data.temperature,
                "humidity": weather_data.humidity,
                "rain": weather_data.recent_rain,
            }
        )
        self._stored_data["weather_history"] = history

        cutoff_weather_summary = now - timedelta(hours=WEATHER_HISTORY_HOURS)
        recent_weather_items = [
            item
            for item in history
            if _parse_datetime(item.get("time")) is not None
            and _parse_datetime(item["time"]) >= cutoff_weather_summary
        ]
        cutoff_rain_summary = now - timedelta(hours=RAIN_HISTORY_HOURS)
        recent_rain_items = [
            item
            for item in history
            if _parse_datetime(item.get("time")) is not None
            and _parse_datetime(item["time"]) >= cutoff_rain_summary
        ]

        return (
            replace(
                weather_data,
                historical_temperature=_average(
                    item.get("temperature") for item in recent_weather_items
                ),
                historical_humidity=_average(
                    item.get("humidity") for item in recent_weather_items
                ),
                historical_rain=_rain_total_by_day(recent_rain_items),
            ),
            True,
        )


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
    return _sum_forecast_rain(forecast, forecast_window)


def _forecast_precipitation_5_days(forecast: list[dict[str, Any]]) -> float | None:
    """Estimate forecast precipitation for the next five days when available."""
    if not forecast:
        return None

    return _sum_forecast_rain(forecast, _forecast_window_size(forecast))


def _sum_forecast_rain(
    forecast: list[dict[str, Any]], forecast_window: int
) -> float | None:
    """Sum forecast precipitation over the requested number of entries."""
    total = 0.0
    found = False
    for item in forecast[:forecast_window]:
        value = item.get("precipitation")
        rain = _as_float(value)
        if rain is not None:
            total += rain
            found = True

    return round(total, 1) if found else None


def _forecast_window_size(forecast: list[dict[str, Any]]) -> int:
    """Return entries covering roughly five forecast days."""
    if len(forecast) <= 5:
        return len(forecast)

    first = _parse_datetime(forecast[0].get("datetime"))
    second = _parse_datetime(forecast[1].get("datetime"))
    if first is not None and second is not None:
        interval = abs(second - first)
        if interval <= timedelta(hours=2):
            return min(len(forecast), 24 * 5)
        return min(len(forecast), 5)

    if len(forecast) > 8:
        return min(len(forecast), 24 * 5)
    return min(len(forecast), 5)


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO datetime from storage."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _average(values: Any) -> float | None:
    """Return the average of numeric values."""
    numbers = [_as_float(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 1)


def _rain_total_by_day(items: list[dict[str, Any]]) -> float | None:
    """Sum daily rain maximums to avoid double-counting repeated sensor updates."""
    daily_max: dict[str, float] = {}
    for item in items:
        item_time = _parse_datetime(item.get("time"))
        rain = _as_float(item.get("rain"))
        if item_time is None or rain is None:
            continue

        day = item_time.date().isoformat()
        daily_max[day] = max(daily_max.get(day, 0.0), rain)

    if not daily_max:
        return None
    return round(sum(daily_max.values()), 1)


def _days_since_date(value: Any, now: datetime) -> int | None:
    """Return days since a YYYY-MM-DD date."""
    if not isinstance(value, str) or not value:
        return None

    try:
        fertilized_date = datetime.fromisoformat(value).date()
    except ValueError:
        return None

    return max(0, (now.date() - fertilized_date).days)


def _iso_at(now: datetime, hour: int) -> str:
    """Return an ISO timestamp for today at the given hour."""
    return now.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _next_lock_start(now: datetime, hour: int) -> datetime:
    """Return the next local lock time."""
    next_start = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now >= next_start:
        next_start += timedelta(days=1)
    return next_start


def _valid_hour(value: Any, default: int) -> int:
    """Return a whole hour between 0 and 23."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return default

    if not float(value).is_integer():
        return default

    hour = int(value)
    if 0 <= hour <= 23:
        return hour
    return default
