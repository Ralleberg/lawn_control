"""Separate product age, application conditions and uncertain nutrient need."""

from __future__ import annotations

from math import isfinite
from typing import TYPE_CHECKING, Any

from ..const import (
    CONF_DAYS_SINCE_FERTILIZER,
    CONF_FERTILIZER_DOSE,
    CONF_FERTILIZER_EFFECT_DAYS,
    CONF_FERTILIZER_LABEL_DOSE,
    CONF_FERTILIZER_N_PERCENT,
    CONF_FERTILIZER_TYPE,
    DEFAULT_FERTILIZER_EFFECT_DAYS,
    DEFAULT_FERTILIZER_NEED_THRESHOLD,
)
from .moisture import lacks_moisture_support, moisture_status
from .season import growth_temperature

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData


def fertilizer_effect(config: dict[str, Any]) -> dict[str, Any]:
    """Estimate residual effect; elapsed time is not proof of nutrient deficiency."""
    duration = max(
        1.0,
        _float_config(
            config, CONF_FERTILIZER_EFFECT_DAYS, DEFAULT_FERTILIZER_EFFECT_DAYS
        ),
    )
    days = _float_config(config, CONF_DAYS_SINCE_FERTILIZER, -1)
    dose = _float_config(config, CONF_FERTILIZER_DOSE)
    label_dose = _float_config(config, CONF_FERTILIZER_LABEL_DOSE)
    nitrogen = _float_config(config, CONF_FERTILIZER_N_PERCENT)
    known = days >= 0 and dose > 0 and label_dose > 0 and nitrogen > 0
    age_factor = max(0.0, 1 - days / duration) if days >= 0 else 0.0
    dose_factor = min(2.0, dose / label_dose) if label_dose > 0 else 0.0
    return {
        "effect_days": duration,
        "days_since_fertilizer": days if days >= 0 else None,
        "dose_g_m2": dose or None,
        "label_dose_g_m2": label_dose or None,
        "nitrogen_g_m2": round(dose * nitrogen / 100, 2) if known else None,
        "application_known": known,
        "age_factor": age_factor,
        "dose_factor": dose_factor,
        "score": min(100, round(100 * age_factor * dose_factor)) if known else None,
        "application_interval_elapsed": known and days >= duration,
    }


