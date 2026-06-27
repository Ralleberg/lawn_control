"""Transparent lawn care rule orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import (
    CONF_LAWN_TYPE,
    CONF_MAX_GRASS_HEIGHT,
    CONF_MIN_GRASS_HEIGHT,
    CONF_SHADE_LEVEL,
    DEFAULT_MAX_GRASS_HEIGHT,
    DEFAULT_MIN_GRASS_HEIGHT,
)
from .drought import calculate_drought_risk
from .fertilizer import (
    FORECAST_RAIN_OK_MM,
    HISTORICAL_RAIN_OK_MM,
    calculate_fertilizer_score,
)
from .maintenance import calculate_verticut_advice
from .mowing import (
    calculate_growth_rate,
    calculate_mowing_advice,
    calculate_robot_mower_advice,
)

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData


def build_advice(
    config: dict[str, Any], weather: LawnWeatherData, language: str
) -> dict[str, Any]:
    """Build all lawn advice from config and weather inputs."""
    height = recommended_grass_height(config, weather, language)
    drought = calculate_drought_risk(config, weather, language)
    growth = calculate_growth_rate(config, weather, drought, language)
    fertilizer = calculate_fertilizer_score(config, weather, drought, growth, language)
    mowing = calculate_mowing_advice(
        config, weather, drought, growth, height, language
    )
    robot_mower = calculate_robot_mower_advice(
        config, weather, drought, growth, mowing, language
    )
    verticut = calculate_verticut_advice(weather, drought, growth, language)
    recommendation = general_recommendation(
        drought, fertilizer, mowing, growth, language
    )

    return {
        "recommended_grass_height": height,
        "drought_risk": drought,
        "growth_rate": growth,
        "fertilizer_score": fertilizer,
        "fertilizer_day": {
            "value": fertilizer["score"] < fertilizer["threshold"]
            and not fertilizer["blocking_factors"],
            "attributes": {
                "score": fertilizer["score"],
                "blocking_factors": fertilizer["blocking_factors"],
                "reason": fertilizer["reason"],
            },
        },
        "should_mow": mowing,
        "robot_mower_should_run": robot_mower,
        "should_verticut": verticut,
        "care_recommendation": recommendation,
    }


def recommended_grass_height(
    config: dict[str, Any], weather: LawnWeatherData, language: str
) -> dict[str, Any]:
    """Calculate a recommended grass height range."""
    configured_min_height = int(
        config.get(CONF_MIN_GRASS_HEIGHT, DEFAULT_MIN_GRASS_HEIGHT)
    )
    configured_max_height = int(
        config.get(CONF_MAX_GRASS_HEIGHT, DEFAULT_MAX_GRASS_HEIGHT)
    )
    min_height = configured_min_height
    max_height = configured_max_height
    reasons: list[str] = []
    text = _texts(language)

    if config.get(CONF_LAWN_TYPE) == "ornamental":
        min_height = max(20, min_height - 5)
        max_height = max(min_height + 5, max_height - 5)
        reasons.append(text["ornamental_height"])
    elif config.get(CONF_LAWN_TYPE) == "wear_tolerant":
        min_height += 5
        max_height += 5
        reasons.append(text["wear_height"])
    elif config.get(CONF_LAWN_TYPE) == "shade":
        min_height += 10
        max_height += 10
        reasons.append(text["shade_height"])

    if config.get(CONF_SHADE_LEVEL) == "high":
        min_height += 5
        max_height += 5
        reasons.append(text["high_shade_height"])

    if weather.month in (6, 7, 8):
        min_height += 5
        max_height += 5
        reasons.append(text["summer_height"])

    historical_rain_ok = _rain_reaches(weather.historical_rain, HISTORICAL_RAIN_OK_MM)
    forecast_rain = weather.forecast_rain_5_days
    forecast_rain_ok = _rain_reaches(forecast_rain, FORECAST_RAIN_OK_MM)
    not_enough_rain = not historical_rain_ok and not forecast_rain_ok
    if not_enough_rain:
        reasons.append(text["low_rain_height"])

    if max_height > configured_max_height:
        max_height = configured_max_height
        reasons.append(text["configured_max_cap"])

    if min_height > max_height:
        min_height = max_height

    if not reasons:
        reasons.append(text["configured_height"])

    target = max_height if not_enough_rain else round((min_height + max_height) / 2)
    return {
        "value": target,
        "attributes": {
            "min_height": min_height,
            "max_height": max_height,
            "historical_rain": weather.historical_rain,
            "forecast_rain": forecast_rain,
            "reason": " ".join(reasons),
        },
    }


def general_recommendation(
    drought: dict[str, Any],
    fertilizer: dict[str, Any],
    mowing: dict[str, Any],
    growth: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Create a short human-readable recommendation."""
    actions: list[str] = []
    reasons: list[str] = []
    text = _texts(language)

    if drought["value"] in ("high", "critical"):
        actions.append(text["water"])
        reasons.append(text["drought"].format(risk=text[drought["value"]]))
    if mowing["value"]:
        actions.append(text["mow"])
        reasons.append(text["mow_reason"])
    if (
        fertilizer["score"] < fertilizer["threshold"]
        and not fertilizer["blocking_factors"]
    ):
        actions.append(text["fertilize"])
        reasons.append(text["fertilizer_score"].format(score=fertilizer["score"]))
    if not actions:
        actions.append(text["monitor"])
        reasons.append(text["growth"].format(growth=text[growth["value"]]))

    return {
        "value": " ".join(actions),
        "attributes": {
            "actions": actions,
            "reason": " ".join(reasons),
        },
    }


