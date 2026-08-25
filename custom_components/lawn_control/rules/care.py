"""Transparent lawn care rule orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import (
    CONF_LAWN_TYPE,
    CONF_MAX_GRASS_HEIGHT,
    CONF_MIN_GRASS_HEIGHT,
    CONF_ROBOTIC_MOWER,
    CONF_SHADE_LEVEL,
    CONF_WATER_DURING_DROUGHT,
    DEFAULT_MAX_GRASS_HEIGHT,
    DEFAULT_MIN_GRASS_HEIGHT,
)
from .drought import calculate_drought_risk
from .fertilizer import calculate_fertilizer_score
from .maintenance import calculate_verticut_advice
from .moisture import (
    historical_rain_threshold,
    rain_moisture_threshold,
    rain_moisture_total,
)
from .mowing import (
    calculate_growth_rate,
    calculate_mowing_advice,
    calculate_robot_mower_advice,
)

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData

MODERATE_FORECAST_DRYING_PRESSURE = 0.75
HIGH_FORECAST_DRYING_PRESSURE = 1.5


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
    verticut = calculate_verticut_advice(config, weather, drought, growth, language)
    active_mowing = robot_mower if config.get(CONF_ROBOTIC_MOWER) else mowing
    recommendation = general_recommendation(
        drought,
        fertilizer,
        active_mowing,
        growth,
        language,
        bool(config.get(CONF_ROBOTIC_MOWER)),
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

    forecast_rain = weather.forecast_rain_5_days
    rain_total = rain_moisture_total(weather)
    rain_threshold = rain_moisture_threshold(config)

    if max_height > configured_max_height:
        max_height = configured_max_height
        reasons.append(text["configured_max_cap"])

    if min_height > max_height:
        min_height = max_height

    if not reasons:
        reasons.append(text["configured_height"])

    if rain_total < rain_threshold / 2:
        target = max_height
        reasons.append(text["very_low_rain_height"])
    elif rain_total < rain_threshold:
        target = _round_to_nearest_5((min_height + max_height) / 2)
        reasons.append(text["medium_rain_height"])
    else:
        target = min_height
        reasons.append(text["good_rain_height"])

    target = _apply_forecast_height_protection(
        config,
        weather,
        target,
        min_height,
        max_height,
        reasons,
        text,
    )

    return {
        "value": target,
        "attributes": {
            "min_height": min_height,
            "max_height": max_height,
            "historical_rain": weather.historical_rain,
            "recent_rain_24h": weather.historical_rain_24h,
            "forecast_rain": forecast_rain,
            "forecast_rain_next_24h": weather.forecast_rain_next_24h,
            "forecast_drying_pressure_72h": weather.forecast_drying_pressure_72h,
            "rain_total": rain_total,
            "reason": " ".join(reasons),
        },
    }


def _apply_forecast_height_protection(
    config: dict[str, Any],
    weather: LawnWeatherData,
    target: int,
    min_height: int,
    max_height: int,
    reasons: list[str],
    text: dict[str, str],
) -> int:
    """Protect the lawn from being cut too low before a forecast dry spell."""
    midpoint = _round_to_nearest_5((min_height + max_height) / 2)
    recent_rain = weather.historical_rain_24h or 0.0
    imminent_rain = weather.forecast_rain_next_24h or 0.0
    drying_pressure = weather.forecast_drying_pressure_72h or 0.0
    meaningful_water = historical_rain_threshold(config)
    soil_moisture = weather.soil_moisture

    if soil_moisture is not None and soil_moisture < 25:
        reasons.append(text["low_soil_moisture_height"])
        return max_height

    if recent_rain >= meaningful_water or imminent_rain >= meaningful_water:
        reasons.append(text["near_term_water_height"])
        return min_height

    moisture_protected = (
        config.get(CONF_WATER_DURING_DROUGHT)
        or (soil_moisture is not None and soil_moisture >= 35)
    )
    if drying_pressure >= HIGH_FORECAST_DRYING_PRESSURE and not moisture_protected:
        reasons.append(text["high_forecast_drying_height"])
        return max_height

    if (
        drying_pressure >= MODERATE_FORECAST_DRYING_PRESSURE
        and target == min_height
        and not moisture_protected
    ):
        reasons.append(text["moderate_forecast_drying_height"])
        return midpoint

    return target


def general_recommendation(
    drought: dict[str, Any],
    fertilizer: dict[str, Any],
    mowing: dict[str, Any],
    growth: dict[str, Any],
    language: str,
    robotic_mower: bool = False,
) -> dict[str, Any]:
    """Create a short human-readable recommendation."""
    actions: list[str] = []
    reasons: list[str] = []
    text = _texts(language)

    if drought["value"] in ("high", "critical"):
        actions.append(text["water"])
        reasons.append(text["drought"].format(risk=text[drought["value"]]))
    if mowing["value"]:
        actions.append(text["robot_mow"] if robotic_mower else text["mow"])
        reasons.append(
            text["robot_mow_reason"] if robotic_mower else text["mow_reason"]
        )
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


def _round_to_nearest_5(value: float) -> int:
    """Round a height to the nearest 5 mm."""
    return int(round(value / 5) * 5)


def _texts(language: str) -> dict[str, str]:
    """Return localized rule text."""
    if language.lower().startswith("da"):
        return {
            "water": "Prioriter vanding og undgå at stresse plænen.",
            "drought": "Tørkerisikoen er {risk}.",
            "mow": "Det er egnet at slå græs i dag.",
            "mow_reason": "Forholdene er tørre nok, og væksten understøtter klipning.",
            "robot_mow": "Robotplæneklipperen kan køre i dag.",
            "robot_mow_reason": "Robotklipperens egne stopfaktorer er ikke aktive.",
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
            "very_low_rain_height": "Samlet regn er langt under den valgte fugtgrænse, så målet holdes på maksimumhøjden.",
            "medium_rain_height": "Samlet regn er under den valgte fugtgrænse, så målet flyttes til medianhøjden.",
            "good_rain_height": "Samlet regn opfylder den valgte fugtgrænse, så minimumhøjden er egnet.",
            "near_term_water_height": "Nok regn er faldet eller forventes inden for 24 timer, så en lavere klippehøjde er forsvarlig.",
            "low_soil_moisture_height": "Lav jordfugtighed holder klippehøjden på maksimum for at beskytte plænen.",
            "high_forecast_drying_height": "Sol, varme og begrænset regn de næste tre døgn holder klippehøjden på maksimum.",
            "moderate_forecast_drying_height": "Forventet udtørring de næste tre døgn hæver klippehøjden til midten af intervallet.",
            "configured_max_cap": "Det indtastede maksimum bruges som øvre grænse.",
            "configured_height": "Det konfigurerede højdeinterval passer til de aktuelle forhold.",
        }

    return {
        "water": "Prioritize watering and avoid stressing the lawn.",
        "drought": "Drought risk is {risk}.",
        "mow": "Mowing is suitable today.",
        "mow_reason": "Conditions are dry enough and growth supports regular mowing.",
        "robot_mow": "The robot mower can run today.",
        "robot_mow_reason": "Robot mower-specific blocking factors are clear.",
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
        "very_low_rain_height": "Combined rain is far below the configured moisture threshold, keeping the target at maximum height.",
        "medium_rain_height": "Combined rain is below the configured moisture threshold, moving the target to median height.",
        "good_rain_height": "Combined rain meets the configured moisture threshold, making minimum height suitable.",
        "near_term_water_height": "Enough rain has fallen or is expected within 24 hours, making a lower mowing height suitable.",
        "low_soil_moisture_height": "Low soil moisture keeps the mowing height at maximum to protect the lawn.",
        "high_forecast_drying_height": "Sun, heat and limited rain over the next three days keep the mowing height at maximum.",
        "moderate_forecast_drying_height": "Expected drying over the next three days raises the mowing height to the middle of the range.",
        "configured_max_cap": "The configured maximum is used as the upper limit.",
        "configured_height": "Configured lawn height range is suitable for current conditions.",
    }
