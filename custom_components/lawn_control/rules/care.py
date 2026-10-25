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
    mowing_frequency,
)
from .season import mowing_season_status

if TYPE_CHECKING:
    from ..coordinator import LawnWeatherData

MODERATE_FORECAST_DRYING_PRESSURE = 0.75
HIGH_FORECAST_DRYING_PRESSURE = 1.5

# Winter retains the autumn protection height; mowing remains seasonally blocked.
ROBOT_MONTHLY_MIN_HEIGHT = (50, 50, 50, 40, 40, 40, 40, 40, 40, 50, 50, 50)
MANUAL_MONTHLY_MIN_HEIGHT = (60, 60, 60, 60, 50, 50, 50, 50, 50, 60, 60, 60)


def build_advice(
    config: dict[str, Any],
    weather: LawnWeatherData,
    language: str,
    previous_season_status: str | None = None,
) -> dict[str, Any]:
    """Build all lawn advice from config and weather inputs."""
    height = recommended_grass_height(config, weather, language)
    drought = calculate_drought_risk(config, weather, language)
    growth = calculate_growth_rate(config, weather, drought, language)
    growth["attributes"].update(
        mowing_season_status(weather, language, previous_season_status)
    )
    fertilizer = calculate_fertilizer_score(config, weather, drought, growth, language)
    mowing = calculate_mowing_advice(config, weather, drought, growth, height, language)
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
        "recommended_mowing_frequency": mowing_frequency(
            config, weather, growth, language
        ),
        "recommended_grass_height": height,
        "drought_risk": drought,
        "growth_rate": growth,
        "fertilizer_score": fertilizer,
        "fertilizer_day": {
            "value": fertilizer["recommended"],
            "attributes": {
                **fertilizer["attributes"],
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
    monthly_heights = (
        ROBOT_MONTHLY_MIN_HEIGHT
        if config.get(CONF_ROBOTIC_MOWER)
        else MANUAL_MONTHLY_MIN_HEIGHT
    )
    seasonal_min_height = monthly_heights[weather.month - 1]
    min_height = max(configured_min_height, seasonal_min_height)
    reasons: list[str] = []
    text = _texts(language)
    reasons.append(text["seasonal_height"].format(height=seasonal_min_height))

    if config.get(CONF_LAWN_TYPE) == "ornamental":
        reasons.append(text["ornamental_height"])
    elif config.get(CONF_LAWN_TYPE) == "wear_tolerant":
        min_height += 5
        reasons.append(text["wear_height"])
    elif config.get(CONF_LAWN_TYPE) == "shade":
        min_height += 10
        reasons.append(text["shade_height"])

    if config.get(CONF_SHADE_LEVEL) == "high":
        min_height += 5
        reasons.append(text["high_shade_height"])

    forecast_rain = weather.forecast_rain_5_days
    rain_total = weather.historical_rain or 0.0
    rain_threshold = rain_moisture_threshold(config)

    # Protective minima take precedence over the user's preferred maximum.
    max_height = max(configured_max_height, min_height)
    if max_height > configured_max_height:
        reasons.append(text["protective_max_override"])

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

    if weather.month == 10:
        target = max_height
        reasons.append(text["final_cut"])
    target = max(min_height, min(max_height, target))
    return {
        "value": target,
        "attributes": {
            "seasonal_min_height": seasonal_min_height,
            "configured_min_height": configured_min_height,
            "configured_max_height": configured_max_height,
            "configured_max_overridden": max_height > configured_max_height,
            "month": weather.month,
            "mower_type": "robot" if config.get(CONF_ROBOTIC_MOWER) else "manual",
            "max_cut_fraction": 1 / 3,
            "min_height": min_height,
            "max_height": max_height,
            "historical_rain": weather.historical_rain,
            "recent_rain_24h": weather.historical_rain_24h,
            "forecast_rain": forecast_rain,
            "forecast_rain_next_24h": weather.forecast_rain_next_24h,
            "forecast_drying_pressure_72h": weather.forecast_drying_pressure_72h,
            "rain_total": rain_moisture_total(weather),
            "observed_rain_total": rain_total,
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
    drying_pressure = weather.forecast_drying_pressure_72h or 0.0
    meaningful_water = historical_rain_threshold(config)
    soil_moisture = weather.soil_moisture

    if soil_moisture is not None and soil_moisture < 25:
        reasons.append(text["low_soil_moisture_height"])
        return max_height

    moisture_protected = config.get(CONF_WATER_DURING_DROUGHT) or (
        soil_moisture is not None and soil_moisture >= 35
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

    if recent_rain >= meaningful_water:
        reasons.append(text["near_term_water_height"])
        return min_height

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
    season = growth["attributes"]
    status = season.get("mowing_season_status")
    if status and status != "ready":
        actions.append(season["mowing_season_label"])
        reasons.append(season["mowing_season_reason"])

    if drought["value"] in ("high", "critical"):
        actions.append(text["water"])
        reasons.append(text["drought"].format(risk=text[drought["value"]]))
    if mowing["value"]:
        actions.append(text["robot_mow"] if robotic_mower else text["mow"])
        reasons.append(
            text["robot_mow_reason"] if robotic_mower else text["mow_reason"]
        )
    if fertilizer["recommended"] is True:
        actions.append(text["fertilize"])
    elif fertilizer["blocking_factors"]:
        actions.append(text["wait_fertilizer"])
    elif fertilizer["recommended"] is None:
        actions.append(text["unknown_fertilizer"])
    reasons.append(fertilizer["reason"])
    if not mowing["value"]:
        if growth["value"] in ("stopped", "slow") and status == "ready":
            actions.insert(0, text["low_growth_pause"])
        elif status == "ready":
            actions.insert(0, text["wait_mowing"])
        reasons.append(mowing["attributes"].get("reason", ""))
    if not actions:
        actions.append(text["monitor"])
    if mowing["value"]:
        guidance = mowing["attributes"].get("frequency_guidance")
        if guidance:
            reasons.append(guidance)
    reasons.append(text["maintenance"])

    return {
        "value": " ".join(actions),
        "attributes": {
            "actions": actions,
            "mowing_season_status": status,
            "winter_storage_recommended": season.get(
                "winter_storage_recommended", False
            ),
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
            "seasonal_height": "Månedens minimum for denne klippertype er {height} mm.",
            "protective_max_override": "Den beskyttende minimumshøjde overstyrer det indtastede maksimum.",
            "water": "Hold pause med klipning under tørke. Hvis du vander, så vand sjældent og grundigt, ca. 20 mm ad gangen.",
            "drought": "Tørkerisikoen er {risk}.",
            "mow": "Det er egnet at slå græs i dag.",
            "mow_reason": "Væksten understøtter klipning; kontrollér at græsset er tørt.",
            "robot_mow": "Robotplæneklipperen kan køre i dag.",
            "robot_mow_reason": "Robotklipperens egne stopfaktorer er ikke aktive.",
            "fertilize": "Gødning kan overvejes ved behov; følg produktets anvisning.",
            "wait_mowing": "Vent med klipning.",
            "wait_fertilizer": "Vent med gødning.",
            "unknown_fertilizer": "Gødningsbehovet kan ikke vurderes endnu.",
            "low_growth_pause": "Væksten er lav; afvent behov for klipning.",
            "final_cut": "Bevar en høj indstilling ved den sidste klipning. Fjern blade og brug skarpe knive.",
            "maintenance": "Brug skarpe knive. Kontrollér robotknive jævnligt, typisk hver 3.–4. uge efter model og slid. Slib almindelig klippers kniv i juni og før sidste klipning. Fjern eller fordel klatter af afklip, fjern tætte lag blade og variér kørespor.",
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
            "ornamental_height": "Prydplænens ønskede højde respekteres inden for månedens beskyttende minimum.",
            "wear_height": "Slidstærke plæner kommer sig bedre med lidt mere bladmasse.",
            "shade_height": "Skyggeplæner har brug for ekstra bladmasse for stærkere vækst.",
            "high_shade_height": "Meget skygge øger den anbefalede klippehøjde.",
            "summer_height": "Sommerstress taler for en højere klippehøjde.",
            "very_low_rain_height": "Observeret regn er langt under den valgte fugtgrænse, så målet holdes på maksimumhøjden.",
            "medium_rain_height": "Observeret regn er under den valgte fugtgrænse, så målet flyttes til medianhøjden.",
            "good_rain_height": "Observeret regn opfylder den valgte fugtgrænse, så minimumhøjden er egnet.",
            "near_term_water_height": "Nok regn er faldet inden for 24 timer, så en lavere klippehøjde er forsvarlig.",
            "low_soil_moisture_height": "Lav jordfugtighed holder klippehøjden på maksimum for at beskytte plænen.",
            "high_forecast_drying_height": "Sol, varme og begrænset regn de næste tre døgn holder klippehøjden på maksimum.",
            "moderate_forecast_drying_height": "Forventet udtørring de næste tre døgn hæver klippehøjden til midten af intervallet.",
            "configured_max_cap": "Det indtastede maksimum bruges som øvre grænse.",
            "configured_height": "Det konfigurerede højdeinterval passer til de aktuelle forhold.",
        }

    return {
        "seasonal_height": "This month's minimum for this mower type is {height} mm.",
        "protective_max_override": "The protective minimum height overrides the configured maximum.",
        "water": "Pause mowing during drought. If watering, water infrequently and deeply, about 20 mm per application.",
        "drought": "Drought risk is {risk}.",
        "mow": "Mowing is suitable today.",
        "mow_reason": "Growth supports mowing; check that the grass is dry.",
        "robot_mow": "The robot mower can run today.",
        "robot_mow_reason": "Robot mower-specific blocking factors are clear.",
        "fertilize": "Consider fertilizer if needed; follow the product label.",
        "wait_mowing": "Wait before mowing.",
        "wait_fertilizer": "Wait before fertilizing.",
        "unknown_fertilizer": "Fertilizer need cannot yet be assessed.",
        "low_growth_pause": "Growth is limited; wait until mowing is needed.",
        "final_cut": "Keep a high setting for the final cut. Remove leaves and use sharp blades.",
        "maintenance": "Use sharp blades. Check robot blades regularly, typically every 3–4 weeks depending on model and wear. Sharpen manual mower blades in June and before the final cut. Collect or spread clumps, remove thick leaf layers and vary mowing routes.",
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
        "ornamental_height": "The preferred ornamental lawn height respects the monthly protective minimum.",
        "wear_height": "Wear tolerant lawns recover better with a little more leaf area.",
        "shade_height": "Shaded lawns need extra leaf area for stronger growth.",
        "high_shade_height": "High shade increases the recommended cutting height.",
        "summer_height": "Summer stress favors a higher mowing height.",
        "very_low_rain_height": "Observed rain is far below the configured moisture threshold, keeping the target at maximum height.",
        "medium_rain_height": "Observed rain is below the configured moisture threshold, moving the target to median height.",
        "good_rain_height": "Observed rain meets the configured moisture threshold, making minimum height suitable.",
        "near_term_water_height": "Enough rain has fallen within 24 hours, making a lower mowing height suitable.",
        "low_soil_moisture_height": "Low soil moisture keeps the mowing height at maximum to protect the lawn.",
        "high_forecast_drying_height": "Sun, heat and limited rain over the next three days keep the mowing height at maximum.",
        "moderate_forecast_drying_height": "Expected drying over the next three days raises the mowing height to the middle of the range.",
        "configured_max_cap": "The configured maximum is used as the upper limit.",
        "configured_height": "Configured lawn height range is suitable for current conditions.",
    }
