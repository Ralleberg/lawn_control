"""Seasonal maintenance rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import (
    CONF_WATER_DURING_DROUGHT,
    FORECAST_RAIN_OK_MM,
    HISTORICAL_RAIN_OK_MM,
)

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData


VERTICUT_MONTHS = (4, 5, 6, 8, 9, 10)


def calculate_verticut_advice(
    config: dict[str, Any],
    weather: LawnWeatherData,
    drought: dict[str, Any],
    growth: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Decide whether conditions are suitable for scarifying the lawn."""
    blocking_factors: list[str] = []
    text = _texts(language)

    if weather.month not in VERTICUT_MONTHS:
        blocking_factors.append(text["outside_season"])

    if drought["value"] in ("high", "critical"):
        blocking_factors.append(text["drought"])

    if growth["value"] == "stopped":
        blocking_factors.append(text["growth_stopped"])

    if _soil_too_dry(config, weather):
        blocking_factors.append(text["dry_soil"])

    if weather.recent_rain is not None and weather.recent_rain >= 8:
        blocking_factors.append(text["wet_lawn"])

    if weather.forecast_rain is not None and weather.forecast_rain > 20:
        blocking_factors.append(text["heavy_rain"])

    recommended = not blocking_factors
    reason = text["ok"] if recommended else " ".join(blocking_factors)

    return {
        "value": recommended,
        "attributes": {
            "blocking_factors": blocking_factors,
            "historical_rain": weather.historical_rain,
            "forecast_rain": weather.forecast_rain_5_days,
            "reason": reason,
        },
    }


def _soil_too_dry(config: dict[str, Any], weather: LawnWeatherData) -> bool:
    """Return whether scarifying should be blocked because the soil is too dry."""
    if config.get(CONF_WATER_DURING_DROUGHT):
        return False

    if weather.soil_moisture is not None:
        return weather.soil_moisture < 25

    return not _rain_reaches(
        weather.historical_rain, HISTORICAL_RAIN_OK_MM
    ) and not _rain_reaches(weather.forecast_rain_5_days, FORECAST_RAIN_OK_MM)


def _rain_reaches(value: float | None, threshold: int) -> bool:
    """Return whether a rain value reaches the configured threshold."""
    return value is not None and value >= threshold


def _texts(language: str) -> dict[str, str]:
    """Return localized maintenance text."""
    if language.lower().startswith("da"):
        return {
            "outside_season": "Kalenderen anbefaler normalt ikke vertikalskæring i denne måned.",
            "drought": "Undgå vertikalskæring under betydelig tørkestress.",
            "growth_stopped": "Væksten er stoppet, så plænen kommer langsommere igen.",
            "dry_soil": "Jorden er for tør til vertikalskæring uden nok akkumuleret regn eller fugtigt forecast.",
            "wet_lawn": "Nylig regn kan gøre plænen for våd til vertikalskæring.",
            "heavy_rain": "Kraftig forecast-regn gør vertikalskæring mindre egnet.",
            "ok": "Kalender og forhold er egnede til vertikalskæring.",
        }

    return {
        "outside_season": "The calendar does not normally recommend scarifying this month.",
        "drought": "Avoid scarifying during significant drought stress.",
        "growth_stopped": "Growth is stopped, so the lawn will recover more slowly.",
        "dry_soil": "Soil is too dry for scarifying without enough accumulated rain or wet forecast.",
        "wet_lawn": "Recent rain can make the lawn too wet for scarifying.",
        "heavy_rain": "Heavy forecast rain makes scarifying less suitable.",
        "ok": "Calendar and conditions are suitable for scarifying.",
    }
