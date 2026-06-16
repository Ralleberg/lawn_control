# Lawn Control

Lawn Control is a Home Assistant custom integration that gives lawn care advice from a weather entity, forecast data and optional real sensors for rain, temperature, humidity and soil moisture.

Version `0.1.2` is advisory only. It exposes a robot mower permission entity,
but it does not send commands to mower hardware.

## Entities

- `sensor.lawn_recommended_grass_height`: target grass height in millimeters with `min_height`, `max_height` and `reason` attributes.
- `sensor.lawn_drought_risk`: `low`, `medium`, `high` or `critical`, with score details.
- `sensor.lawn_growth_rate`: `stopped`, `slow`, `normal` or `fast`, with estimated millimeters per day and next 7 days.
- `sensor.lawn_fertilizer_score`: numeric score from 0 to 100 with scoring details.
- `binary_sensor.lawn_good_day_for_fertilizer`: on when score and blocking checks allow fertilizing.
- `binary_sensor.lawn_should_mow`: on when mowing is recommended.
- `binary_sensor.lawn_robot_mower_should_run`: on when a configured robot mower should be allowed to run.
- `sensor.lawn_care_recommendation`: short human-readable action summary.

## Configuration

Add the integration from Home Assistant's integrations UI. The config flow asks for:

- Weather entity
- Optional temperature, rain, humidity and soil moisture sensors
- Lawn type
- Robotic mower presence
- Shade level
- Soil type
- Care level
- Minimum and maximum grass height
- Whether you water during dry periods, and the watering level
- Optional NPK fertilizer percentages and days since fertilizer

## Rule Approach

The rule engine is intentionally simple in `0.1.2`. It uses transparent scoring and blocking checks for:

- Higher grass during summer stress, shade and wear.
- Drought risk from rain, forecast rain, temperature, humidity, soil moisture, soil type and season.
- Watering during dry periods reduces drought stress and can support growth after dry weather.
- Growth rate from season, temperature and drought stress.
- Extra expected growth from recent NPK fertilizer, mainly driven by nitrogen and reduced over time.
- Fertilizer suitability from season, growth, drought stress, heat and rain forecast.
- Mowing suitability from wet conditions, drought risk, growth rate and forecast rain.
- Robot mower run permission from mowing suitability, wet grass, rain forecast, drought stress and growth.

Each advisory entity exposes the decision details in attributes so the result can be inspected and refined.

## HACS

This repository is structured for HACS as a Home Assistant integration. Add it as a custom repository in HACS, then install and restart Home Assistant.

## Development

Run a syntax check:

```bash
python -m compileall custom_components
```
