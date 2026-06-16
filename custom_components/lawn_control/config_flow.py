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
    CONF_FERTILIZER_K_PERCENT,
    CONF_FERTILIZER_N_PERCENT,
    CONF_FERTILIZER_P_PERCENT,
    CONF_HUMIDITY_SENSOR,
    CONF_LAWN_TYPE,
    CONF_LAST_FERTILIZED_DATE,
    CONF_MAX_GRASS_HEIGHT,
    CONF_MIN_GRASS_HEIGHT,
    CONF_RAIN_SENSOR,
    CONF_ROBOTIC_MOWER,
    CONF_SHADE_LEVEL,
    CONF_SOIL_MOISTURE_SENSOR,
    CONF_SOIL_TYPE,
    CONF_TEMPERATURE_SENSOR,
    CONF_WATER_DURING_DROUGHT,
    CONF_WATERING_LEVEL,
    CONF_WEATHER_ENTITY,
    DEFAULT_MAX_GRASS_HEIGHT,
    DEFAULT_MIN_GRASS_HEIGHT,
    DOMAIN,
    LAWN_TYPES,
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
            if user_input[CONF_MIN_GRASS_HEIGHT] >= user_input[CONF_MAX_GRASS_HEIGHT]:
                errors["base"] = "height_range"
            elif user_input.get(CONF_LAST_FERTILIZED_DATE) and not _is_valid_date(
                user_input[CONF_LAST_FERTILIZED_DATE]
            ):
                errors[CONF_LAST_FERTILIZED_DATE] = "invalid_date"
            else:
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
            if user_input[CONF_MIN_GRASS_HEIGHT] >= user_input[CONF_MAX_GRASS_HEIGHT]:
                errors["base"] = "height_range"
            elif user_input.get(CONF_LAST_FERTILIZED_DATE) and not _is_valid_date(
                user_input[CONF_LAST_FERTILIZED_DATE]
            ):
                errors[CONF_LAST_FERTILIZED_DATE] = "invalid_date"
            else:
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(current),
            errors=errors,
        )
