"""Forecast windows use timestamps, finite metric values and overlap weighting."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from typing import Any


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def normalize_value(value: Any, unit: str | None, kind: str) -> float | None:
    """Reject incompatible units instead of silently assuming metric."""
    number = _as_float(value)
    if number is None:
        return None
    if kind == "temperature":
        if unit in ("°F", "F"):
            number = (number - 32) * 5 / 9
        elif unit in ("K", "°K"):
            number -= 273.15
        elif unit not in (None, "", "°C", "C"):
            return None
        return number if -80 <= number <= 65 else None
    if kind == "rain":
        if unit in ("in", "inch", "inches"):
            number *= 25.4
        elif unit not in (None, "", "mm"):
            return None
        return number if number >= 0 else None
    if kind == "humidity":
        return number if unit in (None, "", "%") and 0 <= number <= 100 else None
    return None


def normalize_forecast(
    forecast: list[dict[str, Any]], temperature_unit: str | None, rain_unit: str | None
) -> list[dict[str, Any]]:
    result = []
    for raw in forecast:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        for key in ("temperature", "templow"):
            if key in item:
                item[key] = normalize_value(item[key], temperature_unit, "temperature")
        if "precipitation" in item:
            item["precipitation"] = normalize_value(
                item["precipitation"], rain_unit, "rain"
            )
        if "humidity" in item:
            item["humidity"] = normalize_value(item["humidity"], "%", "humidity")
        result.append(item)
    return result


def _forecast_interval_hours(forecast: list[dict[str, Any]]) -> float:
    times = [
        t
        for item in forecast
        if (t := _parse_datetime(item.get("datetime"))) is not None
    ]
    if not times:
        return 24.0
    aware = times[0].tzinfo is not None
    times = sorted({t for t in times if (t.tzinfo is not None) == aware})
    if len(times) >= 2:
        return min(
            24.0,
            min(
                (b - a).total_seconds() / 3600
                for a, b in zip(times, times[1:], strict=False)
            ),
        )
    return 1.0 if len(forecast) > 8 else 24.0


def _forecast_periods(
    forecast: list[dict[str, Any]], hours: int, now: datetime, interval_hours: float
) -> list[tuple[dict[str, Any], float]]:
    end = now + timedelta(hours=hours)
    valid: dict[datetime, dict[str, Any]] = {}
    for item in forecast:
        start = _parse_datetime(item.get("datetime"))
        # A missing timestamp cannot establish a future window.
        if start is None or (start.tzinfo is None) != (now.tzinfo is None):
            continue
        valid[start] = item
    entries = sorted(valid.items())
    periods = []
    for index, (start, item) in enumerate(entries):
        period_end = start + timedelta(hours=interval_hours)
        if index + 1 < len(entries):
            period_end = min(period_end, entries[index + 1][0])
        overlap = (min(end, period_end) - max(now, start)).total_seconds() / 3600
        if overlap > 0:
            periods.append((item, overlap))
    return periods


def _forecast_rain_amount(item: dict[str, Any]) -> float | None:
    rain = _as_float(item.get("precipitation"))
    if rain is None or rain < 0:
        return None
    # Providers may already report probability-weighted precipitation. Do not
    # multiply the amount by probability a second time.
    return rain


def _forecast_precipitation_next_hours(
    forecast: list[dict[str, Any]], hours: int, now: datetime
) -> float | None:
    interval = _forecast_interval_hours(forecast)
    values = [
        rain * overlap / interval
        for item, overlap in _forecast_periods(forecast, hours, now, interval)
        if (rain := _forecast_rain_amount(item)) is not None
    ]
    return round(sum(values), 1) if values else None


def _forecast_precipitation(
    forecast: list[dict[str, Any]], now: datetime
) -> float | None:
    return _forecast_precipitation_next_hours(forecast, 24, now)


def _forecast_precipitation_days(
    forecast: list[dict[str, Any]], days: int, now: datetime
) -> float | None:
    return _forecast_precipitation_next_hours(forecast, days * 24, now)


def forecast_temperature(
    forecast: list[dict[str, Any]], now: datetime
) -> dict[str, Any]:
    interval = _forecast_interval_hours(forecast)
    samples = [
        (item, overlap)
        for item, overlap in _forecast_periods(forecast, 72, now, interval)
        if _as_float(item.get("temperature")) is not None
    ]
    coverage = sum(overlap for _, overlap in samples)
    if not coverage:
        return {
            "forecast_temperature_72h": None,
            "forecast_min_temperature_72h": None,
            "forecast_temperature_coverage_hours": 0.0,
        }
    return {
        "forecast_temperature_72h": round(
            sum(
                (
                    (_as_float(item.get("temperature")) + _as_float(item["templow"]))
                    / 2
                    if _as_float(item.get("templow")) is not None
                    else _as_float(item.get("temperature"))
                )
                * overlap
                for item, overlap in samples
            )
            / coverage,
            1,
        ),
        "forecast_min_temperature_72h": min(
            min(
                _as_float(item["temperature"]),
                _as_float(item.get("templow", item["temperature"]))
                if _as_float(item.get("templow")) is not None
                else _as_float(item["temperature"]),
            )
            for item, _ in samples
        ),
        "forecast_temperature_coverage_hours": round(coverage, 1),
    }


def _forecast_drying_pressure(
    forecast: list[dict[str, Any]], hours: int, now: datetime
) -> float | None:
    """Return forecast drying pressure measured as sunny-day equivalents."""
    if not forecast:
        return None

    interval_hours = _forecast_interval_hours(forecast)
    total = 0.0
    found = False

    for item, overlap_hours in _forecast_periods(
        forecast,
        hours,
        now,
        interval_hours,
    ):
        condition = item.get("condition")
        temperature = _as_float(item.get("temperature"))
        humidity = _as_float(item.get("humidity"))
        rain = _forecast_rain_amount(item)
        if condition is None and temperature is None and rain is None:
            continue

        condition_factor = _drying_condition_factor(condition)
        temperature_factor = _drying_temperature_factor(temperature)
        humidity_factor = _drying_humidity_factor(humidity)
        rain_factor = _drying_rain_factor(rain, interval_hours)
        total += (
            condition_factor
            * temperature_factor
            * humidity_factor
            * rain_factor
            * overlap_hours
            / 24
        )
        found = True

    return round(total, 2) if found else None


def _drying_condition_factor(condition: Any) -> float:
    """Return relative drying strength for a weather condition."""
    if condition == "sunny":
        return 1.0
    if condition == "partlycloudy":
        return 0.65
    if condition in ("cloudy", "fog"):
        return 0.2
    if condition == "clear-night":
        return 0.05
    if condition in ("rainy", "pouring", "lightning-rainy", "snowy", "hail"):
        return 0.0
    return 0.35


def _drying_temperature_factor(temperature: float | None) -> float:
    """Return relative drying strength for forecast temperature."""
    if temperature is None:
        return 1.0
    if temperature >= 28:
        return 1.45
    if temperature >= 24:
        return 1.2
    if temperature >= 18:
        return 1.0
    if temperature >= 12:
        return 0.7
    return 0.4


def _drying_humidity_factor(humidity: float | None) -> float:
    """Return relative drying strength for forecast humidity."""
    if humidity is None:
        return 1.0
    if humidity < 40:
        return 1.2
    if humidity >= 75:
        return 0.7
    return 1.0


def _drying_rain_factor(rain: float | None, interval_hours: float) -> float:
    """Reduce drying pressure when meaningful rain is expected in the period."""
    if rain is None or rain <= 0:
        return 1.0

    meaningful_rain = 0.5 if interval_hours <= 2 else 5.0
    if rain >= meaningful_rain:
        return 0.1
    return 0.4
