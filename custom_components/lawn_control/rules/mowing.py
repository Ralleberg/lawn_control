"""Mowing and growth rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import (
    CONF_DAYS_SINCE_FERTILIZER,
    CONF_FERTILIZER_K_PERCENT,
    CONF_FERTILIZER_N_PERCENT,
    CONF_FERTILIZER_P_PERCENT,
    CONF_ROBOTIC_MOWER,
    CONF_WATER_DURING_DROUGHT,
    CONF_WATERING_LEVEL,
)

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData


def calculate_growth_rate(
    config: dict[str, Any],
    weather: LawnWeatherData,
    drought: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Estimate grass growth rate."""
    estimated = 1.5
    reasons: list[str] = []
    text = _texts(language)

    if weather.month in (12, 1, 2):
        estimated = 0.0
        reasons.append(text["winter_growth"])
    elif weather.month in (4, 5, 6, 9):
        estimated += 1.5
        reasons.append(text["active_growth"])
    elif weather.month in (7, 8):
        estimated += 0.5
        reasons.append(text["summer_growth"])

    if weather.temperature is not None:
        if weather.temperature < 6:
            estimated = 0.0
            reasons.append(text["cold_growth"])
        elif 12 <= weather.temperature <= 22:
            estimated += 1.0
            reasons.append(text["good_temperature"])
        elif weather.temperature >= 28:
            estimated -= 1.0
            reasons.append(text["hot_growth"])

    drought_penalty = _drought_growth_penalty(config, drought)
    if drought_penalty:
        estimated -= drought_penalty
        reasons.append(text["drought_growth"].format(risk=text[drought["value"]]))

    watering_bonus = _watering_growth_bonus(config, drought, weather)
    if watering_bonus:
        estimated += watering_bonus
        reasons.append(text["watering_bonus"].format(bonus=watering_bonus))

    fertilizer_bonus = _fertilizer_growth_bonus(config, drought, weather)
    if fertilizer_bonus > 0:
        estimated += fertilizer_bonus
        reasons.append(text["fertilizer_bonus"].format(bonus=fertilizer_bonus))

    estimated = max(0.0, round(estimated, 1))
    if estimated == 0:
        rate = "stopped"
    elif estimated < 1.5:
        rate = "slow"
    elif estimated <= 3.5:
        rate = "normal"
    else:
        rate = "fast"

    if not reasons:
        reasons.append(text["default_growth"])

    return {
        "value": rate,
        "attributes": {
            "estimated_mm_per_day": estimated,
            "estimated_mm_next_7_days": round(estimated * 7, 1),
            "watering_growth_bonus_mm_per_day": watering_bonus,
            "fertilizer_growth_bonus_mm_per_day": fertilizer_bonus,
            "watering_effect": _watering_effect_attributes(config),
            "npk_effect": _npk_effect_attributes(config),
            "reason": " ".join(reasons),
        },
    }


