"""Config flow for Lawn Control."""

from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CARE_LEVELS,
    CONF_CARE_LEVEL,
    CONF_DAILY_UPDATE_HOUR,
    CONF_FERTILIZER_K_PERCENT,
    CONF_FERTILIZER_N_PERCENT,
    CONF_FERTILIZER_P_PERCENT,
    CONF_FORECAST_RAIN_DAYS,
    CONF_FORECAST_RAIN_THRESHOLD,
    CONF_HISTORICAL_RAIN_DAYS,
    CONF_HISTORICAL_RAIN_THRESHOLD,
    CONF_HUMIDITY_SENSOR,
    CONF_LAWN_TYPE,
    CONF_LAST_FERTILIZED_DATE,
    CONF_MAX_GRASS_HEIGHT,
    CONF_MIN_GRASS_HEIGHT,
    CONF_MOWING_UPDATE_FREQUENCY,
    CONF_RAIN_SENSOR,
    CONF_ROBOT_MOWER_ALLOW_NIGHT,
    CONF_ROBOTIC_MOWER,
    CONF_SHADE_LEVEL,
    CONF_SOIL_MOISTURE_SENSOR,
    CONF_SOIL_TYPE,
    CONF_TEMPERATURE_SENSOR,
    CONF_WATER_DURING_DROUGHT,
    CONF_WATERING_LEVEL,
    CONF_WEATHER_ENTITY,
    DEFAULT_DAILY_UPDATE_HOUR,
    DEFAULT_FORECAST_RAIN_DAYS,
    DEFAULT_FORECAST_RAIN_THRESHOLD,
    DEFAULT_HISTORICAL_RAIN_DAYS,
    DEFAULT_HISTORICAL_RAIN_THRESHOLD,
    DEFAULT_MAX_GRASS_HEIGHT,
    DEFAULT_MIN_GRASS_HEIGHT,
    DEFAULT_MOWING_UPDATE_FREQUENCY,
    DOMAIN,
    LAWN_TYPES,
    MOWING_UPDATE_FREQUENCIES,
    SHADE_LEVELS,
    SOIL_TYPES,
    WATERING_LEVELS,
)


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the config/options schema."""
    defaults = defaults or {}

    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Lawn")): str,
            vol.Required(
                CONF_WEATHER_ENTITY,
                default=defaults.get(CONF_WEATHER_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
            vol.Optional(
                CONF_TEMPERATURE_SENSOR,
                **_default_kwargs(defaults, CONF_TEMPERATURE_SENSOR),
            ): _sensor_selector(),
            vol.Optional(
                CONF_RAIN_SENSOR,
                **_default_kwargs(defaults, CONF_RAIN_SENSOR),
            ): _sensor_selector(),
            vol.Optional(
                CONF_HUMIDITY_SENSOR,
                **_default_kwargs(defaults, CONF_HUMIDITY_SENSOR),
            ): _sensor_selector(),
            vol.Optional(
                CONF_SOIL_MOISTURE_SENSOR,
                **_default_kwargs(defaults, CONF_SOIL_MOISTURE_SENSOR),
            ): _sensor_selector(),
            vol.Required(
                CONF_LAWN_TYPE,
                default=defaults.get(CONF_LAWN_TYPE, "regular"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=LAWN_TYPES,
                    translation_key=CONF_LAWN_TYPE,
                )
            ),
            vol.Required(
                CONF_ROBOTIC_MOWER,
                default=defaults.get(CONF_ROBOTIC_MOWER, False),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ROBOT_MOWER_ALLOW_NIGHT,
                default=defaults.get(CONF_ROBOT_MOWER_ALLOW_NIGHT, True),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_MOWING_UPDATE_FREQUENCY,
                default=defaults.get(
                    CONF_MOWING_UPDATE_FREQUENCY,
                    DEFAULT_MOWING_UPDATE_FREQUENCY,
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=MOWING_UPDATE_FREQUENCIES,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_MOWING_UPDATE_FREQUENCY,
                )
            ),
            vol.Required(
                CONF_DAILY_UPDATE_HOUR,
                default=defaults.get(
                    CONF_DAILY_UPDATE_HOUR, DEFAULT_DAILY_UPDATE_HOUR
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=23,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="h",
                    translation_key=CONF_DAILY_UPDATE_HOUR,
                )
            ),
            vol.Required(
                CONF_HISTORICAL_RAIN_THRESHOLD,
                default=defaults.get(
                    CONF_HISTORICAL_RAIN_THRESHOLD,
                    DEFAULT_HISTORICAL_RAIN_THRESHOLD,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5,
                    max=15,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="mm",
                )
            ),
            vol.Required(
                CONF_FORECAST_RAIN_THRESHOLD,
                default=defaults.get(
                    CONF_FORECAST_RAIN_THRESHOLD,
                    DEFAULT_FORECAST_RAIN_THRESHOLD,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10,
                    max=25,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="mm",
                )
            ),
            vol.Required(
                CONF_HISTORICAL_RAIN_DAYS,
                default=defaults.get(
                    CONF_HISTORICAL_RAIN_DAYS,
                    DEFAULT_HISTORICAL_RAIN_DAYS,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=3,
                    max=10,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="d",
                )
            ),
            vol.Required(
                CONF_FORECAST_RAIN_DAYS,
                default=defaults.get(
                    CONF_FORECAST_RAIN_DAYS,
                    DEFAULT_FORECAST_RAIN_DAYS,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=2,
                    max=7,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="d",
                )
            ),
            vol.Required(
                CONF_SHADE_LEVEL,
                default=defaults.get(CONF_SHADE_LEVEL, "low"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=SHADE_LEVELS,
                    translation_key=CONF_SHADE_LEVEL,
                )
            ),
            vol.Required(
                CONF_SOIL_TYPE,
                default=defaults.get(CONF_SOIL_TYPE, "normal"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=SOIL_TYPES,
                    translation_key=CONF_SOIL_TYPE,
                )
            ),
            vol.Required(
                CONF_CARE_LEVEL,
                default=defaults.get(CONF_CARE_LEVEL, "normal"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=CARE_LEVELS,
                    translation_key=CONF_CARE_LEVEL,
                )
            ),
            vol.Required(
                CONF_MIN_GRASS_HEIGHT,
                default=defaults.get(
                    CONF_MIN_GRASS_HEIGHT, DEFAULT_MIN_GRASS_HEIGHT
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=15, max=100, step=1, unit_of_measurement="mm"
                )
            ),
            vol.Required(
                CONF_MAX_GRASS_HEIGHT,
                default=defaults.get(
                    CONF_MAX_GRASS_HEIGHT, DEFAULT_MAX_GRASS_HEIGHT
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=20, max=120, step=1, unit_of_measurement="mm"
                )
            ),
            vol.Optional(
                CONF_WATER_DURING_DROUGHT,
                default=defaults.get(CONF_WATER_DURING_DROUGHT, False),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_WATERING_LEVEL,
                default=defaults.get(CONF_WATERING_LEVEL, "normal"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=WATERING_LEVELS,
                    translation_key=CONF_WATERING_LEVEL,
                )
            ),
            vol.Optional(
                CONF_FERTILIZER_N_PERCENT,
                default=defaults.get(CONF_FERTILIZER_N_PERCENT, 0),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=40, step=0.5, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_FERTILIZER_P_PERCENT,
                default=defaults.get(CONF_FERTILIZER_P_PERCENT, 0),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=40, step=0.5, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_FERTILIZER_K_PERCENT,
                default=defaults.get(CONF_FERTILIZER_K_PERCENT, 0),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=40, step=0.5, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_LAST_FERTILIZED_DATE,
                **_default_kwargs(defaults, CONF_LAST_FERTILIZED_DATE),
            ): str,
        }
    )


def _default_kwargs(defaults: dict[str, Any], key: str) -> dict[str, Any]:
    """Return selector default kwargs only when there is a real value."""
    value = defaults.get(key)
    if value in (None, ""):
        return {}
    return {"default": value}


def _sensor_selector() -> selector.EntitySelector:
    """Return a sensor entity selector."""
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


def _clean_user_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Remove empty optional fields and make defaults explicit."""
    cleaned = {
        key: value
        for key, value in user_input.items()
        if value not in (None, "")
    }
    cleaned.setdefault(CONF_ROBOTIC_MOWER, False)
    cleaned.setdefault(CONF_ROBOT_MOWER_ALLOW_NIGHT, True)
    cleaned.setdefault(CONF_MOWING_UPDATE_FREQUENCY, DEFAULT_MOWING_UPDATE_FREQUENCY)
    cleaned.setdefault(CONF_DAILY_UPDATE_HOUR, DEFAULT_DAILY_UPDATE_HOUR)
    cleaned.setdefault(
        CONF_HISTORICAL_RAIN_THRESHOLD, DEFAULT_HISTORICAL_RAIN_THRESHOLD
    )
    cleaned.setdefault(CONF_FORECAST_RAIN_THRESHOLD, DEFAULT_FORECAST_RAIN_THRESHOLD)
    cleaned.setdefault(CONF_HISTORICAL_RAIN_DAYS, DEFAULT_HISTORICAL_RAIN_DAYS)
    cleaned.setdefault(CONF_FORECAST_RAIN_DAYS, DEFAULT_FORECAST_RAIN_DAYS)
    cleaned.setdefault(CONF_WATER_DURING_DROUGHT, False)
    cleaned.setdefault(CONF_WATERING_LEVEL, "normal")
    cleaned.setdefault(CONF_FERTILIZER_N_PERCENT, 0)
    cleaned.setdefault(CONF_FERTILIZER_P_PERCENT, 0)
    cleaned.setdefault(CONF_FERTILIZER_K_PERCENT, 0)
    return cleaned


