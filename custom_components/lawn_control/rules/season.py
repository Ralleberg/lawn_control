"""Temperature-based mowing season advice, separate from today's suitability."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData

GROWTH_STOP_TEMPERATURE = 6.0
GROWTH_RESUME_TEMPERATURE = 8.0


def growth_temperature(weather: LawnWeatherData) -> float | None:
    """Use the cooler of current air and the recent mean to avoid warm spikes."""
    known = [v for v in (weather.temperature, weather.historical_temperature) if v is not None]
    return min(known) if known else None


def mowing_season_status(
    weather: LawnWeatherData, language: str, previous_status: str | None = None
) -> dict:
    """Distinguish storage advice, cold pauses and seasonal temperature readiness."""
    temperature = growth_temperature(weather)
    if weather.month in (11, 12, 1, 2):
        status = "winter_pause"
    elif temperature is None:
        status = "unknown"
    elif weather.weather_state in ("snowy", "snowy-rainy") or temperature <= GROWTH_STOP_TEMPERATURE:
        persistent_cold = (
            weather.month in (9, 10)
            and weather.seasonal_temperature_coverage_hours >= 70
            and weather.seasonal_temperature_72h is not None
            and weather.seasonal_temperature_72h <= GROWTH_STOP_TEMPERATURE
        )
        status = "winter_pause" if persistent_cold else "cold_pause"
    elif temperature < GROWTH_RESUME_TEMPERATURE and previous_status != "ready":
        status = "warming_up"
    else:
        status = "ready"
    text = _texts(language)
    return {
        "mowing_season_status": status,
        "mowing_season_label": text[status],
        "mowing_season_reason": text[f"{status}_reason"],
        "winter_storage_recommended": status == "winter_pause",
        "temperature": weather.temperature,
        "historical_temperature": weather.historical_temperature,
        "growth_temperature": temperature,
        "seasonal_temperature_72h": weather.seasonal_temperature_72h,
        "seasonal_temperature_coverage_hours": weather.seasonal_temperature_coverage_hours,
        "growth_stop_temperature": GROWTH_STOP_TEMPERATURE,
        "growth_resume_temperature": GROWTH_RESUME_TEMPERATURE,
    }


def _texts(language: str) -> dict[str, str]:
    if language.lower().startswith("da"):
        return {
            "winter_pause": "Vinterpause – robotten kan pakkes væk.",
            "winter_pause_reason": "Uden for klippesæsonen eller vedvarende efterårskulde. Rengør og opbevar robotten efter producentens anvisninger.",
            "cold_pause": "Kuldepause – vent med klipning.",
            "cold_pause_reason": "Kulde eller sne stopper klipning. En kort kuldeperiode er ikke i sig selv grund til at pakke robotten væk for vinteren.",
            "warming_up": "Afventer varmere vejr før sæsonstart.",
            "warming_up_reason": "Efter pausen skal både aktuel temperatur og tilgængeligt døgnmiddel nå 8 °C, før klippesæsonen meldes klar.",
            "ready": "Klar til klippesæsonen.",
            "ready_reason": "Sæson og temperatur tillader klipning. Dagens regn, fugt, tørke og tidsvindue kan stadig kræve pause.",
            "unknown": "Afventer temperaturdata.",
            "unknown_reason": "Temperaturdata mangler, så sæsonens temperaturstatus kan ikke vurderes.",
        }
    return {
        "winter_pause": "Winter pause – the robot can be stored.",
        "winter_pause_reason": "Outside mowing season or persistently cold autumn weather. Clean and store the robot following the manufacturer's instructions.",
        "cold_pause": "Cold pause – wait before mowing.",
        "cold_pause_reason": "Cold or snow prevents mowing. A brief cold spell alone is not a reason for winter storage.",
        "warming_up": "Waiting for warmer weather before season start.",
        "warming_up_reason": "After a pause, current temperature and the available daily mean must both reach 8 °C before the mowing season is ready.",
        "ready": "Ready for the mowing season.",
        "ready_reason": "Season and temperature permit mowing. Today's rain, moisture, drought and time window can still require a pause.",
        "unknown": "Waiting for temperature data.",
        "unknown_reason": "Temperature data is missing, so temperature readiness cannot be assessed.",
    }
