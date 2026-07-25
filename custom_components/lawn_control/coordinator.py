"""Data coordinator for Lawn Control."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DAYS_SINCE_FERTILIZER,
    CONF_FORECAST_RAIN_DAYS,
    CONF_HISTORICAL_RAIN_DAYS,
    CONF_HUMIDITY_SENSOR,
    CONF_LAST_FERTILIZED_DATE,
    CONF_RAIN_SENSOR,
    CONF_ROBOT_MOWER_ALLOW_NIGHT,
    CONF_SOIL_MOISTURE_SENSOR,
    CONF_TEMPERATURE_SENSOR,
    CONF_WEATHER_ENTITY,
    DEFAULT_FORECAST_RAIN_DAYS,
    DEFAULT_HISTORICAL_RAIN_DAYS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .rules.care import build_advice

LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
WEATHER_HISTORY_HOURS = 24
MAX_WEATHER_HISTORY_KEEP_DAYS = 10
SUN_ENTITY_ID = "sun.sun"
UNAVAILABLE_STATES = {"unknown", "unavailable"}


@dataclass(slots=True)
class LawnWeatherData:
    """Weather inputs used by the rule engine."""

    weather_state: str | None
    temperature: float | None
    humidity: float | None
    recent_rain: float | None
    recent_hour_rain: float | None
    soil_moisture: float | None
    forecast_rain: float | None
    forecast_rain_5_days: float | None
    forecast_condition: str | None
    historical_temperature: float | None
    historical_humidity: float | None
    historical_rain: float | None
    sun_is_up: bool | None
    unavailable_required_entities: tuple[str, ...]
    unavailable_optional_entities: tuple[str, ...]
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
            async_track_state_change_event(
                self.hass,
                _configured_source_entities(self.config),
                self._async_refresh_from_source_change,
            )
        ]

    async def async_shutdown(self) -> None:
        """Stop scheduled refreshes."""
        for unsub in self._unsub_refresh_times:
            unsub()
        self._unsub_refresh_times = []

    @callback
    def _async_refresh_from_source_change(self, event: Event) -> None:
        """Refresh immediately when a configured source entity changes."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        if (
            old_state is not None
            and old_state.state == new_state.state
            and old_state.attributes == new_state.attributes
        ):
            return
        self.hass.async_create_task(self.async_request_refresh())

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

    async def _async_update_data(self) -> dict[str, Any]:
        """Update all calculated advice."""
        forecasts = await self._async_get_forecasts()
        weather_data = self._read_weather_data(forecasts)
        weather_data, history_saved = self._update_weather_history(weather_data)
        language = getattr(self.hass.config, "language", "en")
        advice = build_advice(self.config, weather_data, language)
        advice["availability"] = _availability_data(weather_data)
        should_save = self._remove_legacy_should_mow_lock()
        should_save = should_save or history_saved
        if should_save:
            await self._store.async_save(self._stored_data)
        return advice

    def _remove_legacy_should_mow_lock(self) -> bool:
        """Remove the old daily mowing lock from stored coordinator data."""
        if "should_mow" not in self._stored_data:
            return False
        self._stored_data.pop("should_mow", None)
        return True

    async def _async_get_forecasts(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch forecast data from the configured weather entity."""
        entity_id = self.config[CONF_WEATHER_ENTITY]
        forecasts: dict[str, list[dict[str, Any]]] = {}
        weather_state = self.hass.states.get(entity_id)
        if not _state_is_available(weather_state):
            return forecasts

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
                forecasts[forecast_type] = forecast

        return forecasts

    def _read_weather_data(
        self, forecasts: dict[str, list[dict[str, Any]]]
    ) -> LawnWeatherData:
        """Read weather and optional sensor states from Home Assistant."""
        config = self.config
        unavailable_required_entities: list[str] = []
        unavailable_optional_entities: list[str] = []
        weather_state = self.hass.states.get(config[CONF_WEATHER_ENTITY])
        if _state_is_available(weather_state):
            weather_attrs = weather_state.attributes
            weather_state_value = weather_state.state
        else:
            weather_attrs = {}
            weather_state_value = None
            unavailable_required_entities.append(config[CONF_WEATHER_ENTITY])
        sun_state = self.hass.states.get(SUN_ENTITY_ID)
        if (
            not config.get(CONF_ROBOT_MOWER_ALLOW_NIGHT, True)
            and not _state_is_available(sun_state)
        ):
            unavailable_optional_entities.append(SUN_ENTITY_ID)

        hourly_forecast = forecasts.get("hourly", [])
        daily_forecast = forecasts.get("daily", [])
        legacy_forecast = weather_attrs.get("forecast") or []
        short_forecast = hourly_forecast or daily_forecast or legacy_forecast
        rain_forecast = daily_forecast or hourly_forecast or legacy_forecast
        first_forecast = short_forecast[0] if short_forecast else {}

        temperature = self._read_float_sensor(
            CONF_TEMPERATURE_SENSOR,
            unavailable_optional_entities,
        )
        humidity = self._read_float_sensor(
            CONF_HUMIDITY_SENSOR,
            unavailable_optional_entities,
        )
        recent_rain = self._read_observed_rain(
            weather_attrs,
            unavailable_optional_entities,
        )

        return LawnWeatherData(
            weather_state=weather_state_value,
            temperature=temperature
            if temperature is not None
            else _as_float(weather_attrs.get("temperature")),
            humidity=humidity
            if humidity is not None
            else _as_float(weather_attrs.get("humidity")),
            recent_rain=recent_rain,
            recent_hour_rain=None,
            soil_moisture=self._read_float_sensor(
                CONF_SOIL_MOISTURE_SENSOR,
                unavailable_optional_entities,
            ),
            forecast_rain=_forecast_precipitation(short_forecast),
            forecast_rain_5_days=_forecast_precipitation_days(
                rain_forecast,
                _int_config(
                    config,
                    CONF_FORECAST_RAIN_DAYS,
                    DEFAULT_FORECAST_RAIN_DAYS,
                ),
            ),
            forecast_condition=first_forecast.get("condition"),
            historical_temperature=None,
            historical_humidity=None,
            historical_rain=None,
            sun_is_up=_sun_is_up(sun_state.state if sun_state else None),
            unavailable_required_entities=tuple(
                dict.fromkeys(unavailable_required_entities)
            ),
            unavailable_optional_entities=tuple(
                dict.fromkeys(unavailable_optional_entities)
            ),
            month=datetime.now().month,
        )

    def _read_float_sensor(
        self,
        config_key: str,
        unavailable_entities: list[str],
    ) -> float | None:
        """Read an optional numeric sensor configured by entity id."""
        entity_id = self.config.get(config_key)
        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)
        if not _state_is_available(state):
            unavailable_entities.append(entity_id)
            return None

        value = _as_float(state.state)
        if value is None:
            unavailable_entities.append(entity_id)
        return value

    def _read_observed_rain(
        self,
        weather_attrs: dict[str, Any],
        unavailable_entities: list[str],
    ) -> float | None:
        """Read observed rain, preferring a real rain sensor over weather attributes."""
        rain_sensor = self._read_float_sensor(CONF_RAIN_SENSOR, unavailable_entities)
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
        history_days = _int_config(
            self.config, CONF_HISTORICAL_RAIN_DAYS, DEFAULT_HISTORICAL_RAIN_DAYS
        )
        original_history = self._stored_data.get("weather_history", [])
        if not isinstance(original_history, list):
            original_history = []
        history = original_history
        cutoff_keep = now - timedelta(
            days=max(MAX_WEATHER_HISTORY_KEEP_DAYS, history_days)
        )

        history = [
            item
            for item in history
            if _parse_datetime(item.get("time")) is not None
            and _parse_datetime(item["time"]) >= cutoff_keep
        ]
        should_save = history != original_history
        if _should_store_weather_history(weather_data):
            history.append(
                {
                    "time": now.isoformat(),
                    "temperature": weather_data.temperature,
                    "humidity": weather_data.humidity,
                    "rain": weather_data.recent_rain,
                }
            )
            should_save = True

        if should_save:
            self._stored_data["weather_history"] = history

        cutoff_weather_summary = now - timedelta(hours=WEATHER_HISTORY_HOURS)
        recent_weather_items = [
            item
            for item in history
            if _parse_datetime(item.get("time")) is not None
            and _parse_datetime(item["time"]) >= cutoff_weather_summary
        ]
        cutoff_rain_summary = now - timedelta(days=history_days)
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
                recent_hour_rain=_rain_total_since(history, now - timedelta(hours=1)),
            ),
            should_save,
        )


def _as_float(value: Any) -> float | None:
    """Convert a value to float when possible."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _configured_source_entities(config: dict[str, Any]) -> list[str]:
    """Return source entities that should trigger an immediate refresh."""
    entity_ids = [
        config.get(CONF_WEATHER_ENTITY),
        config.get(CONF_TEMPERATURE_SENSOR),
        config.get(CONF_RAIN_SENSOR),
        config.get(CONF_HUMIDITY_SENSOR),
        config.get(CONF_SOIL_MOISTURE_SENSOR),
        SUN_ENTITY_ID,
    ]
    return list(dict.fromkeys(entity_id for entity_id in entity_ids if entity_id))


def _state_is_available(state: Any) -> bool:
    """Return false for missing, unknown or unavailable Home Assistant states."""
    if state is None:
        return False
    return state.state not in UNAVAILABLE_STATES


def _availability_data(weather_data: LawnWeatherData) -> dict[str, Any]:
    """Return shared source availability details for entity attributes."""
    unavailable_required = list(weather_data.unavailable_required_entities)
    unavailable_optional = list(weather_data.unavailable_optional_entities)
    return {
        "source_entities_available": not unavailable_required,
        "unavailable_source_entities": unavailable_required + unavailable_optional,
        "unavailable_required_entities": unavailable_required,
        "unavailable_optional_entities": unavailable_optional,
    }


def _should_store_weather_history(weather_data: LawnWeatherData) -> bool:
    """Return whether the current sample is valid enough for weather history."""
    if weather_data.unavailable_required_entities:
        return False

    return any(
        value is not None
        for value in (
            weather_data.temperature,
            weather_data.humidity,
            weather_data.recent_rain,
        )
    )


def _sun_is_up(state: str | None) -> bool | None:
    """Return whether the Home Assistant sun entity is above the horizon."""
    if state == "above_horizon":
        return True
    if state == "below_horizon":
        return False
    return None


def _int_config(config: dict[str, Any], key: str, default: int) -> int:
    """Read a whole-number config value."""
    try:
        return int(float(config.get(key, default)))
    except (TypeError, ValueError):
        return default


def _forecast_precipitation(forecast: list[dict[str, Any]]) -> float | None:
    """Estimate near-term forecast precipitation from weather attributes."""
    if not forecast:
        return None

    forecast_window = 24 if len(forecast) > 8 else 3
    return _sum_forecast_rain(forecast, forecast_window)


def _forecast_precipitation_days(
    forecast: list[dict[str, Any]], days: int
) -> float | None:
    """Estimate forecast precipitation for the configured horizon."""
    if not forecast:
        return None

    return _sum_forecast_rain(forecast, _forecast_window_size(forecast, days))


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


def _forecast_window_size(forecast: list[dict[str, Any]], days: int) -> int:
    """Return entries covering roughly the configured forecast days."""
    if len(forecast) <= days:
        return len(forecast)

    first = _parse_datetime(forecast[0].get("datetime"))
    second = _parse_datetime(forecast[1].get("datetime"))
    if first is not None and second is not None:
        interval = abs(second - first)
        if interval <= timedelta(hours=2):
            return min(len(forecast), 24 * days)
        return min(len(forecast), days)

    if len(forecast) > 8:
        return min(len(forecast), 24 * days)
    return min(len(forecast), days)


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


def _rain_total_since(items: list[dict[str, Any]], since: datetime) -> float | None:
    """Return rain added since a time from a daily cumulative rain sensor."""
    timed_values = [
        (item_time, rain)
        for item in items
        if (item_time := _parse_datetime(item.get("time"))) is not None
        and (rain := _as_float(item.get("rain"))) is not None
    ]
    if not timed_values:
        return None

    current_time, current_rain = max(timed_values, key=lambda item: item[0])
    same_day_values = [
        (item_time, rain)
        for item_time, rain in timed_values
        if item_time.date() == current_time.date() and item_time <= current_time
    ]
    previous_values = [
        (item_time, rain) for item_time, rain in same_day_values if item_time <= since
    ]
    if previous_values:
        _, previous_rain = max(previous_values, key=lambda item: item[0])
    elif len(same_day_values) > 1:
        _, previous_rain = min(same_day_values, key=lambda item: item[0])
    else:
        return 0.0

    return round(max(0.0, current_rain - previous_rain), 1)


def _days_since_date(value: Any, now: datetime) -> int | None:
    """Return days since a YYYY-MM-DD date."""
    if not isinstance(value, str) or not value:
        return None

    try:
        fertilized_date = datetime.fromisoformat(value).date()
    except ValueError:
        return None

    return max(0, (now.date() - fertilized_date).days)
