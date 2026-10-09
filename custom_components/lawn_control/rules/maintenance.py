"""Seasonal maintenance rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..const import CONF_CARE_LEVEL
from .moisture import lacks_moisture_support
from .mowing import mowing_safety_blocks

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData


VERTICUT_MONTHS = (5, 6, 7, 8, 9)


def calculate_verticut_advice(
    config: dict[str, Any],
    weather: LawnWeatherData,
    drought: dict[str, Any],
    growth: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Decide whether conditions are suitable for scarifying the lawn."""
    blocking_factors = mowing_safety_blocks(config, weather, language)
    text = _texts(language)

    if config.get(CONF_CARE_LEVEL) != "high":
        blocking_factors.append(text["intensive_care_only"])

    if weather.month not in VERTICUT_MONTHS or (weather.month == 9 and weather.day > 15):
        blocking_factors.append(text["outside_season"])

    if drought["value"] in ("high", "critical"):
        blocking_factors.append(text["drought"])

    if growth["value"] in ("stopped", "slow"):
        blocking_factors.append(text["growth_stopped"])

    if lacks_moisture_support(config, weather):
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
            "aftercare": text["aftercare"],
            "historical_rain": weather.historical_rain,
            "forecast_rain": weather.forecast_rain_5_days,
            "reason": reason,
        },
    }


def _texts(language: str) -> dict[str, str]:
    """Return localized maintenance text."""
    if language.lower().startswith("da"):
        return {
            "intensive_care_only": "Vertikalskæring anbefales kun ved højt plejeniveau og planlagt efterpleje, ikke som rutine mod mos.",
            "aftercare": "Vertikalskær kun sundt græs. Efterså, topdress og hold såningen fugtig, indtil græsset er etableret.",
            "outside_season": "Kalenderen anbefaler normalt ikke vertikalskæring i denne måned.",
            "drought": "Undgå vertikalskæring under betydelig tørkestress.",
            "growth_stopped": "Væksten er for begrænset til, at plænen kan komme sig godt.",
            "dry_soil": "Jorden er for tør til vertikalskæring uden nok akkumuleret regn eller fugtigt forecast.",
            "wet_lawn": "Nylig regn kan gøre plænen for våd til vertikalskæring.",
            "heavy_rain": "Kraftig forecast-regn gør vertikalskæring mindre egnet.",
            "ok": "Kalender og forhold er egnede til vertikalskæring.",
        }

    return {
        "intensive_care_only": "Scarifying requires high care level and planned aftercare; it is not routine moss treatment.",
        "aftercare": "Only scarify healthy grass. Overseed, topdress and keep seed moist until established.",
        "outside_season": "The calendar does not normally recommend scarifying this month.",
        "drought": "Avoid scarifying during significant drought stress.",
        "growth_stopped": "Growth is too limited for the lawn to recover well.",
        "dry_soil": "Soil is too dry for scarifying without enough accumulated rain or wet forecast.",
        "wet_lawn": "Recent rain can make the lawn too wet for scarifying.",
        "heavy_rain": "Heavy forecast rain makes scarifying less suitable.",
        "ok": "Calendar and conditions are suitable for scarifying.",
    }
