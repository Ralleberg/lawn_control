"""Shared moisture support rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import (
    CONF_FORECAST_RAIN_THRESHOLD,
    CONF_HISTORICAL_RAIN_THRESHOLD,
    CONF_WATER_DURING_DROUGHT,
    DEFAULT_FORECAST_RAIN_THRESHOLD,
    DEFAULT_HISTORICAL_RAIN_THRESHOLD,
)

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData

SOIL_MOISTURE_OK = 25


def has_moisture_support(config: dict[str, Any], weather: LawnWeatherData) -> bool:
    """Return whether the lawn has enough moisture support for active care."""
    if config.get(CONF_WATER_DURING_DROUGHT):
        return True

    if weather.soil_moisture is not None:
        return weather.soil_moisture >= SOIL_MOISTURE_OK

    historical_rain = _rain_value(weather.historical_rain)
    forecast_rain = _rain_value(weather.forecast_rain_5_days)
    return (
        historical_rain >= historical_rain_threshold(config)
        or forecast_rain >= forecast_rain_threshold(config)
        or rain_moisture_total(weather) >= rain_moisture_threshold(config)
    )


def rain_moisture_total(weather: LawnWeatherData) -> float:
    """Return combined historical and forecast rain support."""
    return round(
        _rain_value(weather.historical_rain)
        + _rain_value(weather.forecast_rain_5_days),
        1,
    )


def historical_rain_threshold(config: dict[str, Any]) -> float:
    """Return the configured historical rain threshold."""
    return _float_config(
        config,
        CONF_HISTORICAL_RAIN_THRESHOLD,
        DEFAULT_HISTORICAL_RAIN_THRESHOLD,
    )


def forecast_rain_threshold(config: dict[str, Any]) -> float:
    """Return the configured forecast rain threshold."""
    return _float_config(
        config,
        CONF_FORECAST_RAIN_THRESHOLD,
        DEFAULT_FORECAST_RAIN_THRESHOLD,
    )


def rain_moisture_threshold(config: dict[str, Any]) -> float:
    """Return the combined rain threshold for general moisture support."""
    return max(historical_rain_threshold(config), forecast_rain_threshold(config))


def lacks_moisture_support(config: dict[str, Any], weather: LawnWeatherData) -> bool:
    """Return whether the lawn lacks enough moisture support for active care."""
    return not has_moisture_support(config, weather)


def moisture_status(
    config: dict[str, Any], weather: LawnWeatherData, language: str
) -> str:
    """Return localized moisture status text."""
    text = _texts(language)
    if config.get(CONF_WATER_DURING_DROUGHT):
        return text["watering"]

    if weather.soil_moisture is not None:
        return (
            text["supported"]
            if weather.soil_moisture >= SOIL_MOISTURE_OK
            else text["missing"]
        )

    return text["supported"] if has_moisture_support(config, weather) else text["missing"]


def _rain_value(value: float | None) -> float:
    """Return rain as a numeric value."""
    return value if value is not None else 0.0


def _float_config(config: dict[str, Any], key: str, default: float) -> float:
    """Read a numeric config value."""
    value = config.get(key, default)
    if value in (None, ""):
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _texts(language: str) -> dict[str, str]:
    """Return localized moisture text."""
    if language.lower().startswith("da"):
        return {
            "supported": "fugtkravet er opfyldt",
            "missing": "fugtkravet er ikke opfyldt",
            "watering": "vanding i tørre perioder er aktiveret",
        }

    return {
        "supported": "moisture requirement is met",
        "missing": "moisture requirement is not met",
        "watering": "watering during dry periods is enabled",
    }
