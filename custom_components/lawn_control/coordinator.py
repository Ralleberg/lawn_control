"""Data coordinator for Lawn Control."""

from __future__ import annotations

import logging
import math
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DAILY_UPDATE_HOUR,
    CONF_DAYS_SINCE_FERTILIZER,
    CONF_FORECAST_RAIN_DAYS,
    CONF_HISTORICAL_RAIN_DAYS,
    CONF_HUMIDITY_SENSOR,
    CONF_LAST_FERTILIZED_DATE,
    CONF_MOWING_UPDATE_FREQUENCY,
    CONF_RAIN_SENSOR,
    CONF_ROBOTIC_MOWER,
    CONF_ROBOT_MOWER_ALLOW_NIGHT,
    CONF_ROBOT_MOWER_ENTITY,
    CONF_SOIL_MOISTURE_SENSOR,
    CONF_TEMPERATURE_SENSOR,
    CONF_WEATHER_ENTITY,
    DEFAULT_DAILY_UPDATE_HOUR,
    DEFAULT_FORECAST_RAIN_DAYS,
    DEFAULT_HISTORICAL_RAIN_DAYS,
    DEFAULT_MOWING_UPDATE_FREQUENCY,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    FERTILIZER_PERCENT_CONFIGS,
    MOWING_UPDATE_FREQUENCIES,
)
from .rules.care import build_advice, general_recommendation

LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
WEATHER_HISTORY_HOURS = 24
MAX_WEATHER_HISTORY_KEEP_DAYS = 10
GROWTH_HISTORY_KEEP_DAYS = 21
MOWING_LOCK_VERSION = 5
ROBOT_CATCH_UP_TRIGGER_MM = 3.0
ROBOT_CATCH_UP_COOLDOWN = timedelta(hours=48)
ROBOT_MOWER_MOWING_STATE = "mowing"
ROBOT_MOWER_FINISHED_STATES = {"docked", "returning"}
ROBOT_MOWER_ERROR_STATE = "error"
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
    forecast_rain_next_24h: float | None
    forecast_drying_pressure_72h: float | None
    forecast_condition: str | None
    historical_temperature: float | None
    historical_humidity: float | None
    historical_rain: float | None
    historical_rain_24h: float | None
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
        if self._migrate_editable_settings():
            await self._store.async_save(self._stored_data)
        self._unsub_refresh_times = [
            async_track_time_change(
                self.hass,
                self._async_refresh_from_period_boundary,
                minute=0,
                second=0,
            ),
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

    async def _async_refresh_from_period_boundary(self, now: datetime) -> None:
        """Refresh when a mowing update period can start."""
        await self.async_request_refresh()

    @property
    def daily_update_hour(self) -> int:
        """Return the configured daily mowing update hour."""
        return _valid_hour(
            self.config.get(CONF_DAILY_UPDATE_HOUR),
            DEFAULT_DAILY_UPDATE_HOUR,
        )

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
        for key in FERTILIZER_PERCENT_CONFIGS:
            config[key] = self._fertilizer_percent_value(key, config)
        last_fertilized_date = self._last_fertilized_date_string(config)
        if last_fertilized_date is not None:
            config[CONF_LAST_FERTILIZED_DATE] = last_fertilized_date
        days_since_fertilizer = _days_since_date(
            last_fertilized_date, dt_util.now()
        )
        if days_since_fertilizer is not None:
            config[CONF_DAYS_SINCE_FERTILIZER] = days_since_fertilizer
        return config

    @property
    def last_fertilized_date(self) -> date | None:
        """Return the latest fertilizer date for the date entity."""
        value = self._last_fertilized_date_string(
            {**self.entry.data, **self.entry.options}
        )
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    async def async_set_last_fertilized_date(self, value: date) -> None:
        """Persist a new latest fertilizer date from the date entity."""
        self._stored_data[CONF_LAST_FERTILIZED_DATE] = value.isoformat()
        await self._store.async_save(self._stored_data)
        await self.async_request_refresh()

    def fertilizer_percent(self, key: str) -> float:
        """Return a fertilizer percentage for a number entity."""
        return self._fertilizer_percent_value(
            key, {**self.entry.data, **self.entry.options}
        )

    async def async_set_fertilizer_percent(self, key: str, value: float) -> None:
        """Persist a fertilizer percentage from a number entity."""
        percent = _coerce_fertilizer_percent(value)
        if percent is None:
            raise ValueError(f"Invalid fertilizer percentage: {value}")

        self._stored_data[key] = percent
        await self._store.async_save(self._stored_data)
        await self.async_request_refresh()

    def _last_fertilized_date_string(self, config: dict[str, Any]) -> str | None:
        """Return stored fertilizer date, falling back to legacy config values."""
        stored_value = self._stored_data.get(CONF_LAST_FERTILIZED_DATE)
        if _is_valid_date(stored_value):
            return str(stored_value)

        config_value = config.get(CONF_LAST_FERTILIZED_DATE)
        if _is_valid_date(config_value):
            return str(config_value)

        return None

    def _migrate_last_fertilized_date(self) -> bool:
        """Copy legacy config/options fertilizer date into editable storage."""
        stored_value = self._stored_data.get(CONF_LAST_FERTILIZED_DATE)
        if _is_valid_date(stored_value):
            return False

        config = {**self.entry.data, **self.entry.options}
        config_value = config.get(CONF_LAST_FERTILIZED_DATE)
        if not _is_valid_date(config_value):
            return False

        self._stored_data[CONF_LAST_FERTILIZED_DATE] = str(config_value)
        return True

    def _migrate_fertilizer_percentages(self) -> bool:
        """Copy legacy config/options fertilizer percentages into editable storage."""
        changed = False
        config = {**self.entry.data, **self.entry.options}
        for key in FERTILIZER_PERCENT_CONFIGS:
            if _coerce_fertilizer_percent(self._stored_data.get(key)) is not None:
                continue

            percent = _coerce_fertilizer_percent(config.get(key))
            if percent is None:
                continue

            self._stored_data[key] = percent
            changed = True
        return changed

    def _migrate_editable_settings(self) -> bool:
        """Copy legacy config/options editable fields into integration storage."""
        date_changed = self._migrate_last_fertilized_date()
        percentages_changed = self._migrate_fertilizer_percentages()
        return date_changed or percentages_changed

    def _fertilizer_percent_value(self, key: str, config: dict[str, Any]) -> float:
        """Return stored fertilizer percentage, falling back to legacy config."""
        stored_value = _coerce_fertilizer_percent(self._stored_data.get(key))
        if stored_value is not None:
            return stored_value

        config_value = _coerce_fertilizer_percent(config.get(key))
        if config_value is not None:
            return config_value

        return 0.0

    async def _async_update_data(self) -> dict[str, Any]:
        """Update all calculated advice."""
        forecasts = await self._async_get_forecasts()
        weather_data = self._read_weather_data(forecasts)
        weather_data, history_saved = self._update_weather_history(weather_data)
        language = getattr(self.hass.config, "language", "en")
        advice = build_advice(self.config, weather_data, language)
        advice["availability"] = _availability_data(weather_data)
        mower_saved = self._update_robot_mower_tracking(advice)
        growth_saved = self._update_growth_history(advice)
        mower_status = self._robot_mower_status_data(advice)
        self._apply_robot_mower_status_attributes(advice, mower_status)
        self._apply_robot_mower_catch_up(
            advice,
            weather_data,
            language,
            mower_status,
        )
        advice, should_save = self._apply_mowing_frequency_lock(advice)
        self._apply_mowing_time_window(advice, weather_data, language)
        self._refresh_care_recommendation(advice, language)
        should_save = should_save or history_saved or mower_saved or growth_saved
        if should_save:
            await self._store.async_save(self._stored_data)
        return advice

    def _update_robot_mower_tracking(self, advice: dict[str, Any]) -> bool:
        """Track completed robot mower runs from the configured mower entity."""
        entity_id = self.config.get(CONF_ROBOT_MOWER_ENTITY)
        if not entity_id:
            return False

        state = self.hass.states.get(entity_id)
        if not _state_is_available(state):
            return False

        now = dt_util.now()
        tracker = self._stored_data.get("robot_mower")
        if not isinstance(tracker, dict) or tracker.get("entity_id") != entity_id:
            tracker = {"entity_id": entity_id}

        original_tracker = deepcopy(tracker)
        previous_state = tracker.get("state")
        current_state = state.state
        tracker["state"] = current_state

        if (
            current_state == ROBOT_MOWER_MOWING_STATE
            and previous_state != ROBOT_MOWER_MOWING_STATE
        ):
            tracker["mowing_started_at"] = now.isoformat()

        if (
            previous_state == ROBOT_MOWER_MOWING_STATE
            and current_state in ROBOT_MOWER_FINISHED_STATES
        ):
            tracker["last_mowed_at"] = now.isoformat()
            tracker["last_mowed_height"] = advice["recommended_grass_height"]["value"]
            tracker.pop("mowing_started_at", None)

        if tracker != original_tracker:
            self._stored_data["robot_mower"] = tracker
            return True

        return False

    def _update_growth_history(self, advice: dict[str, Any]) -> bool:
        """Store hourly grass growth estimates for later mower catch-up decisions."""
        availability = advice.get("availability", {})
        if not availability.get("source_entities_available", True):
            return False

        growth_rate = _as_float(
            advice.get("growth_rate", {})
            .get("attributes", {})
            .get("estimated_mm_per_day")
        )
        if growth_rate is None:
            return False

        now = dt_util.now()
        sample_time = now.replace(minute=0, second=0, microsecond=0)
        original_history = self._stored_data.get("growth_history", [])
        if not isinstance(original_history, list):
            original_history = []

        cutoff = now - timedelta(days=GROWTH_HISTORY_KEEP_DAYS)
        history = [
            item
            for item in original_history
            if _parse_datetime(item.get("time")) is not None
            and _parse_datetime(item["time"]) >= cutoff
        ]
        sample = {"time": sample_time.isoformat(), "growth": growth_rate}
        if history and history[-1].get("time") == sample["time"]:
            history[-1] = sample
        else:
            history.append(sample)

        if history == original_history:
            return False

        self._stored_data["growth_history"] = history
        return True

    def _apply_robot_mower_catch_up(
        self,
        advice: dict[str, Any],
        weather_data: LawnWeatherData,
        language: str,
        mower_status: dict[str, Any],
    ) -> None:
        """Allow occasional robot catch-up mowing when grass is getting too high."""
        if not self.config.get(CONF_ROBOTIC_MOWER):
            return

        robot_advice = advice["robot_mower_should_run"]
        if robot_advice.get("value") is not False:
            return

        availability = advice.get("availability", {})
        if not availability.get("source_entities_available", True):
            return

        if self._robot_catch_up_hard_blocked(advice, weather_data):
            return

        now = dt_util.now()
        target_height = _as_float(advice["recommended_grass_height"]["value"])
        last_mowed_at = _parse_datetime(mower_status.get("last_registered_run"))
        estimated_height = _as_float(mower_status.get("estimated_grass_height"))
        if (
            last_mowed_at is None
            or target_height is None
            or estimated_height is None
            or now - last_mowed_at < ROBOT_CATCH_UP_COOLDOWN
        ):
            return

        if estimated_height < target_height + ROBOT_CATCH_UP_TRIGGER_MM:
            return

        attributes = robot_advice.setdefault("attributes", {})
        attributes["blocking_factors"] = []
        attributes["catch_up"] = True
        attributes["reason"] = _robot_catch_up_text(language)
        robot_advice["value"] = True

    def _robot_mower_status_data(self, advice: dict[str, Any]) -> dict[str, Any]:
        """Return tracked robot mower data for entity attributes and catch-up."""
        status: dict[str, Any] = {"catch_up": False}
        tracker = self._stored_data.get("robot_mower")
        if not isinstance(tracker, dict):
            return status

        last_mowed_at = _parse_datetime(tracker.get("last_mowed_at"))
        last_mowed_height = _as_float(tracker.get("last_mowed_height"))
        if last_mowed_at is None or last_mowed_height is None:
            return status

        status["last_registered_run"] = last_mowed_at.isoformat()
        growth_since_mowed = _growth_total_since(
            self._stored_data.get("growth_history", []),
            last_mowed_at,
            dt_util.now(),
        )
        if growth_since_mowed is not None:
            status["estimated_grass_height"] = round(
                last_mowed_height + growth_since_mowed,
                1,
            )

        return status

    def _apply_robot_mower_status_attributes(
        self,
        advice: dict[str, Any],
        mower_status: dict[str, Any],
    ) -> None:
        """Expose concise robot mower tracking details on robot mower advice."""
        attributes = advice["robot_mower_should_run"].setdefault("attributes", {})
        attributes["catch_up"] = bool(mower_status.get("catch_up", False))

        for key in ("last_registered_run", "estimated_grass_height"):
            value = mower_status.get(key)
            if value is not None:
                attributes[key] = value

    def _robot_catch_up_hard_blocked(
        self,
        advice: dict[str, Any],
        weather_data: LawnWeatherData,
    ) -> bool:
        """Return whether catch-up mowing must remain blocked."""
        entity_id = self.config.get(CONF_ROBOT_MOWER_ENTITY)
        if entity_id:
            state = self.hass.states.get(entity_id)
            if not _state_is_available(state) or state.state == ROBOT_MOWER_ERROR_STATE:
                return True

        if (
            not self.config.get(CONF_ROBOT_MOWER_ALLOW_NIGHT, True)
            and weather_data.sun_is_up is not True
        ):
            return True

        if weather_data.weather_state in ("rainy", "pouring", "lightning-rainy", "hail"):
            return True

        if (
            weather_data.recent_hour_rain is not None
            and weather_data.recent_hour_rain >= 5
        ):
            return True

        if advice["drought_risk"]["value"] in ("high", "critical"):
            return True

        temperature = _first_known_float(
            weather_data.temperature,
            weather_data.historical_temperature,
        )
        return temperature is not None and temperature < 6

    def _apply_mowing_frequency_lock(
        self,
        advice: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Keep mowing advice stable for the configured update frequency."""
        availability = advice.get("availability", {})
        if not availability.get("source_entities_available", True):
            return advice, False

        now = dt_util.now()
        frequency = _mowing_update_frequency(self.config.get(CONF_MOWING_UPDATE_FREQUENCY))
        period_start = _mowing_period_start(now, frequency, self.daily_update_hour)
        next_update = _next_mowing_period_start(period_start, frequency)
        period_key = period_start.isoformat()
        lock = self._stored_data.get("should_mow")
        should_save = False

        if (
            not isinstance(lock, dict)
            or lock.get("version") != MOWING_LOCK_VERSION
            or lock.get("frequency") != frequency
            or lock.get("period_start") != period_key
        ):
            lock = {
                "version": MOWING_LOCK_VERSION,
                "frequency": frequency,
                "period_start": period_key,
                "next_update": next_update.isoformat(),
                "locked_at": now.isoformat(),
                "data": deepcopy(advice["should_mow"]),
            }
            self._stored_data["should_mow"] = lock
            should_save = True

        live = deepcopy(advice["should_mow"])
        locked = deepcopy(lock["data"])
        locked["attributes"] = {
            **locked.get("attributes", {}),
            "locked": True,
            "mowing_update_frequency": frequency,
            "lock_time": lock.get("period_start", lock["locked_at"]),
            "next_update": lock.get("next_update", next_update.isoformat()),
            "live_value": live["value"],
        }
        advice["should_mow"] = locked

        return advice, should_save

    def _refresh_care_recommendation(
        self,
        advice: dict[str, Any],
        language: str,
    ) -> None:
        """Keep the summary aligned with the effective mowing recommendation."""
        robotic_mower = bool(self.config.get(CONF_ROBOTIC_MOWER))
        active_mowing = (
            advice["robot_mower_should_run"] if robotic_mower else advice["should_mow"]
        )
        advice["care_recommendation"] = general_recommendation(
            advice["drought_risk"],
            advice["fertilizer_score"],
            active_mowing,
            advice["growth_rate"],
            language,
            robotic_mower,
        )

    def _apply_mowing_time_window(
        self,
        advice: dict[str, Any],
        weather_data: LawnWeatherData,
        language: str,
    ) -> None:
        """Prevent mowing before the configured start time when night mowing is off."""
        if self.config.get(CONF_ROBOT_MOWER_ALLOW_NIGHT, True):
            return

        now = dt_util.now()
        daily_hour = self.daily_update_hour
        text = _mowing_time_texts(language)

        if weather_data.sun_is_up is None:
            self._block_mowing_advice(
                advice["should_mow"],
                text["sun_unavailable"],
                None,
                daily_hour,
            )
            return

        if weather_data.sun_is_up is False:
            self._block_mowing_advice(
                advice["should_mow"],
                text["night_block"],
                False,
                daily_hour,
            )
            return

        if now.hour < daily_hour:
            reason = text["start_hour_block"].format(hour=daily_hour)
            self._block_mowing_advice(advice["should_mow"], reason, False, daily_hour)
            self._block_mowing_advice(
                advice["robot_mower_should_run"],
                reason,
                False,
                daily_hour,
            )
            return

        self._mark_mowing_time_window_open(advice["should_mow"], daily_hour)
        self._mark_mowing_time_window_open(
            advice["robot_mower_should_run"],
            daily_hour,
        )

    def _block_mowing_advice(
        self,
        mowing_advice: dict[str, Any],
        reason: str,
        value: bool | None,
        daily_hour: int,
    ) -> None:
        """Block a mowing advice payload with shared time window attributes."""
        attributes = mowing_advice.setdefault("attributes", {})
        blocking_factors = list(attributes.get("blocking_factors", []))
        if reason not in blocking_factors:
            blocking_factors.append(reason)
        attributes["blocking_factors"] = blocking_factors
        attributes["mowing_time_window_open"] = False
        attributes["mowing_allowed_from_hour"] = daily_hour
        attributes["reason"] = " ".join(blocking_factors)
        if "catch_up" in attributes:
            attributes["catch_up"] = False
        mowing_advice["value"] = value

    def _mark_mowing_time_window_open(
        self,
        mowing_advice: dict[str, Any],
        daily_hour: int,
    ) -> None:
        """Mark a mowing advice payload as inside the allowed time window."""
        attributes = mowing_advice.setdefault("attributes", {})
        attributes["mowing_time_window_open"] = True
        attributes["mowing_allowed_from_hour"] = daily_hour

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
        now = dt_util.now()
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
        mower_entity_id = config.get(CONF_ROBOT_MOWER_ENTITY)
        mower_state = self.hass.states.get(mower_entity_id) if mower_entity_id else None
        if mower_entity_id and not _state_is_available(mower_state):
            unavailable_optional_entities.append(mower_entity_id)

        hourly_forecast = forecasts.get("hourly", [])
        daily_forecast = forecasts.get("daily", [])
        legacy_forecast = weather_attrs.get("forecast") or []
        short_forecast = hourly_forecast or daily_forecast or legacy_forecast
        rain_forecast = daily_forecast or hourly_forecast or legacy_forecast
        drying_forecast = daily_forecast or hourly_forecast or legacy_forecast
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
            forecast_rain_next_24h=_forecast_precipitation_next_hours(
                short_forecast,
                24,
                now,
            ),
            forecast_drying_pressure_72h=_forecast_drying_pressure(
                drying_forecast,
                72,
                now,
            ),
            forecast_condition=first_forecast.get("condition"),
            historical_temperature=None,
            historical_humidity=None,
            historical_rain=None,
            historical_rain_24h=None,
            sun_is_up=_sun_is_up(sun_state.state if sun_state else None),
            unavailable_required_entities=tuple(
                dict.fromkeys(unavailable_required_entities)
            ),
            unavailable_optional_entities=tuple(
                dict.fromkeys(unavailable_optional_entities)
            ),
            month=now.month,
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
        return (
            replace(
                weather_data,
                historical_temperature=_average(
                    item.get("temperature") for item in recent_weather_items
                ),
                historical_humidity=_average(
                    item.get("humidity") for item in recent_weather_items
                ),
                historical_rain=_rain_total_since(
                    history,
                    now - timedelta(hours=history_days * 24),
                ),
                historical_rain_24h=_rain_total_since(
                    history,
                    now - timedelta(hours=24),
                ),
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
        config.get(CONF_ROBOT_MOWER_ENTITY),
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


def _mowing_update_frequency(value: Any) -> str:
    """Return a valid mowing update frequency."""
    if value in MOWING_UPDATE_FREQUENCIES:
        return str(value)
    return DEFAULT_MOWING_UPDATE_FREQUENCY


def _mowing_period_start(now: datetime, frequency: str, daily_hour: int) -> datetime:
    """Return the start of the current mowing update period."""
    anchor = now.replace(hour=daily_hour, minute=0, second=0, microsecond=0)
    if frequency == "daily":
        if now < anchor:
            return anchor - timedelta(days=1)
        return anchor

    period_hours = _mowing_period_hours(frequency)
    seconds_since_anchor = (now - anchor).total_seconds()
    periods_since_anchor = math.floor(seconds_since_anchor / (period_hours * 3600))
    return anchor + timedelta(hours=periods_since_anchor * period_hours)


def _next_mowing_period_start(period_start: datetime, frequency: str) -> datetime:
    """Return when the current mowing update period ends."""
    if frequency == "daily":
        return period_start + timedelta(days=1)
    return period_start + timedelta(hours=_mowing_period_hours(frequency))


def _mowing_period_hours(frequency: str) -> int:
    """Return the number of hours in a non-daily mowing update period."""
    if frequency == "4_hours":
        return 4
    if frequency == "6_hours":
        return 6
    return 1


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


def _mowing_time_texts(language: str) -> dict[str, str]:
    """Return localized mowing time window text."""
    if language.lower().startswith("da"):
        return {
            "night_block": "Klipning om natten er slået fra.",
            "start_hour_block": "Klipning er først tilladt fra kl. {hour:02d}:00.",
            "sun_unavailable": "Solens status er utilgængelig, så klippetidsvinduet kan ikke vurderes.",
        }

    return {
        "night_block": "Mowing at night is disabled.",
        "start_hour_block": "Mowing is only allowed from {hour:02d}:00.",
        "sun_unavailable": "Sun status is unavailable, so the mowing time window cannot be evaluated.",
    }


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


def _forecast_drying_pressure(
    forecast: list[dict[str, Any]], hours: int, now: datetime
) -> float | None:
    """Return forecast drying pressure measured as sunny-day equivalents."""
    if not forecast:
        return None

    interval_hours = _forecast_interval_hours(forecast)
    total = 0.0
    found = False

    for item, overlap_hours in _forecast_periods(
        forecast,
        hours,
        now,
        interval_hours,
    ):
        condition = item.get("condition")
        temperature = _as_float(item.get("temperature"))
        humidity = _as_float(item.get("humidity"))
        rain = _probability_weighted_rain(item)
        if condition is None and temperature is None and rain is None:
            continue

        condition_factor = _drying_condition_factor(condition)
        temperature_factor = _drying_temperature_factor(temperature)
        humidity_factor = _drying_humidity_factor(humidity)
        rain_factor = _drying_rain_factor(rain, interval_hours)
        total += (
            condition_factor
            * temperature_factor
            * humidity_factor
            * rain_factor
            * overlap_hours
            / 24
        )
        found = True

    return round(total, 2) if found else None


def _forecast_precipitation_next_hours(
    forecast: list[dict[str, Any]], hours: int, now: datetime
) -> float | None:
    """Return probability-weighted rain from periods overlapping the horizon."""
    if not forecast:
        return None

    interval_hours = _forecast_interval_hours(forecast)
    total = 0.0
    found = False
    for item, _overlap_hours in _forecast_periods(
        forecast,
        hours,
        now,
        interval_hours,
    ):
        rain = _probability_weighted_rain(item)
        if rain is not None:
            total += rain
            found = True

    return round(total, 1) if found else None


def _forecast_periods(
    forecast: list[dict[str, Any]],
    hours: int,
    now: datetime,
    interval_hours: float,
) -> list[tuple[dict[str, Any], float]]:
    """Return forecast items and their overlap with a future time horizon."""
    horizon_end = now + timedelta(hours=hours)
    periods: list[tuple[dict[str, Any], float]] = []

    for index, item in enumerate(forecast):
        period_start = _parse_datetime(item.get("datetime"))
        if period_start is None:
            period_start = now + timedelta(hours=index * interval_hours)
        period_end = period_start + timedelta(hours=interval_hours)

        try:
            overlap_start = max(now, period_start)
            overlap_end = min(horizon_end, period_end)
        except TypeError:
            period_start = now + timedelta(hours=index * interval_hours)
            period_end = period_start + timedelta(hours=interval_hours)
            overlap_start = period_start
            overlap_end = min(horizon_end, period_end)

        if overlap_end <= overlap_start:
            continue
        overlap_hours = (overlap_end - overlap_start).total_seconds() / 3600
        periods.append((item, overlap_hours))

    return periods


def _forecast_interval_hours(forecast: list[dict[str, Any]]) -> float:
    """Return the approximate number of hours represented by each forecast item."""
    if len(forecast) > 1:
        first = _parse_datetime(forecast[0].get("datetime"))
        second = _parse_datetime(forecast[1].get("datetime"))
        if first is not None and second is not None:
            hours = abs((second - first).total_seconds()) / 3600
            if hours > 0:
                return min(24.0, hours)
    return 1.0 if len(forecast) > 8 else 24.0


def _probability_weighted_rain(item: dict[str, Any]) -> float | None:
    """Return expected rain after precipitation probability is considered."""
    rain = _as_float(item.get("precipitation"))
    if rain is None:
        return None

    probability = _as_float(item.get("precipitation_probability"))
    if probability is None:
        return rain
    return rain * max(0.0, min(100.0, probability)) / 100


def _drying_condition_factor(condition: Any) -> float:
    """Return relative drying strength for a weather condition."""
    if condition == "sunny":
        return 1.0
    if condition == "partlycloudy":
        return 0.65
    if condition in ("cloudy", "fog"):
        return 0.2
    if condition == "clear-night":
        return 0.05
    if condition in ("rainy", "pouring", "lightning-rainy", "snowy", "hail"):
        return 0.0
    return 0.35


def _drying_temperature_factor(temperature: float | None) -> float:
    """Return relative drying strength for forecast temperature."""
    if temperature is None:
        return 1.0
    if temperature >= 28:
        return 1.45
    if temperature >= 24:
        return 1.2
    if temperature >= 18:
        return 1.0
    if temperature >= 12:
        return 0.7
    return 0.4


def _drying_humidity_factor(humidity: float | None) -> float:
    """Return relative drying strength for forecast humidity."""
    if humidity is None:
        return 1.0
    if humidity < 40:
        return 1.2
    if humidity >= 75:
        return 0.7
    return 1.0


def _drying_rain_factor(rain: float | None, interval_hours: float) -> float:
    """Reduce drying pressure when meaningful rain is expected in the period."""
    if rain is None or rain <= 0:
        return 1.0

    meaningful_rain = 0.5 if interval_hours <= 2 else 5.0
    if rain >= meaningful_rain:
        return 0.1
    return 0.4


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


def _growth_total_since(
    items: list[dict[str, Any]],
    since: datetime,
    now: datetime,
) -> float | None:
    """Return estimated grass growth since a time from hourly growth samples."""
    if not isinstance(items, list):
        return None

    timed_values = sorted(
        [
            (item_time, growth)
            for item in items
            if (item_time := _parse_datetime(item.get("time"))) is not None
            and (growth := _as_float(item.get("growth"))) is not None
        ],
        key=lambda item: item[0],
    )
    if not timed_values:
        return None

    total = 0.0
    previous_time: datetime | None = None
    previous_growth: float | None = None
    found_sample = False

    for item_time, growth in timed_values:
        if previous_time is not None and previous_growth is not None:
            segment_start = max(previous_time, since)
            segment_end = min(item_time, now)
            if segment_end > segment_start:
                total += previous_growth * (
                    segment_end - segment_start
                ).total_seconds() / 86400
                found_sample = True

        previous_time = item_time
        previous_growth = growth

    if previous_time is not None and previous_growth is not None:
        segment_start = max(previous_time, since)
        if now > segment_start:
            total += previous_growth * (now - segment_start).total_seconds() / 86400
            found_sample = True

    return round(total, 1) if found_sample else 0.0


def _first_known_float(*values: float | None) -> float | None:
    """Return the first known numeric value."""
    for value in values:
        if value is not None:
            return value
    return None


def _robot_catch_up_text(language: str) -> str:
    """Return localized robot mower catch-up text."""
    if language.lower().startswith("da"):
        return "Robotklipning tillades som indhentning, fordi den teoretiske græshøjde er blevet for høj."

    return "Robot mowing is allowed as catch-up because the estimated grass height is getting too high."


def _rain_total_since(items: list[dict[str, Any]], since: datetime) -> float | None:
    """Return rain added since a time from a cumulative daily rain sensor.

    The configured rain sensor is expected to report total rain for the current
    day. Some sources reset after midnight with a delay, so calendar-day maximums
    can count yesterday's rain again after midnight. Summing only positive
    changes makes the calculation resilient to delayed resets while keeping a
    rolling hour-based rain history.
    """
    timed_values = sorted(
        [
            (item_time, rain)
            for item in items
            if (item_time := _parse_datetime(item.get("time"))) is not None
            and (rain := _as_float(item.get("rain"))) is not None
        ],
        key=lambda item: item[0],
    )
    if not timed_values:
        return None

    if timed_values[-1][0] <= since:
        return 0.0

    total = 0.0
    counted_by_day: dict[str, float] = {}
    previous_time, previous_rain = timed_values[0]
    found_window_sample = False

    for item_time, rain in timed_values[1:]:
        day = item_time.date().isoformat()
        increase = _rain_increase(
            previous_time,
            previous_rain,
            item_time,
            rain,
            counted_by_day.get(day, 0.0),
        )
        if increase > 0:
            counted_by_day[day] = counted_by_day.get(day, 0.0) + increase
            if item_time > since:
                total += increase

        if item_time > since:
            found_window_sample = True
        previous_time = item_time
        previous_rain = rain

    return round(total, 1) if found_window_sample else 0.0


def _rain_increase(
    previous_time: datetime,
    previous_rain: float,
    item_time: datetime,
    rain: float,
    counted_today: float,
) -> float:
    """Return the rain increment between two cumulative rain samples."""
    if rain >= previous_rain:
        return rain - previous_rain

    # A drop means the daily rain sensor reset. If the reset was delayed past
    # midnight, positive stale increases may already have counted today's rain.
    if item_time.date() == previous_time.date():
        return max(0.0, rain - counted_today)

    return rain


def _days_since_date(value: Any, now: datetime) -> int | None:
    """Return days since a YYYY-MM-DD date."""
    if not _is_valid_date(value):
        return None

    try:
        fertilized_date = datetime.fromisoformat(value).date()
    except ValueError:
        return None

    return max(0, (now.date() - fertilized_date).days)


def _is_valid_date(value: Any) -> bool:
    """Return true if value is YYYY-MM-DD."""
    if not isinstance(value, str) or not value:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _coerce_fertilizer_percent(value: Any) -> float | None:
    """Return a valid fertilizer percentage as a float."""
    if value in (None, "") or isinstance(value, bool):
        return None

    try:
        percent = float(value)
    except (TypeError, ValueError):
        return None

    if not 0 <= percent <= 40:
        return None

    return round(round(percent * 2) / 2, 1)
