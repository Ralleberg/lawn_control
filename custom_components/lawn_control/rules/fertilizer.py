"""Fertilizer suitability rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import (
    CONF_CARE_LEVEL,
    CONF_DAYS_SINCE_FERTILIZER,
    CONF_FERTILIZER_K_PERCENT,
    CONF_FERTILIZER_N_PERCENT,
    CONF_FERTILIZER_P_PERCENT,
    CONF_SOIL_TYPE,
    DEFAULT_FERTILIZER_NEED_THRESHOLD,
)

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData


def calculate_fertilizer_score(
    config: dict[str, Any],
    weather: LawnWeatherData,
    drought: dict[str, Any],
    growth: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Score whether fertilizer conditions are suitable."""
    condition_score = 50
    details: dict[str, Any] = {"condition_base": 50}
    blocking_factors: list[str] = []
    text = _texts(language)

    if weather.month in (4, 5, 6, 8, 9):
        condition_score += 20
        details["season"] = text["active_season"]
    elif weather.month in (11, 12, 1, 2):
        condition_score -= 35
        blocking_factors.append(text["winter_block"])
        details["season"] = text["winter"]
    else:
        details["season"] = text["shoulder_season"]

    if growth["value"] in ("normal", "fast"):
        condition_score += 15
        details["growth"] = text[growth["value"]]
    elif growth["value"] == "stopped":
        condition_score -= 30
        blocking_factors.append(text["growth_stopped"])

    if drought["value"] in ("high", "critical"):
        condition_score -= 40
        blocking_factors.append(text["drought_block"])

    if weather.forecast_rain is not None:
        if 2 <= weather.forecast_rain <= 12:
            condition_score += 10
            details["forecast_rain"] = text["light_rain"]
        elif weather.forecast_rain > 20:
            condition_score -= 25
            blocking_factors.append(text["heavy_rain"])

    if weather.temperature is not None and weather.temperature >= 28:
        condition_score -= 20
        blocking_factors.append(text["heat_block"])

    if config.get(CONF_SOIL_TYPE) == "sandy":
        condition_score -= 5
        details["soil_type"] = text["sandy_soil"]

    if config.get(CONF_CARE_LEVEL) == "high":
        condition_score += 5
    elif config.get(CONF_CARE_LEVEL) == "low":
        condition_score -= 5

    condition_score = max(0, min(100, condition_score))
    score = _fertilizer_residual_score(config)
    details["condition_score"] = condition_score
    details["days_since_fertilizer"] = _float_config(
        config, CONF_DAYS_SINCE_FERTILIZER, default=90
    )
    details["fertilizer_strength"] = _fertilizer_strength(config)

    return {
        "score": score,
        "threshold": DEFAULT_FERTILIZER_NEED_THRESHOLD,
        "blocking_factors": blocking_factors,
        "attributes": {
            "score": score,
            "threshold": DEFAULT_FERTILIZER_NEED_THRESHOLD,
            "details": details,
            "blocking_factors": blocking_factors,
            "reason": text["reason"].format(score=score),
        },
        "reason": text["reason"].format(score=score),
    }


def _fertilizer_residual_score(config: dict[str, Any]) -> int:
    """Estimate remaining fertilizer effect from age and NPK strength."""
    days = _float_config(config, CONF_DAYS_SINCE_FERTILIZER, default=90)
    if days >= 60:
        return 0

    age_factor = max(0.0, 1 - (days / 60))
    return round(100 * _fertilizer_strength(config) * age_factor)


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
            "light_rain": "let regn kan vande gødningen ned",
            "heavy_rain": "kraftig forecast-regn kan vaske gødningen væk.",
            "heat_block": "Høj temperatur øger risikoen for svidning.",
            "sandy_soil": "sandet jord egner sig bedre til lettere doseringer",
            "reason": "Gødningsscore er {score} baseret på gødningsalder og NPK-styrke.",
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
        "light_rain": "light rain can water fertilizer in",
        "heavy_rain": "Heavy forecast rain may wash fertilizer away.",
        "heat_block": "High temperature increases scorch risk.",
        "sandy_soil": "sandy soil favors lighter applications",
        "reason": "Fertilizer score is {score} from fertilizer age and NPK strength.",
        "stopped": "stopped",
        "slow": "slow",
        "normal": "normal",
        "fast": "fast",
    }