def calculate_fertilizer_score(
    config: dict[str, Any],
    weather: LawnWeatherData,
    drought: dict[str, Any],
    growth: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Recommend consideration only after label interval and active growth checks."""
    text = _texts(language)
    blocks: list[str] = []
    missing: list[str] = []
    product = config.get(CONF_FERTILIZER_TYPE, "regular")
    permitted_months = (9, 10) if product == "autumn" else (3, 4, 5, 6, 7, 8, 9)
    if weather.month not in permitted_months:
        blocks.append(
            text["autumn_window"] if product == "autumn" else text["regular_window"]
        )
    if growth["value"] in ("stopped", "slow"):
        blocks.append(text["limited_growth"])
    temperature = growth_temperature(weather)
    if (
        temperature is None
        or weather.temperature is None
        or weather.weather_state is None
    ):
        missing.append(text["temperature_missing"])
    elif temperature < 10:
        blocks.append(text["cold"])
    if (
        weather.historical_temperature is None
        or getattr(weather, "historical_temperature_coverage_hours", 0) < 18
    ):
        missing.append(text["history_missing"])
    forecast_mean = getattr(weather, "forecast_temperature_72h", None)
    forecast_min = getattr(weather, "forecast_min_temperature_72h", None)
    if (
        forecast_mean is None
        or getattr(weather, "forecast_temperature_coverage_hours", 0) < 48
    ):
        missing.append(text["forecast_missing"])
    elif forecast_mean < 10 or (forecast_min is not None and forecast_min <= 0):
        blocks.append(text["cold_forecast"])
    if weather.weather_state in ("snowy", "snowy-rainy", "hail"):
        blocks.append(text["cold"])
    if weather.temperature is not None and weather.temperature >= 28:
        blocks.append(text["heat"])
    if drought["value"] in ("high", "critical"):
        blocks.append(text["drought"])
    if lacks_moisture_support(config, weather):
        if weather.soil_moisture is None and weather.historical_rain is None:
            missing.append(text["moisture_missing"])
        else:
            blocks.append(text["dry"])
    if (weather.forecast_rain_next_24h or 0) >= 20:
        blocks.append(text["heavy_rain"])
    effect = fertilizer_effect(config)
    if not effect["application_known"]:
        missing.append(text["application_missing"])
    elif not effect["application_interval_elapsed"]:
        blocks.append(text["effect_remaining"])
    recommended = False if blocks else None if missing else True
    reason = (
        " ".join(dict.fromkeys(blocks + missing))
        if blocks or missing
        else text["consider"]
    )
    return {
        "score": effect["score"],
        "threshold": DEFAULT_FERTILIZER_NEED_THRESHOLD,
        "recommended": recommended,
        "blocking_factors": blocks,
        "reason": reason,
        "attributes": {
            **effect,
            "fertilizer_type": product,
            "need_confirmed": False,
            "conditions_suitable": not blocks
            and not any(m != text["application_missing"] for m in missing),
            "assessment_known": recommended is not None,
            "missing_information": missing,
            "threshold": DEFAULT_FERTILIZER_NEED_THRESHOLD,
            "historical_rain": weather.historical_rain,
            "forecast_rain": weather.forecast_rain_5_days,
            "moisture_status": moisture_status(config, weather, language),
            "blocking_factors": blocks,
            "reason": reason,
        },
    }


def _float_config(config: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        value = float(config.get(key, default))
        return value if isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _texts(language: str) -> dict[str, str]:
    if language.lower().startswith("da"):
        return {
            "regular_window": "Vent med almindelig gødning; tidspunktet er uden for marts–september.",
            "autumn_window": "Efterårsgødning vurderes kun i september og oktober efter produktets anvisning.",
            "limited_growth": "Vent med gødning; væksten er for lav til at udnytte den godt.",
            "cold": "Kulde eller frost taler imod gødning.",
            "cold_forecast": "Kommende kulde eller frost taler imod gødning.",
            "temperature_missing": "Temperaturdata mangler til vurdering af gødning.",
            "history_missing": "Der mangler tilstrækkelig temperaturhistorik fra det seneste døgn.",
            "forecast_missing": "Der mangler temperaturprognose for mindst to af de kommende tre døgn.",
            "heat": "Vent med gødning i stærk varme.",
            "drought": "Vent med gødning under tørkestress.",
            "dry": "Vent med gødning, indtil jorden har tilstrækkelig fugt.",
            "moisture_missing": "Jordfugt eller observeret regn mangler til vurdering af gødning.",
            "heavy_rain": "Kraftig regn det næste døgn taler imod gødning.",
            "application_missing": "Registrér seneste gødskning, N-indhold, tilført mængde og dosering på etiketten; næringsbehovet er ukendt.",
            "effect_remaining": "Vent med mere gødning; produktets angivne virkningsperiode er ikke udløbet.",
            "consider": "Gødning kan overvejes, hvis plænen har behov. Følg produktets dosering; udløbet virkning dokumenterer ikke næringsmangel.",
        }
    return {
        "regular_window": "Wait before applying regular fertilizer; outside March–September.",
        "autumn_window": "Autumn fertilizer is assessed only in September and October, following the product label.",
        "limited_growth": "Wait before fertilizing; growth is too limited to use it well.",
        "cold": "Cold or frost makes fertilizing unsuitable.",
        "cold_forecast": "Upcoming cold or frost makes fertilizing unsuitable.",
        "temperature_missing": "Temperature data is missing for fertilizer advice.",
        "history_missing": "Insufficient temperature history over the last day.",
        "forecast_missing": "Temperature forecast must cover at least two of the next three days.",
        "heat": "Wait before fertilizing in strong heat.",
        "drought": "Wait before fertilizing during drought stress.",
        "dry": "Wait before fertilizing until the soil has sufficient moisture.",
        "moisture_missing": "Soil moisture or observed rain is missing for fertilizer advice.",
        "heavy_rain": "Heavy rain over the next day makes fertilizing unsuitable.",
        "application_missing": "Record the last application, nitrogen content, applied dose and label dose; nutrient need is unknown.",
        "effect_remaining": "Wait before applying more fertilizer; the product's stated effect period has not elapsed.",
        "consider": "Consider fertilizer if the lawn needs it. Follow the product dose; elapsed effect does not prove nutrient deficiency.",
    }
