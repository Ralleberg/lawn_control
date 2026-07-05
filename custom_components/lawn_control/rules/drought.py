"""Drought risk rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import CONF_SOIL_TYPE, CONF_WATER_DURING_DROUGHT, CONF_WATERING_LEVEL
from .moisture import forecast_rain_threshold, historical_rain_threshold

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData


def calculate_drought_risk(
    config: dict[str, Any], weather: LawnWeatherData, language: str
) -> dict[str, Any]:
    """Estimate drought risk from recent rain, forecast rain, heat, humidity and soil."""
    score = 0
    text = _texts(language)

    recent_rain = _best_recent_rain(weather.recent_rain, weather.historical_rain)
    forecast_rain = weather.forecast_rain_5_days
    temperature = _highest_value(weather.temperature, weather.historical_temperature)
    humidity = _lowest_value(weather.humidity, weather.historical_humidity)
    sun_stress = _sun_stress(weather.weather_state, weather.forecast_condition)
    historical_threshold = historical_rain_threshold(config)
    forecast_threshold = forecast_rain_threshold(config)

    if recent_rain is None:
        score += 20
    elif recent_rain < historical_threshold / 4:
        score += 35
    elif recent_rain < historical_threshold:
        score += 20

    if forecast_rain is None:
        score += 10
    elif forecast_rain < forecast_threshold / 4:
        score += 20
    elif forecast_rain >= forecast_threshold:
        score -= 15

    if temperature is not None and temperature >= 28:
        score += 25
    elif temperature is not None and temperature >= 24:
        score += 15
    elif temperature is not None and temperature >= 20:
        score += 5

    if humidity is not None and humidity < 35:
        score += 20
    elif humidity is not None and humidity < 45:
        score += 12
    elif humidity is not None and humidity >= 75:
        score -= 5

    if sun_stress:
        score += sun_stress
        if temperature is not None and temperature >= 24:
            score += 5

    if weather.soil_moisture is not None:
        if weather.soil_moisture < 25:
            score += 25
        elif weather.soil_moisture > 45:
            score -= 20

    if config.get(CONF_SOIL_TYPE) == "sandy":
        score += 10
    elif config.get(CONF_SOIL_TYPE) == "clay":
        score -= 5

    if weather.month in (6, 7, 8):
        score += 10

    watering_reduction = _watering_reduction(config)
    if watering_reduction:
        score -= watering_reduction

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
            "historical_rain": weather.historical_rain,
            "forecast_rain": forecast_rain,
            "reason": text["reason"].format(score=score),
        },
    }


def _best_recent_rain(
    current_rain: float | None, historical_rain: float | None
) -> float | None:
    """Use the highest observed rain from current and recent historical data."""
    values = [value for value in (current_rain, historical_rain) if value is not None]
    if not values:
        return None
    return max(values)


def _highest_value(*values: float | None) -> float | None:
    """Return the highest known value."""
    known = [value for value in values if value is not None]
    return max(known) if known else None


def _lowest_value(*values: float | None) -> float | None:
    """Return the lowest known value."""
    known = [value for value in values if value is not None]
    return min(known) if known else None


def _sun_stress(weather_state: str | None, forecast_condition: str | None) -> int:
    """Return drying stress from sunny current or forecast weather."""
    conditions = {weather_state, forecast_condition}
    if "sunny" in conditions:
        return 15
    if "partlycloudy" in conditions:
        return 8
    return 0


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


def _texts(language: str) -> dict[str, str]:
    """Return localized drought text."""
    if language.lower().startswith("da"):
        return {
            "unknown": "ukendt",
            "very_low": "meget lav",
            "low": "lav",
            "adequate": "tilstrækkelig",
            "little_expected": "kun lidt forventet",
            "meaningful_rain": "betydelig regn forventes",
            "hot": "varmt",
            "warm": "lunt",
            "dry_air": "tør luft",
            "sandy_soil": "sandet jord tørrer hurtigt",
            "clay_soil": "lerjord holder længere på vandet",
            "summer": "sommer",
            "watering_reduction": "vanding i tørre perioder reducerer stress med {reduction}",
            "reason": "Tørkescore er {score} baseret på vand, varme og jordinput.",
        }

    return {
        "unknown": "unknown",
        "very_low": "very low",
        "low": "low",
        "adequate": "adequate",
        "little_expected": "little expected",
        "meaningful_rain": "meaningful rain expected",
        "hot": "hot",
        "warm": "warm",
        "dry_air": "dry air",
        "sandy_soil": "sandy soil dries quickly",
        "clay_soil": "clay soil holds water longer",
        "summer": "summer",
        "watering_reduction": "watering during dry periods reduces stress by {reduction}",
        "reason": "Drought score is {score} based on water, heat and soil inputs.",
    }
