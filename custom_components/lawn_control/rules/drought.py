"""Drought risk rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import CONF_SOIL_TYPE, CONF_WATER_DURING_DROUGHT, CONF_WATERING_LEVEL

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData


def calculate_drought_risk(
    config: dict[str, Any], weather: LawnWeatherData
) -> dict[str, Any]:
    """Estimate drought risk from recent rain, forecast rain, heat, humidity and soil."""
    score = 0
    details: dict[str, Any] = {}

    recent_rain = weather.recent_rain
    forecast_rain = weather.forecast_rain
    temperature = weather.temperature
    humidity = weather.humidity

    if recent_rain is None:
        score += 20
        details["recent_rain"] = "unknown"
    elif recent_rain < 2:
        score += 35
        details["recent_rain"] = "very low"
    elif recent_rain < 8:
        score += 20
        details["recent_rain"] = "low"
    else:
        details["recent_rain"] = "adequate"

    if forecast_rain is None:
        score += 10
        details["forecast_rain"] = "unknown"
    elif forecast_rain < 2:
        score += 20
        details["forecast_rain"] = "little expected"
    elif forecast_rain >= 8:
        score -= 15
        details["forecast_rain"] = "meaningful rain expected"

    if temperature is not None and temperature >= 26:
        score += 20
        details["temperature"] = "hot"
    elif temperature is not None and temperature >= 22:
        score += 10
        details["temperature"] = "warm"

    if humidity is not None and humidity < 45:
        score += 15
        details["humidity"] = "dry air"

    if weather.soil_moisture is not None:
        details["soil_moisture"] = weather.soil_moisture
        if weather.soil_moisture < 25:
            score += 25
        elif weather.soil_moisture > 45:
            score -= 20

    if config.get(CONF_SOIL_TYPE) == "sandy":
        score += 10
        details["soil_type"] = "sandy soil dries quickly"
    elif config.get(CONF_SOIL_TYPE) == "clay":
        score -= 5
        details["soil_type"] = "clay soil holds water longer"

    if weather.month in (6, 7, 8):
        score += 10
        details["season"] = "summer"

    watering_reduction = _watering_reduction(config)
    if watering_reduction:
        score -= watering_reduction
        details["watering"] = (
            f"watering during dry periods reduces stress by {watering_reduction}"
        )

    score = max(0, min(100, score))
    if score >= 80:
        risk = "critical"
    elif score >= 60:
        risk = "high"
    elif score >= 35:
        risk = "medium"
    else:
        risk = "low"

    return {
        "value": risk,
        "attributes": {
            "score": score,
            "details": details,
            "reason": f"Drought score is {score} based on water, heat and soil inputs.",
        },
    }


def _watering_reduction(config: dict[str, Any]) -> int:
    """Return drought stress reduction from configured watering."""
    if not config.get(CONF_WATER_DURING_DROUGHT):
        return 0

    level = config.get(CONF_WATERING_LEVEL, "normal")
    if level == "high":
        return 30
    if level == "low":
        return 12
    return 22