def _is_valid_date(value: str) -> bool:
    """Return true if value is YYYY-MM-DD."""
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_whole_number_in_range(value: Any, minimum: int, maximum: int) -> bool:
    """Return true if value is a whole number within a range."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    return float(value).is_integer() and minimum <= int(value) <= maximum


def _is_valid_hour(value: Any) -> bool:
    """Return true if value is a whole hour between 0 and 23."""
    return _is_whole_number_in_range(value, 0, 23)


def _is_valid_mowing_update_frequency(value: Any) -> bool:
    """Return true if value is a known mowing update frequency."""
    return value in MOWING_UPDATE_FREQUENCIES


def _rain_setting_errors(user_input: dict[str, Any]) -> dict[str, str]:
    """Return validation errors for rain tuning options."""
    checks = {
        CONF_HISTORICAL_RAIN_THRESHOLD: (5, 15),
        CONF_FORECAST_RAIN_THRESHOLD: (10, 25),
        CONF_HISTORICAL_RAIN_DAYS: (3, 10),
        CONF_FORECAST_RAIN_DAYS: (2, 7),
    }
    return {
        key: "invalid_rain_setting"
        for key, (minimum, maximum) in checks.items()
        if not _is_whole_number_in_range(user_input.get(key), minimum, maximum)
    }


class LawnControlConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lawn Control."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = _clean_user_input(user_input)
            errors.update(_rain_setting_errors(user_input))
            if user_input[CONF_MIN_GRASS_HEIGHT] >= user_input[CONF_MAX_GRASS_HEIGHT]:
                errors["base"] = "height_range"
            elif not _is_valid_mowing_update_frequency(
                user_input.get(CONF_MOWING_UPDATE_FREQUENCY)
            ):
                errors[CONF_MOWING_UPDATE_FREQUENCY] = "invalid_mowing_update_frequency"
            elif not _is_valid_hour(user_input.get(CONF_DAILY_UPDATE_HOUR)):
                errors[CONF_DAILY_UPDATE_HOUR] = "invalid_hour"
            elif user_input.get(CONF_LAST_FERTILIZED_DATE) and not _is_valid_date(
                user_input[CONF_LAST_FERTILIZED_DATE]
            ):
                errors[CONF_LAST_FERTILIZED_DATE] = "invalid_date"
            elif not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> LawnControlOptionsFlow:
        """Create the options flow."""
        return LawnControlOptionsFlow(config_entry)


class LawnControlOptionsFlow(config_entries.OptionsFlow):
    """Handle Lawn Control options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage options."""
        errors: dict[str, str] = {}
        current = {**self._config_entry.data, **self._config_entry.options}

        if user_input is not None:
            user_input = _clean_user_input(user_input)
            errors.update(_rain_setting_errors(user_input))
            if user_input[CONF_MIN_GRASS_HEIGHT] >= user_input[CONF_MAX_GRASS_HEIGHT]:
                errors["base"] = "height_range"
            elif not _is_valid_mowing_update_frequency(
                user_input.get(CONF_MOWING_UPDATE_FREQUENCY)
            ):
                errors[CONF_MOWING_UPDATE_FREQUENCY] = "invalid_mowing_update_frequency"
            elif not _is_valid_hour(user_input.get(CONF_DAILY_UPDATE_HOUR)):
                errors[CONF_DAILY_UPDATE_HOUR] = "invalid_hour"
            elif user_input.get(CONF_LAST_FERTILIZED_DATE) and not _is_valid_date(
                user_input[CONF_LAST_FERTILIZED_DATE]
            ):
                errors[CONF_LAST_FERTILIZED_DATE] = "invalid_date"
            elif not errors:
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(current),
            errors=errors,
        )
