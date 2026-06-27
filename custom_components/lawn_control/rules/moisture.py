"""Shared moisture support rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import (
    CONF_WATER_DURING_DROUGHT,
    FORECAST_RAIN_OK_MM,
    HISTORICAL_RAIN_OK_MM,
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

    return _rain_reaches(
        weather.historical_rain, HISTORICAL_RAIN_OK_MM
    ) or _rain_reaches(weather.forecast_rain_5_days, FORECAST_RAIN_OK_MM)


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


def _rain_reaches(value: float | None, threshold: int) -> bool:
    """Return whether a rain value reaches the configured threshold."""
    return value is not None and value >= threshold


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
