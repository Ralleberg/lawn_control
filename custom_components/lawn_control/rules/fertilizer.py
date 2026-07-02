"""Fertilizer suitability rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import (
    CONF_DAYS_SINCE_FERTILIZER,
    CONF_FERTILIZER_K_PERCENT,
    CONF_FERTILIZER_N_PERCENT,
    CONF_FERTILIZER_P_PERCENT,
    DEFAULT_FERTILIZER_NEED_THRESHOLD,
)
from .moisture import lacks_moisture_support, moisture_status

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData

def calculate_fertilizer_score(
    config: dict[str, Any],
    weather: LawnWeatherData,
    drought: dict[str, Any],
    growth: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Score remaining fertilizer effect and check whether conditions block use."""
    blocking_factors: list[str] = []
    text = _texts(language)

    if weather.month in (11, 12, 1, 2):
        blocking_factors.append(text["winter_block"])

    if growth["value"] == "stopped":
        blocking_factors.append(text["growth_stopped"])

    if drought["value"] in ("high", "critical"):
        blocking_factors.append(text["drought_block"])

    forecast_rain = weather.forecast_rain_5_days
    moisture = moisture_status(config, weather, language)
    if lacks_moisture_support(config, weather):
        blocking_factors.append(text["rain_block"])

    if weather.temperature is not None and weather.temperature >= 28:
        blocking_factors.append(text["heat_block"])

    score = _fertilizer_residual_score(config)
    days_since_fertilizer = _float_config(config, CONF_DAYS_SINCE_FERTILIZER, default=90)

    return {
        "score": score,
        "threshold": DEFAULT_FERTILIZER_NEED_THRESHOLD,
        "blocking_factors": blocking_factors,
        "attributes": {
            "threshold": DEFAULT_FERTILIZER_NEED_THRESHOLD,
            "days_since_fertilizer": days_since_fertilizer,
            "historical_rain": weather.historical_rain,
            "forecast_rain": forecast_rain,
            "moisture_status": moisture,
            "blocking_factors": blocking_factors,
            "reason": text["reason"].format(score=score),
        },
        "reason": text["reason"].format(score=score),
    }


def _fertilizer_residual_score(config: dict[str, Any]) -> int:
    """Estimate remaining fertilizer effect from age and NPK strength."""
    return round(100 * _fertilizer_strength(config) * _fertilizer_age_factor(config))


def _fertilizer_age_factor(config: dict[str, Any]) -> float:
    """Return the remaining age factor over a 60 day fertilizer window."""
    days = _float_config(config, CONF_DAYS_SINCE_FERTILIZER, default=90)
    if days >= 60:
        return 0.0
    return round(max(0.0, 1 - (days / 60)), 2)


def _fertilizer_strength(config: dict[str, Any]) -> float:
    """Estimate relative fertilizer strength from configured NPK percentages."""
    n = _float_config(config, CONF_FERTILIZER_N_PERCENT)
    p = _float_config(config, CONF_FERTILIZER_P_PERCENT)
    k = _float_config(config, CONF_FERTILIZER_K_PERCENT)

    if n <= 0:
        return 0.0

    nitrogen_factor = min(n / 20, 1.5)
    complete_factor = 1.0
    if p > 0:
        complete_factor += 0.05
    if k > 0:
        complete_factor += 0.05
    return round(min(1.0, nitrogen_factor * complete_factor), 2)


def _float_config(config: dict[str, Any], key: str, default: float = 0.0) -> float:
    """Read a numeric config value."""
    try:
        return float(config.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _texts(language: str) -> dict[str, str]:
    """Return localized fertilizer text."""
    if language.lower().startswith("da"):
        return {
            "active_season": "aktiv vækstsæson",
            "winter": "vinter",
            "shoulder_season": "overgangssæson",
            "winter_block": "Græsset kan sandsynligvis ikke udnytte gødning om vinteren.",
            "growth_stopped": "Væksten ser ud til at være stoppet.",
            "drought_block": "Undgå gødning under betydelig tørkestress.",
            "rain_block": "Gødning kræver fugtig jord, vanding i tørre perioder eller mindst 20 mm samlet historisk og forecast-regn.",
            "heat_block": "Høj temperatur øger risikoen for svidning.",
            "sandy_soil": "sandet jord egner sig bedre til lettere doseringer",
            "reason": "Gødningsscore er {score} baseret på gødningsalder og NPK-styrke.",
            "score_basis": "Score beregnes kun fra NPK og dage siden seneste gødning.",
            "stopped": "stoppet",
            "slow": "langsom",
            "normal": "normal",
            "fast": "hurtig",
        }

    return {
        "active_season": "active growing season",
        "winter": "winter",
        "shoulder_season": "shoulder season",
        "winter_block": "Grass is unlikely to use fertilizer in winter.",
        "growth_stopped": "Growth appears stopped.",
        "drought_block": "Avoid fertilizer during significant drought stress.",
        "rain_block": "Fertilizer requires moist soil, watering during dry periods or at least 20 mm combined historical and forecast rain.",
        "heat_block": "High temperature increases scorch risk.",
        "sandy_soil": "sandy soil favors lighter applications",
        "reason": "Fertilizer score is {score} from fertilizer age and NPK strength.",
        "score_basis": "Score is calculated only from NPK and days since last fertilizer.",
        "stopped": "stopped",
        "slow": "slow",
        "normal": "normal",
        "fast": "fast",
    }
