"""Drought risk rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import CONF_SOIL_TYPE, CONF_WATER_DURING_DROUGHT, CONF_WATERING_LEVEL

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData


def calculate_drought_risk(
    config: dict[str, Any], weather: LawnWeatherData, language: str
) -> dict[str, Any]:
    """Estimate drought risk from recent rain, forecast rain, heat, humidity and soil."""
    score = 0
    details: dict[str, Any] = {}
    text = _texts(language)

    recent_rain = weather.recent_rain
    forecast_rain = weather.forecast_rain
    temperature = weather.temperature
    humidity = weather.humidity

    if recent_rain is None:
        score += 20
        details["recent_rain"] = text["unknown"]
    elif recent_rain < 2:
        score += 35
        details["recent_rain"] = text["very_low"]
    elif recent_rain < 8:
        score += 20
        details["recent_rain"] = text["low"]
    else:
        details["recent_rain"] = text["adequate"]

    if forecast_rain is None:
        score += 10
        details["forecast_rain"] = text["unknown"]
    elif forecast_rain < 2:
        score += 20
        details["forecast_rain"] = text["little_expected"]
    elif forecast_rain >= 8:
        score -= 15
        details["forecast_rain"] = text["meaningful_rain"]

    if temperature is not None and temperature >= 26:
        score += 20
        details["temperature"] = text["hot"]
    elif temperature is not None and temperature >= 22:
        score += 10
        details["temperature"] = text["warm"]

    if humidity is not None and humidity < 45:
        score += 15
        details["humidity"] = text["dry_air"]

    if weather.soil_moisture is not None:
        details["soil_moisture"] = weather.soil_moisture
        if weather.soil_moisture < 25:
            score += 25
        elif weather.soil_moisture > 45:
            score -= 20

    if config.get(CONF_SOIL_TYPE) == "sandy":
        score += 10
        details["soil_type"] = text["sandy_soil"]
    elif config.get(CONF_SOIL_TYPE) == "clay":
        score -= 5
        details["soil_type"] = text["clay_soil"]

    if weather.month in (6, 7, 8):
        score += 10
        details["season"] = text["summer"]

    watering_reduction = _watering_reduction(config)
    if watering_reduction:
        score -= watering_reduction
        details["watering"] = (
            text["watering_reduction"].format(reduction=watering_reduction)
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
            "reason": text["reason"].format(score=score),
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
