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
) -> dict[str, Any]:
    """Estimate grass growth rate."""
    estimated = 1.5
    reasons: list[str] = []

    if weather.month in (12, 1, 2):
        estimated = 0.0
        reasons.append("Winter conditions usually stop visible growth.")
    elif weather.month in (4, 5, 6, 9):
        estimated += 1.5
        reasons.append("The season supports active growth.")
    elif weather.month in (7, 8):
        estimated += 0.5
        reasons.append("Summer growth depends strongly on moisture.")

    if weather.temperature is not None:
        if weather.temperature < 6:
            estimated = 0.0
            reasons.append("Low temperature stops growth.")
        elif 12 <= weather.temperature <= 22:
            estimated += 1.0
            reasons.append("Temperature is favorable for growth.")
        elif weather.temperature >= 28:
            estimated -= 1.0
            reasons.append("High temperature can slow growth.")

    drought_penalty = _drought_growth_penalty(config, drought)
    if drought_penalty:
        estimated -= drought_penalty
        reasons.append(
            f"{drought['value'].capitalize()} drought risk limits growth."
        )

    watering_bonus = _watering_growth_bonus(config, drought, weather)
    if watering_bonus:
        estimated += watering_bonus
        reasons.append(
            f"Watering during dry periods supports about {watering_bonus} mm/day."
        )

    fertilizer_bonus = _fertilizer_growth_bonus(config, drought, weather)
    if fertilizer_bonus > 0:
        estimated += fertilizer_bonus
        reasons.append(
            f"Recent NPK fertilizer adds about {fertilizer_bonus} mm/day."
        )

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
        reasons.append("Default growth estimate is based on current season.")

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
) -> dict[str, Any]:
    """Decide whether mowing is suitable now."""
    blocking_factors: list[str] = []

    if drought["value"] in ("high", "critical"):
        blocking_factors.append("Avoid mowing during significant drought stress.")

    if weather.recent_rain is not None and weather.recent_rain >= 8:
        blocking_factors.append("Recent rain may leave the grass too wet.")

    if weather.forecast_rain is not None and weather.forecast_rain >= 15:
        blocking_factors.append("Heavy forecast rain makes mowing less suitable.")

    if weather.weather_state in ("rainy", "pouring", "lightning-rainy"):
        blocking_factors.append("Current weather is wet.")

    if growth["value"] in ("stopped", "slow"):
        blocking_factors.append("Growth is too limited to justify mowing.")

    recommended = not blocking_factors and growth["value"] in ("normal", "fast")
    if recommended:
        reason = "Conditions are dry enough and growth supports regular mowing."
    else:
        reason = " ".join(blocking_factors) or "Mowing is not needed today."

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
) -> dict[str, Any]:
    """Decide whether a robot mower should be allowed to run."""
    blocking_factors: list[str] = []

    if not config.get(CONF_ROBOTIC_MOWER):
        blocking_factors.append("Robotic mower is not enabled in this integration.")

    if weather.weather_state in ("rainy", "pouring", "lightning-rainy", "hail"):
        blocking_factors.append("Current weather is not suitable for robot mowing.")

    if weather.recent_rain is not None and weather.recent_rain >= 5:
        blocking_factors.append("Recent rain can leave grass too wet for robot mowing.")

    if weather.forecast_rain is not None and weather.forecast_rain >= 8:
        blocking_factors.append("Forecast rain makes robot mowing less suitable.")

    if drought["value"] in ("high", "critical"):
        blocking_factors.append("Avoid robot mowing during significant drought stress.")

    if growth["value"] == "stopped":
        blocking_factors.append("Growth is stopped, so robot mowing is unnecessary.")

    allowed = not blocking_factors and mowing["value"]
    if allowed:
        reason = "Robot mowing is allowed because mowing conditions are suitable."
    else:
        reason = " ".join(blocking_factors) or mowing["attributes"]["reason"]

    return {
        "value": allowed,
        "attributes": {
            "blocking_factors": blocking_factors,
            "growth_rate": growth["value"],
            "estimated_mm_per_day": growth["attributes"]["estimated_mm_per_day"],
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