def _rain_reaches(value: float | None, threshold: int) -> bool:
    """Return whether a rain value reaches the configured threshold."""
    return value is not None and value >= threshold


def _texts(language: str) -> dict[str, str]:
    """Return localized rule text."""
    if language.lower().startswith("da"):
        return {
            "water": "Prioriter vanding og undgå at stresse plænen.",
            "drought": "Tørkerisikoen er {risk}.",
            "mow": "Det er egnet at slå græs i dag.",
            "mow_reason": "Forholdene er tørre nok, og væksten understøtter klipning.",
            "fertilize": "Forholdene er gode til gødning.",
            "fertilizer_score": "Gødningsscoren er {score}.",
            "monitor": "Hold øje med forholdene.",
            "growth": "Væksten er {growth}, og der anbefales ingen større handling.",
            "low": "lav",
            "medium": "mellem",
            "high": "høj",
            "critical": "kritisk",
            "stopped": "stoppet",
            "slow": "langsom",
            "normal": "normal",
            "fast": "hurtig",
            "ornamental_height": "Prydplæner kan holdes en smule kortere.",
            "wear_height": "Slidstærke plæner kommer sig bedre med lidt mere bladmasse.",
            "shade_height": "Skyggeplæner har brug for ekstra bladmasse for stærkere vækst.",
            "high_shade_height": "Meget skygge øger den anbefalede klippehøjde.",
            "summer_height": "Sommerstress taler for en højere klippehøjde.",
            "low_rain_height": "For lidt historisk og forecast-regn flytter målet til maksimumhøjden.",
            "configured_max_cap": "Det indtastede maksimum bruges som øvre grænse.",
            "configured_height": "Det konfigurerede højdeinterval passer til de aktuelle forhold.",
        }

    return {
        "water": "Prioritize watering and avoid stressing the lawn.",
        "drought": "Drought risk is {risk}.",
        "mow": "Mowing is suitable today.",
        "mow_reason": "Conditions are dry enough and growth supports regular mowing.",
        "fertilize": "Fertilizer conditions are favorable.",
        "fertilizer_score": "Fertilizer score is {score}.",
        "monitor": "Keep monitoring conditions.",
        "growth": "Growth is {growth} and no major action is recommended.",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "critical": "critical",
        "stopped": "stopped",
        "slow": "slow",
        "normal": "normal",
        "fast": "fast",
        "ornamental_height": "Ornamental lawns can be kept slightly shorter.",
        "wear_height": "Wear tolerant lawns recover better with a little more leaf area.",
        "shade_height": "Shaded lawns need extra leaf area for stronger growth.",
        "high_shade_height": "High shade increases the recommended cutting height.",
        "summer_height": "Summer stress favors a higher mowing height.",
        "low_rain_height": "Insufficient historical and forecast rain moves the target to maximum height.",
        "configured_max_cap": "The configured maximum is used as the upper limit.",
        "configured_height": "Configured lawn height range is suitable for current conditions.",
    }
