"""Mowing and growth rules."""

from __future__ import annotations

from math import ceil, isfinite
from typing import TYPE_CHECKING, Any

from ..const import (
    CONF_DAYS_SINCE_FERTILIZER,
    CONF_FERTILIZER_K_PERCENT,
    CONF_FERTILIZER_N_PERCENT,
    CONF_FERTILIZER_P_PERCENT,
    CONF_ROBOT_MOWER_ALLOW_NIGHT,
    CONF_ROBOTIC_MOWER,
    CONF_WATER_DURING_DROUGHT,
    CONF_WATERING_LEVEL,
)
from .fertilizer import fertilizer_effect
from .moisture import lacks_moisture_support
from .season import GROWTH_STOP_TEMPERATURE, growth_temperature

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData

RECENT_RAIN_BLOCK_MM = 5


def calculate_growth_rate(
    config: dict[str, Any],
    weather: LawnWeatherData,
    drought: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Estimate grass growth rate."""
    estimated = 1.5
    temperature = growth_temperature(weather)
    reasons: list[str] = []
    text = _texts(language)

    if weather.month in (11, 12, 1, 2):
        estimated = 0.0
        reasons.append(text["winter_growth"])
    elif weather.month in (4, 5, 6, 9):
        estimated += 1.5
        reasons.append(text["active_growth"])
    elif weather.month in (7, 8):
        estimated += 0.5
        reasons.append(text["summer_growth"])

    if temperature is not None:
        if temperature <= GROWTH_STOP_TEMPERATURE:
            estimated = 0.0
            reasons.append(text["cold_growth"])
        elif 12 <= temperature <= 22:
            estimated += 1.0
            reasons.append(text["good_temperature"])
        elif temperature >= 28:
            estimated -= 1.0
            reasons.append(text["hot_growth"])

    weather_adjustment = _weather_growth_adjustment(config, weather, drought)
    if weather_adjustment > 0:
        estimated += weather_adjustment
        reasons.append(text["sun_growth"].format(bonus=weather_adjustment))
    elif weather_adjustment < 0:
        estimated += weather_adjustment
        reasons.append(text["dry_weather_growth"])

    drought_penalty = _drought_growth_penalty(config, drought, weather)
    if drought_penalty:
        estimated -= drought_penalty
        reasons.append(text["drought_growth"].format(risk=text[drought["value"]]))

    watering_bonus = _watering_growth_bonus(config, drought, weather)
    if watering_bonus:
        estimated += watering_bonus
        reasons.append(text["watering_bonus"].format(bonus=watering_bonus))

    fertilizer_effect = _fertilizer_growth_effect(config, drought, weather)
    fertilizer_bonus = fertilizer_effect["bonus"]
    if fertilizer_bonus > 0:
        estimated += fertilizer_bonus
        reasons.append(text["fertilizer_bonus"].format(bonus=fertilizer_bonus))

    # Weather and fertilizer bonuses cannot restart dormant or cold grass.
    temperature = growth_temperature(weather)
    if weather.month in (11, 12, 1, 2) or (
        temperature is not None and temperature <= GROWTH_STOP_TEMPERATURE
    ):
        estimated = 0.0
        fertilizer_bonus = 0.0
    temperature_factor = (
        min(1.0, max(0.0, (temperature - GROWTH_STOP_TEMPERATURE) / 6))
        if temperature is not None
        else 1.0
    )
    estimated *= temperature_factor
    fertilizer_bonus = round(fertilizer_bonus * temperature_factor, 1)
    if 0 < temperature_factor < 1:
        reasons.append(text["cool_growth"])
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

    known = weather.temperature is not None and weather.weather_state is not None
    if not known:
        rate = "unknown"
    fertilizer_days = _fertilizer_days_attributes(config)
    return {
        "value": rate,
        "attributes": {
            "temperature": weather.temperature,
            "historical_temperature": weather.historical_temperature,
            "growth_temperature": temperature,
            "temperature_growth_factor": round(temperature_factor, 2),
            "estimate_known": known,
            "estimated_mm_per_day": estimated if known else None,
            "estimated_mm_next_7_days": round(estimated * 7, 1) if known else None,
            "fertilizer_growth_bonus_mm_per_day": fertilizer_bonus,
            "days_since_fertilizer": fertilizer_days["days_since_fertilizer"],
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
    blocking_factors = mowing_safety_blocks(config, weather, language)
    text = _texts(language)
    if growth["attributes"].get("mowing_season_status", "ready") != "ready":
        blocking_factors.append(growth["attributes"]["mowing_season_label"])

    if drought["value"] in ("high", "critical"):
        blocking_factors.append(text["mow_drought"])

    if (
        weather.recent_hour_rain is not None
        and weather.recent_hour_rain >= RECENT_RAIN_BLOCK_MM
    ):
        blocking_factors.append(text["recent_rain_wet"])

    if weather.forecast_rain is not None and weather.forecast_rain >= 15:
        blocking_factors.append(text["forecast_rain"])

    if weather.weather_state in ("rainy", "pouring", "lightning-rainy", "hail"):
        blocking_factors.append(text["wet_weather"])

    if lacks_moisture_support(config, weather):
        blocking_factors.append(text["mow_low_water"])

    safety_blocks = list(blocking_factors)
    if growth["value"] in ("stopped", "slow"):
        blocking_factors.append(text["limited_growth"])

    recommended = not blocking_factors and growth["value"] in ("normal", "fast")
    if recommended:
        reason = text["mow_ok"]
    else:
        reason = " ".join(blocking_factors) or text["mow_not_needed"]

    return {
        "value": None if not mowing_data_known(weather) else recommended,
        "attributes": {
            "blocking_factors": blocking_factors,
            "safety_blocking_factors": safety_blocks,
            "recommended_height": height["value"],
            "max_cut_fraction": 1 / 3,
            "cutting_guidance": text["one_third"],
            "recent_hour_rain": weather.recent_hour_rain,
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
    blocking_factors = mowing_safety_blocks(config, weather, language)
    text = _texts(language)
    if growth["attributes"].get("mowing_season_status", "ready") != "ready":
        blocking_factors.append(growth["attributes"]["mowing_season_label"])

    if not config.get(CONF_ROBOTIC_MOWER):
        blocking_factors.append(text["robot_disabled"])

    night_mowing_allowed = config.get(CONF_ROBOT_MOWER_ALLOW_NIGHT, False)
    night_window_unknown = False
    if not night_mowing_allowed:
        if weather.sun_is_up is False:
            blocking_factors.append(text["robot_night"])
        elif weather.sun_is_up is None:
            blocking_factors.append(text["robot_sun_unavailable"])
            night_window_unknown = True

    if weather.weather_state in ("rainy", "pouring", "lightning-rainy", "hail"):
        blocking_factors.append(text["robot_weather"])

    if (
        weather.historical_temperature is not None
        and weather.historical_temperature <= GROWTH_STOP_TEMPERATURE
    ):
        blocking_factors.append(text["history_cold"])

    if lacks_moisture_support(config, weather):
        blocking_factors.append(text["robot_low_water"])

    if (
        weather.recent_hour_rain is not None
        and weather.recent_hour_rain >= RECENT_RAIN_BLOCK_MM
    ):
        blocking_factors.append(text["robot_recent_rain"])

    if weather.forecast_rain is not None and weather.forecast_rain >= 8:
        blocking_factors.append(text["robot_forecast"])

    if drought["value"] in ("high", "critical"):
        blocking_factors.append(text["robot_drought"])

    safety_blocks = list(blocking_factors)
    if growth["value"] in ("stopped", "slow"):
        blocking_factors.append(text["robot_limited_growth"])

    allowed = (
        None
        if night_window_unknown or not mowing_data_known(weather)
        else not blocking_factors
    )
    if allowed:
        reason = text["robot_ok"]
    else:
        reason = " ".join(blocking_factors) or text["robot_not_needed"]

    return {
        "value": allowed,
        "attributes": {
            "blocking_factors": blocking_factors,
            "safety_blocking_factors": safety_blocks,
            "night_mowing_allowed": night_mowing_allowed,
            "max_cut_fraction": 1 / 3,
            "cutting_guidance": text["one_third"],
            "recommended_height": mowing["attributes"]["recommended_height"],
            "night_window_known": not night_window_unknown,
            "sun_is_up": weather.sun_is_up,
            "growth_rate": growth["value"],
            "estimated_mm_per_day": growth["attributes"]["estimated_mm_per_day"],
            "historical_rain": weather.historical_rain,
            "forecast_rain": weather.forecast_rain_5_days,
            "recent_hour_rain": weather.recent_hour_rain,
            "reason": reason,
        },
    }


def mowing_data_known(weather: LawnWeatherData) -> bool:
    return (
        weather.temperature is not None
        and weather.weather_state is not None
        and (weather.soil_moisture is not None or weather.historical_rain is not None)
    )


def protect_cut_height(
    height: dict[str, Any], estimated_height: float | None, language: str
) -> None:
    """Limit a tracked lawn's estimated cut to one third, rounded upward."""
    if (
        estimated_height is None
        or not isfinite(estimated_height)
        or estimated_height <= 0
    ):
        return
    safe_height = ceil(estimated_height * 2 / 3 / 5) * 5
    if safe_height <= height["value"]:
        return
    attributes = height["attributes"]
    height["value"] = safe_height
    attributes["min_height"] = max(attributes["min_height"], safe_height)
    attributes["max_height"] = max(attributes["max_height"], safe_height)
    attributes["configured_max_overridden"] = (
        safe_height > attributes["configured_max_height"]
    )
    attributes["estimated_height_before_cut"] = estimated_height
    attributes["gradual_lowering_required"] = True
    attributes["next_cut_min_interval_days"] = 2
    attributes["reason"] += " " + _texts(language)["one_third"]


def mowing_safety_blocks(
    config: dict[str, Any], weather: LawnWeatherData, language: str
) -> list[str]:
    """Shared protection that also applies to catch-up and locked advice."""
    text = _texts(language)
    blocks: list[str] = []
    if weather.temperature is None:
        blocks.append(text["temperature_missing"])
    if weather.weather_state is None:
        blocks.append(text["weather_missing"])
    # Current humidity is only a proxy for dew; do not use yesterday's mean.
    if weather.humidity is not None and weather.humidity >= 95:
        blocks.append(text["dew"])

    if weather.month in (11, 12, 1, 2):
        blocks.append(text["outside_mowing_season"])
    if weather.weather_state in ("snowy", "snowy-rainy") or (
        weather.temperature is not None and weather.temperature <= 0
    ):
        blocks.append(text["frost"])
    temperature = growth_temperature(weather)
    if temperature is not None and temperature <= GROWTH_STOP_TEMPERATURE:
        blocks.append(text["cold_growth"])
    if weather.soil_moisture is not None and weather.soil_moisture < 25:
        blocks.append(text["dry_soil"])
    if (
        (weather.forecast_drying_pressure_72h or 0) >= 1.5
        and not config.get(CONF_WATER_DURING_DROUGHT)
        and (weather.soil_moisture is None or weather.soil_moisture < 35)
    ):
        blocks.append(text["dry_spell"])
    return blocks


def _fertilizer_growth_effect(
    config: dict[str, Any],
    drought: dict[str, Any],
    weather: LawnWeatherData,
) -> dict[str, float]:
    """Return the fertilizer growth factors used by the growth rule."""
    if weather.month in (11, 12, 1, 2):
        return _empty_fertilizer_effect()

    if drought["value"] in ("high", "critical") or lacks_moisture_support(
        config, weather
    ):
        return _empty_fertilizer_effect()

    temperature = growth_temperature(weather)
    if temperature is not None and temperature <= GROWTH_STOP_TEMPERATURE:
        return _empty_fertilizer_effect()

    n = _float_config(config, CONF_FERTILIZER_N_PERCENT)
    p = _float_config(config, CONF_FERTILIZER_P_PERCENT)
    k = _float_config(config, CONF_FERTILIZER_K_PERCENT)
    effect = fertilizer_effect(config)
    if n <= 0 or not effect["application_known"] or not effect["age_factor"]:
        return _empty_fertilizer_effect()

    age_factor = effect["age_factor"] * min(1.0, effect["dose_factor"])
    fertilizer_strength = _fertilizer_strength(n, p, k)
    residual_score = _fertilizer_residual_score(n, p, k, age_factor)
    moisture_factor = _fertilizer_moisture_factor(config, weather)
    balance_factor = 1.0

    if p > 0 and k > 0:
        balance_factor += 0.1
    if n >= 25:
        balance_factor += 0.15

    bonus = min(
        3.0,
        1.8 * fertilizer_strength * balance_factor * age_factor * moisture_factor,
    )
    return {
        "bonus": round(bonus, 1),
        "residual_score": residual_score,
        "fertilizer_strength": round(fertilizer_strength, 2),
        "age_factor": round(age_factor, 2),
        "moisture_factor": round(moisture_factor, 2),
    }


def _empty_fertilizer_effect() -> dict[str, float]:
    """Return a zero fertilizer effect."""
    return {
        "bonus": 0.0,
        "residual_score": 0.0,
        "fertilizer_strength": 0.0,
        "age_factor": 0.0,
        "moisture_factor": 0.0,
    }


def _fertilizer_strength(n: float, p: float, k: float) -> float:
    """Estimate relative growth strength from NPK percentages."""
    nitrogen_factor = min(n / 20, 1.5)
    complete_factor = 1.0
    if p > 0:
        complete_factor += 0.05
    if k > 0:
        complete_factor += 0.05
    return min(1.6, nitrogen_factor * complete_factor)


def _fertilizer_residual_score(
    n: float, p: float, k: float, age_factor: float
) -> float:
    """Estimate remaining fertilizer effect on the same 0-100 scale as the score."""
    strength = min(1.0, _fertilizer_strength(n, p, k))
    return round(100 * strength * age_factor)


def _fertilizer_moisture_factor(
    config: dict[str, Any], weather: LawnWeatherData
) -> float:
    """Estimate whether moisture can activate fertilizer for growth."""
    if weather.soil_moisture is not None:
        if weather.soil_moisture < 25:
            return 0.65
        if weather.soil_moisture > 45:
            return 1.2

    if weather.weather_state in ("rainy", "pouring", "lightning-rainy"):
        return 1.2

    if weather.recent_rain is not None:
        if weather.recent_rain >= 2:
            return 1.2
        if weather.recent_rain == 0:
            return 0.85

    if weather.forecast_rain is not None:
        if 2 <= weather.forecast_rain <= 12:
            return 1.15
        if weather.forecast_rain > 20:
            return 0.9

    if config.get(CONF_WATER_DURING_DROUGHT):
        return 1.1

    return 1.0


def _drought_growth_penalty(
    config: dict[str, Any], drought: dict[str, Any], weather: LawnWeatherData
) -> float:
    """Return drought growth penalty after configured watering is considered."""
    if drought["value"] == "critical":
        base_penalty = 2.0
    elif drought["value"] == "high":
        base_penalty = 1.0
    else:
        return 0.0

    if not config.get(CONF_WATER_DURING_DROUGHT) or lacks_moisture_support(
        config, weather
    ):
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
    if not config.get(CONF_WATER_DURING_DROUGHT) or lacks_moisture_support(
        config, weather
    ):
        return 0.0

    if weather.month in (11, 12, 1, 2):
        return 0.0

    temperature = growth_temperature(weather)
    if temperature is not None and temperature <= GROWTH_STOP_TEMPERATURE:
        return 0.0

    if drought["value"] not in ("medium", "high", "critical"):
        return 0.0

    level = config.get(CONF_WATERING_LEVEL, "normal")
    if level == "high":
        return 0.9
    if level == "low":
        return 0.3
    return 0.6


def _weather_growth_adjustment(
    config: dict[str, Any],
    weather: LawnWeatherData,
    drought: dict[str, Any],
) -> float:
    """Return growth adjustment from sun, temperature and air humidity."""
    if weather.month in (11, 12, 1, 2):
        return 0.0

    temperature = growth_temperature(weather)
    humidity = _first_known(weather.humidity, weather.historical_humidity)
    sun_factor = _sun_factor(weather.weather_state, weather.forecast_condition)
    moisture_missing = lacks_moisture_support(config, weather)

    if temperature is not None and temperature <= GROWTH_STOP_TEMPERATURE:
        return 0.0

    if (
        sun_factor
        and temperature is not None
        and 10 <= temperature <= 24
        and not moisture_missing
        and drought["value"] in ("low", "medium")
    ):
        return 0.4 if sun_factor == "sunny" else 0.2

    if (
        temperature is not None
        and temperature >= 26
        and (moisture_missing or drought["value"] in ("high", "critical"))
    ):
        return -0.6 if sun_factor == "sunny" else -0.4

    if (
        humidity is not None
        and humidity < 40
        and drought["value"]
        in (
            "medium",
            "high",
            "critical",
        )
    ):
        return -0.3

    return 0.0


def _sun_factor(
    weather_state: str | None, forecast_condition: str | None
) -> str | None:
    """Return current or forecast sunlight signal."""
    conditions = {weather_state, forecast_condition}
    if "sunny" in conditions:
        return "sunny"
    if "partlycloudy" in conditions:
        return "partlycloudy"
    return None


def _first_known(*values: float | None) -> float | None:
    """Return the first known numeric value."""
    for value in values:
        if value is not None:
            return value
    return None


def _fertilizer_days_attributes(config: dict[str, Any]) -> dict[str, Any]:
    """Return fertilizer age data used by growth rules."""
    return {
        "days_since_fertilizer": config.get(CONF_DAYS_SINCE_FERTILIZER),
    }


def _float_config(config: dict[str, Any], key: str, default: float = 0.0) -> float:
    """Read a numeric config value."""
    value = config.get(key, default)
    if value in (None, ""):
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _texts(language: str) -> dict[str, str]:
    """Return localized mowing text."""
    if language.lower().startswith("da"):
        return {
            "temperature_missing": "Temperaturdata mangler; klipning kan ikke vurderes.",
            "weather_missing": "Vejrdata mangler; klipning kan ikke vurderes.",
            "dew": "Meget høj aktuel luftfugtighed tyder på dug; vent til græsset er tørt.",
            "outside_mowing_season": "Hold klippepause fra november til februar.",
            "frost": "Undgå klipning ved frost eller sne.",
            "dry_soil": "Målt tør jord kræver klippepause, selv hvis vanding er aktiveret.",
            "dry_spell": "En kommende tør periode kræver klippepause for at bevare bladmasse.",
            "one_third": "Fjern højst en tredjedel af græshøjden pr. klipning; sænk højden gradvist over flere klipninger.",
            "cool_growth": "Køligt vejr reducerer væksten gradvist mellem 6 og 12 °C.",
            "winter_growth": "Vinterforhold stopper normalt synlig vækst.",
            "active_growth": "Sæsonen understøtter aktiv vækst.",
            "summer_growth": "Sommervækst afhænger meget af fugt.",
            "cold_growth": "Lav temperatur stopper væksten.",
            "good_temperature": "Temperaturen er gunstig for vækst.",
            "hot_growth": "Høj temperatur kan bremse væksten.",
            "sun_growth": "Sol og passende temperatur tilføjer ca. {bonus} mm/dag, når fugten er tilstrækkelig.",
            "dry_weather_growth": "Sol, varme eller tør luft begrænser væksten, når fugten er lav.",
            "drought_growth": "{risk} tørkerisiko begrænser væksten.",
            "watering_bonus": "Vanding i tørre perioder understøtter ca. {bonus} mm/dag.",
            "fertilizer_bonus": "Nylig NPK-gødning tilføjer ca. {bonus} mm/dag.",
            "default_growth": "Standardestimatet for vækst er baseret på den aktuelle sæson.",
            "mow_drought": "Undgå græsslåning under betydelig tørkestress.",
            "recent_rain_wet": "5 mm eller mere regn den seneste time kan efterlade græsset for vådt.",
            "forecast_rain": "Kraftig forecast-regn gør græsslåning mindre egnet.",
            "wet_weather": "Det aktuelle vejr er vådt.",
            "mow_low_water": "Den observerede fugt er for lav til græsslåning.",
            "limited_growth": "Væksten er for begrænset til at retfærdiggøre græsslåning.",
            "mow_ok": "Ingen kendte stopgrunde; klip kun, hvis græsset er tørt.",
            "mow_not_needed": "Græsslåning er ikke nødvendig i dag.",
            "robot_disabled": "Robotplæneklipper er ikke aktiveret i integrationen.",
            "robot_night": "Robotklipning om natten er slået fra.",
            "robot_sun_unavailable": "Solens status er utilgængelig, så natblokeringen kan ikke vurderes.",
            "robot_weather": "Det aktuelle vejr er ikke egnet til robotklipning.",
            "history_humidity": "Fugtighedshistorikken tyder på langsom tørring.",
            "history_cold": "Temperaturhistorikken er for kold til klipning.",
            "robot_low_water": "Den observerede fugt er for lav til robotklipning.",
            "robot_recent_rain": "5 mm eller mere regn den seneste time kan efterlade græsset for vådt til robotklipning.",
            "robot_forecast": "Forecast-regn gør robotklipning mindre egnet.",
            "robot_drought": "Undgå robotklipning under betydelig tørkestress.",
            "robot_limited_growth": "Væksten er for begrænset til robotklipning.",
            "robot_not_needed": "Robotklipning er ikke nødvendig lige nu.",
            "robot_ok": "Robotklipning er tilladt, fordi klippeforholdene er egnede.",
            "low": "lav",
            "medium": "mellem",
            "high": "høj",
            "critical": "kritisk",
        }

    return {
        "temperature_missing": "Temperature data is missing; mowing cannot be assessed.",
        "weather_missing": "Weather data is missing; mowing cannot be assessed.",
        "dew": "Very high current humidity suggests dew; wait until the grass is dry.",
        "outside_mowing_season": "Pause mowing from November through February.",
        "frost": "Avoid mowing in frost or snow.",
        "dry_soil": "Measured dry soil requires a mowing pause even when watering is enabled.",
        "dry_spell": "An approaching dry spell requires a mowing pause to preserve leaf area.",
        "one_third": "Remove at most one third of the grass height per cut; lower the height gradually over multiple cuts.",
        "cool_growth": "Cool weather gradually reduces growth between 6 and 12 °C.",
        "winter_growth": "Winter conditions usually stop visible growth.",
        "active_growth": "The season supports active growth.",
        "summer_growth": "Summer growth depends strongly on moisture.",
        "cold_growth": "Low temperature stops growth.",
        "good_temperature": "Temperature is favorable for growth.",
        "hot_growth": "High temperature can slow growth.",
        "sun_growth": "Sun and suitable temperature add about {bonus} mm/day when moisture is sufficient.",
        "dry_weather_growth": "Sun, heat or dry air limits growth when moisture is low.",
        "drought_growth": "{risk} drought risk limits growth.",
        "watering_bonus": "Watering during dry periods supports about {bonus} mm/day.",
        "fertilizer_bonus": "Recent NPK fertilizer adds about {bonus} mm/day.",
        "default_growth": "Default growth estimate is based on current season.",
        "mow_drought": "Avoid mowing during significant drought stress.",
        "recent_rain_wet": "5 mm or more rain in the last hour can leave the grass too wet.",
        "forecast_rain": "Heavy forecast rain makes mowing less suitable.",
        "wet_weather": "Current weather is wet.",
        "mow_low_water": "Observed moisture is too low for mowing.",
        "limited_growth": "Growth is too limited to justify mowing.",
        "mow_ok": "No known blocking factors; mow only if the grass is dry.",
        "mow_not_needed": "Mowing is not needed today.",
        "robot_disabled": "Robotic mower is not enabled in this integration.",
        "robot_night": "Robot mowing at night is disabled.",
        "robot_sun_unavailable": "Sun status is unavailable, so the night mowing block cannot be evaluated.",
        "robot_weather": "Current weather is not suitable for robot mowing.",
        "history_humidity": "Recent humidity history suggests slow drying.",
        "history_cold": "Recent temperature history is too cold for mowing.",
        "robot_low_water": "Observed moisture is too low for robot mowing.",
        "robot_recent_rain": "5 mm or more rain in the last hour can leave grass too wet for robot mowing.",
        "robot_forecast": "Forecast rain makes robot mowing less suitable.",
        "robot_drought": "Avoid robot mowing during significant drought stress.",
        "robot_limited_growth": "Growth is too limited for robot mowing.",
        "robot_not_needed": "Robot mowing is not needed right now.",
        "robot_ok": "Robot mowing is allowed because mowing conditions are suitable.",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "critical": "critical",
    }


def mowing_frequency(
    config: dict[str, Any],
    weather: LawnWeatherData,
    growth: dict[str, Any],
    language: str,
    drought: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recommend cuts per week independently of the advice refresh interval."""
    robot = bool(config.get(CONF_ROBOTIC_MOWER))
    known = growth["attributes"].get("estimate_known", True)
    paused = (
        growth["attributes"].get("mowing_season_status") != "ready"
        or (drought is not None and drought["value"] in ("high", "critical"))
        or (weather.soil_moisture is not None and weather.soil_moisture < 25)
    )
    if not known:
        cuts = None
    elif paused or growth["value"] == "stopped":
        cuts = 0
    elif growth["value"] == "slow":
        cuts = 1
    elif robot:
        cuts = 3 if weather.month in (3, 4, 10) else 7
    else:
        cuts = 3 if growth["value"] == "fast" else 2
    if language.lower().startswith("da"):
        guidance = (
            "Afventer data til klippefrekvens."
            if cuts is None
            else "Hold klippepause."
            if cuts == 0
            else "Lav vækst: klip kun ved behov, højst én gang om ugen."
            if cuts == 1
            else f"Sigt efter {cuts} klippedage om ugen, når græsset er tørt og forholdene tillader det."
        )
    else:
        guidance = (
            "Waiting for data to assess mowing frequency."
            if cuts is None
            else "Pause mowing."
            if cuts == 0
            else "Limited growth: mow only if needed, at most once a week."
            if cuts == 1
            else f"Aim for {cuts} mowing days per week when grass is dry and conditions permit."
        )
    return {
        "value": cuts,
        "attributes": {
            "recommended_cuts_per_week": cuts,
            "min_interval_days": round(7 / cuts, 2) if cuts else None,
            "frequency_guidance": guidance,
            "reason": guidance,
            "growth_rate": growth["value"],
        },
    }


def gradual_cut_plan(current: float, target: float) -> list[int]:
    """Plan safe settings; each step is separated by at least two days."""
    plan: list[int] = []
    if not isfinite(current) or current <= 0 or not isfinite(target) or target <= 0:
        return plan
    while current > target:
        next_height = max(ceil(current * 2 / 3 / 5) * 5, ceil(target))
        # At tiny heights rounding must never trap the planner.
        if next_height >= current:
            break
        plan.append(next_height)
        current = next_height
    return plan
