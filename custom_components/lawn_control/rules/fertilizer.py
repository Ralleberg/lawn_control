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
    language: str,
) -> dict[str, Any]:
    """Score whether fertilizer conditions are suitable."""
    score = 50
    details: dict[str, Any] = {"base": 50}
    blocking_factors: list[str] = []
    text = _texts(language)

    if weather.month in (4, 5, 6, 8, 9):
        score += 20
        details["season"] = text["active_season"]
    elif weather.month in (11, 12, 1, 2):
        score -= 35
        blocking_factors.append(text["winter_block"])
        details["season"] = text["winter"]
    else:
        details["season"] = text["shoulder_season"]

    if growth["value"] in ("normal", "fast"):
        score += 15
        details["growth"] = text[growth["value"]]
    elif growth["value"] == "stopped":
        score -= 30
        blocking_factors.append(text["growth_stopped"])

    if drought["value"] in ("high", "critical"):
        score -= 40
        blocking_factors.append(text["drought_block"])

    if weather.forecast_rain is not None:
        if 2 <= weather.forecast_rain <= 12:
            score += 10
            details["forecast_rain"] = text["light_rain"]
        elif weather.forecast_rain > 20:
            score -= 25
            blocking_factors.append(text["heavy_rain"])

    if weather.temperature is not None and weather.temperature >= 28:
        score -= 20
        blocking_factors.append(text["heat_block"])

    if config.get(CONF_SOIL_TYPE) == "sandy":
        score -= 5
        details["soil_type"] = text["sandy_soil"]

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
            "reason": text["reason"].format(score=score),
        },
        "reason": text["reason"].format(score=score),
    }


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
            "reason": "Gødningsscore er {score} efter kontrol af sæson, vækst og stress.",
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
        "reason": "Fertilizer score is {score} after season, growth and stress checks.",
        "stopped": "stopped",
        "slow": "slow",
        "normal": "normal",
        "fast": "fast",
    }
