"""Fertilizer suitability rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import CONF_CARE_LEVEL, CONF_SOIL_TYPE, DEFAULT_FERTILIZER_THRESHOLD

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData


def calculate_fertilizer_score(
    config: dict[str, Any],
    weather: LawnWeatherData,
    drought: dict[str, Any],
    growth: dict[str, Any],
) -> dict[str, Any]:
    """Score whether fertilizer conditions are suitable."""
    score = 50
    details: dict[str, Any] = {"base": 50}
    blocking_factors: list[str] = []

    if weather.month in (4, 5, 6, 8, 9):
        score += 20
        details["season"] = "active growing season"
    elif weather.month in (11, 12, 1, 2):
        score -= 35
        blocking_factors.append("Grass is unlikely to use fertilizer in winter.")
        details["season"] = "winter"
    else:
        details["season"] = "shoulder season"

    if growth["value"] in ("normal", "fast"):
        score += 15
        details["growth"] = growth["value"]
    elif growth["value"] == "stopped":
        score -= 30
        blocking_factors.append("Growth appears stopped.")

    if drought["value"] in ("high", "critical"):
        score -= 40
        blocking_factors.append("Avoid fertilizer during significant drought stress.")

    if weather.forecast_rain is not None:
        if 2 <= weather.forecast_rain <= 12:
            score += 10
            details["forecast_rain"] = "light rain can water fertilizer in"
        elif weather.forecast_rain > 20:
            score -= 25
            blocking_factors.append("Heavy forecast rain may wash fertilizer away.")

    if weather.temperature is not None and weather.temperature >= 28:
        score -= 20
        blocking_factors.append("High temperature increases scorch risk.")

    if config.get(CONF_SOIL_TYPE) == "sandy":
        score -= 5
        details["soil_type"] = "sandy soil favors lighter applications"

    if config.get(CONF_CARE_LEVEL) == "high":
        score += 5
    elif config.get(CONF_CARE_LEVEL) == "low":
        score -= 5

    score = max(0, min(100, score))
    return {
        "score": score,
        "threshold": DEFAULT_FERTILIZER_THRESHOLD,
        "blocking_factors": blocking_factors,
        "attributes": {
            "score": score,
            "threshold": DEFAULT_FERTILIZER_THRESHOLD,
            "details": details,
            "blocking_factors": blocking_factors,
            "reason": f"Fertilizer score is {score} after season, growth and stress checks.",
        },
        "reason": f"Fertilizer score is {score} after season, growth and stress checks.",
    }