def calculate_mowing_advice(
    config: dict[str, Any],
    weather: LawnWeatherData,
    drought: dict[str, Any],
    growth: dict[str, Any],
    height: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Decide whether mowing is suitable now."""
    blocking_factors: list[str] = []
    text = _texts(language)

    if drought["value"] in ("high", "critical"):
        blocking_factors.append(text["mow_drought"])

    if weather.recent_rain is not None and weather.recent_rain >= 8:
        blocking_factors.append(text["recent_rain_wet"])

    if weather.forecast_rain is not None and weather.forecast_rain >= 15:
        blocking_factors.append(text["forecast_rain"])

    if weather.weather_state in ("rainy", "pouring", "lightning-rainy"):
        blocking_factors.append(text["wet_weather"])

    if growth["value"] in ("stopped", "slow"):
        blocking_factors.append(text["limited_growth"])

    recommended = not blocking_factors and growth["value"] in ("normal", "fast")
    if recommended:
        reason = text["mow_ok"]
    else:
        reason = " ".join(blocking_factors) or text["mow_not_needed"]

    return {
        "value": recommended,
        "attributes": {
            "blocking_factors": blocking_factors,
            "recommended_min_height": height["attributes"]["min_height"],
            "recommended_max_height": height["attributes"]["max_height"],
            "reason": reason,
        },
    }


def calculate_robot_mower_advice(
    config: dict[str, Any],
    weather: LawnWeatherData,
    drought: dict[str, Any],
    growth: dict[str, Any],
    mowing: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Decide whether a robot mower should be allowed to run."""
    blocking_factors: list[str] = []
    text = _texts(language)

    if not config.get(CONF_ROBOTIC_MOWER):
        blocking_factors.append(text["robot_disabled"])

    if weather.weather_state in ("rainy", "pouring", "lightning-rainy", "hail"):
        blocking_factors.append(text["robot_weather"])

    if weather.historical_rain is not None and weather.historical_rain >= 5:
        blocking_factors.append(text["history_rain"])

    if weather.historical_humidity is not None and weather.historical_humidity >= 90:
        blocking_factors.append(text["history_humidity"])

    if weather.historical_temperature is not None and weather.historical_temperature < 6:
        blocking_factors.append(text["history_cold"])

    if weather.recent_rain is not None and weather.recent_rain >= 5:
        blocking_factors.append(text["robot_recent_rain"])

    if weather.forecast_rain is not None and weather.forecast_rain >= 8:
        blocking_factors.append(text["robot_forecast"])

    if drought["value"] in ("high", "critical"):
        blocking_factors.append(text["robot_drought"])

    if growth["value"] == "stopped":
        blocking_factors.append(text["robot_stopped"])

    allowed = not blocking_factors and mowing["value"]
    if allowed:
        reason = text["robot_ok"]
    else:
        reason = " ".join(blocking_factors) or mowing["attributes"]["reason"]

    return {
        "value": allowed,
        "attributes": {
            "blocking_factors": blocking_factors,
            "growth_rate": growth["value"],
            "estimated_mm_per_day": growth["attributes"]["estimated_mm_per_day"],
            "historical_temperature": weather.historical_temperature,
            "historical_humidity": weather.historical_humidity,
            "historical_rain": weather.historical_rain,
            "reason": reason,
        },
    }


def _fertilizer_growth_bonus(
    config: dict[str, Any],
    drought: dict[str, Any],
    weather: LawnWeatherData,
) -> float:
    """Estimate extra daily growth from recent NPK fertilizer."""
    if weather.month in (12, 1, 2):
        return 0.0

    if drought["value"] in ("high", "critical"):
        return 0.0

    if weather.temperature is not None and weather.temperature < 6:
        return 0.0

    n = _float_config(config, CONF_FERTILIZER_N_PERCENT)
    p = _float_config(config, CONF_FERTILIZER_P_PERCENT)
    k = _float_config(config, CONF_FERTILIZER_K_PERCENT)
    days = _float_config(config, CONF_DAYS_SINCE_FERTILIZER, default=90)
    if n <= 0 or days > 60:
        return 0.0

    age_factor = max(0.0, 1 - (days / 60))
    nitrogen_factor = min(n / 20, 1.5)
    balance_factor = 1.0

    if p > 0 and k > 0:
        balance_factor += 0.1
    if n >= 25:
        balance_factor += 0.15

    return round(min(2.5, 1.4 * nitrogen_factor * balance_factor * age_factor), 1)


def _drought_growth_penalty(config: dict[str, Any], drought: dict[str, Any]) -> float:
    """Return drought growth penalty after configured watering is considered."""
    if drought["value"] == "critical":
        base_penalty = 2.0
    elif drought["value"] == "high":
        base_penalty = 1.0
    else:
        return 0.0

    if not config.get(CONF_WATER_DURING_DROUGHT):
        return base_penalty

    level = config.get(CONF_WATERING_LEVEL, "normal")
    if level == "high":
        return round(base_penalty * 0.25, 1)
    if level == "low":
        return round(base_penalty * 0.65, 1)
    return round(base_penalty * 0.4, 1)


def _watering_growth_bonus(
    config: dict[str, Any],
    drought: dict[str, Any],
    weather: LawnWeatherData,
) -> float:
    """Estimate growth support from watering during dry periods."""
    if not config.get(CONF_WATER_DURING_DROUGHT):
        return 0.0

    if weather.month in (12, 1, 2):
        return 0.0

    if weather.temperature is not None and weather.temperature < 6:
        return 0.0

    if drought["value"] not in ("medium", "high", "critical"):
        return 0.0

    level = config.get(CONF_WATERING_LEVEL, "normal")
    if level == "high":
        return 0.9
    if level == "low":
        return 0.3
    return 0.6


def _npk_effect_attributes(config: dict[str, Any]) -> dict[str, Any]:
    """Return transparent NPK inputs used by growth rules."""
    return {
        "n_percent": _float_config(config, CONF_FERTILIZER_N_PERCENT),
        "p_percent": _float_config(config, CONF_FERTILIZER_P_PERCENT),
        "k_percent": _float_config(config, CONF_FERTILIZER_K_PERCENT),
        "days_since_fertilizer": _float_config(
            config, CONF_DAYS_SINCE_FERTILIZER, default=90
        ),
    }


def _watering_effect_attributes(config: dict[str, Any]) -> dict[str, Any]:
    """Return transparent watering inputs used by growth rules."""
    return {
        "water_during_drought": bool(config.get(CONF_WATER_DURING_DROUGHT)),
        "watering_level": config.get(CONF_WATERING_LEVEL, "normal"),
    }


def _float_config(config: dict[str, Any], key: str, default: float = 0.0) -> float:
    """Read a numeric config value."""
    try:
        return float(config.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _texts(language: str) -> dict[str, str]:
    """Return localized mowing text."""
    if language.lower().startswith("da"):
        return {
            "winter_growth": "Vinterforhold stopper normalt synlig vækst.",
            "active_growth": "Sæsonen understøtter aktiv vækst.",
            "summer_growth": "Sommervækst afhænger meget af fugt.",
            "cold_growth": "Lav temperatur stopper væksten.",
            "good_temperature": "Temperaturen er gunstig for vækst.",
            "hot_growth": "Høj temperatur kan bremse væksten.",
            "drought_growth": "{risk} tørkerisiko begrænser væksten.",
            "watering_bonus": "Vanding i tørre perioder understøtter ca. {bonus} mm/dag.",
            "fertilizer_bonus": "Nylig NPK-gødning tilføjer ca. {bonus} mm/dag.",
            "default_growth": "Standardestimatet for vækst er baseret på den aktuelle sæson.",
            "mow_drought": "Undgå græsslåning under betydelig tørkestress.",
            "recent_rain_wet": "Nylig regn kan efterlade græsset for vådt.",
            "forecast_rain": "Kraftig forecast-regn gør græsslåning mindre egnet.",
            "wet_weather": "Det aktuelle vejr er vådt.",
            "limited_growth": "Væksten er for begrænset til at retfærdiggøre græsslåning.",
            "mow_ok": "Forholdene er tørre nok, og væksten understøtter græsslåning.",
            "mow_not_needed": "Græsslåning er ikke nødvendig i dag.",
            "robot_disabled": "Robotplæneklipper er ikke aktiveret i integrationen.",
            "robot_weather": "Det aktuelle vejr er ikke egnet til robotklipning.",
            "history_rain": "Regnhistorikken tyder på vådt græs.",
            "history_humidity": "Fugtighedshistorikken tyder på langsom tørring.",
            "history_cold": "Temperaturhistorikken er for kold til klipning.",
            "robot_recent_rain": "Nylig regn kan efterlade græsset for vådt til robotklipning.",
            "robot_forecast": "Forecast-regn gør robotklipning mindre egnet.",
            "robot_drought": "Undgå robotklipning under betydelig tørkestress.",
            "robot_stopped": "Væksten er stoppet, så robotklipning er unødvendig.",
            "robot_ok": "Robotklipning er tilladt, fordi klippeforholdene er egnede.",
            "low": "lav",
            "medium": "mellem",
            "high": "høj",
            "critical": "kritisk",
        }

    return {
        "winter_growth": "Winter conditions usually stop visible growth.",
        "active_growth": "The season supports active growth.",
        "summer_growth": "Summer growth depends strongly on moisture.",
        "cold_growth": "Low temperature stops growth.",
        "good_temperature": "Temperature is favorable for growth.",
        "hot_growth": "High temperature can slow growth.",
        "drought_growth": "{risk} drought risk limits growth.",
        "watering_bonus": "Watering during dry periods supports about {bonus} mm/day.",
        "fertilizer_bonus": "Recent NPK fertilizer adds about {bonus} mm/day.",
        "default_growth": "Default growth estimate is based on current season.",
        "mow_drought": "Avoid mowing during significant drought stress.",
        "recent_rain_wet": "Recent rain may leave the grass too wet.",
        "forecast_rain": "Heavy forecast rain makes mowing less suitable.",
        "wet_weather": "Current weather is wet.",
        "limited_growth": "Growth is too limited to justify mowing.",
        "mow_ok": "Conditions are dry enough and growth supports regular mowing.",
        "mow_not_needed": "Mowing is not needed today.",
        "robot_disabled": "Robotic mower is not enabled in this integration.",
        "robot_weather": "Current weather is not suitable for robot mowing.",
        "history_rain": "Recent rain history suggests wet grass.",
        "history_humidity": "Recent humidity history suggests slow drying.",
        "history_cold": "Recent temperature history is too cold for mowing.",
        "robot_recent_rain": "Recent rain can leave grass too wet for robot mowing.",
        "robot_forecast": "Forecast rain makes robot mowing less suitable.",
        "robot_drought": "Avoid robot mowing during significant drought stress.",
        "robot_stopped": "Growth is stopped, so robot mowing is unnecessary.",
        "robot_ok": "Robot mowing is allowed because mowing conditions are suitable.",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "critical": "critical",
    }
