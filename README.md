# Lawn Control

![Lawn Control teaser](assets/lawn-control-teaser.png)

Lawn Control is a Home Assistant custom integration that gives lawn care advice from a weather entity, forecast data and optional real sensors for rain, temperature, humidity and soil moisture.

Version `1.1.3` is advisory only. It exposes a robot mower permission entity,
but it does not send commands to mower hardware.

## Entities

- `sensor.lawn_recommended_grass_height`: target grass height in millimeters with `min_height`, `max_height` and `reason` attributes.
- `sensor.lawn_drought_risk`: `low`, `medium`, `high` or `critical`, with score details.
- `sensor.lawn_growth_rate`: `stopped`, `slow`, `normal` or `fast`, with estimated millimeters per day, next 7 days and fertilizer growth factors.
- `sensor.lawn_fertilizer_score`: estimated remaining fertilizer effect from 0 to 100 with scoring details.
- `sensor.lawn_historical_rain`: accumulated historical rain used by the rule engine.
- `sensor.lawn_forecast_rain`: forecast rain used by the rule engine.
- `sensor.lawn_combined_rain`: combined historical and forecast rain used for moisture decisions.
- `binary_sensor.lawn_good_day_for_fertilizer`: on when the fertilizer need score is below 40 and blocking checks allow fertilizing.
- `binary_sensor.lawn_should_mow`: locked once per day to show whether mowing is recommended today. Not created when robotic mower is enabled.
- `binary_sensor.lawn_robot_mower_should_run`: on when a configured robot mower should be allowed to run.
- `binary_sensor.lawn_should_verticut`: on when the calendar and current conditions support scarifying.
- `sensor.lawn_care_recommendation`: short human-readable action summary.

## Configuration

Add the integration from Home Assistant's integrations UI. The config flow asks for:

- Weather entity
- Optional temperature, rain, humidity and soil moisture sensors
- Lawn type
- Robotic mower presence
- Daily assessment hour from 0 to 23
- Shade level
- Soil type
- Care level
- Minimum and maximum grass height
- Whether you water during dry periods, and the watering level
- Optional NPK fertilizer percentages and latest fertilizer date in `YYYY-MM-DD` format, for example `2026-05-20`

## Rule Approach

The rule engine is intentionally simple in `1.1.3`. It uses transparent scoring and blocking checks for:

- Grass height steps from combined 5-day rain support: maximum height below 10 mm, rounded median from 10-20 mm and minimum height from 20 mm.
- Drought risk from observed rain, 5-day rain history, 5-day forecast rain, temperature, humidity, soil moisture, soil type and season.
- Watering during dry periods reduces drought stress and can support growth after dry weather.
- Growth rate from season, temperature and drought stress.
- Extra expected growth from recent NPK fertilizer, driven by nitrogen, NPK completeness, fertilizer age and moisture from rain, forecast rain, soil moisture or configured watering.
- Fertilizer score from latest fertilizer date and NPK strength, calculated automatically on every update and changing only as fertilizer age changes.
- Fertilizer blocking checks from season, growth, drought stress, heat and shared moisture support from soil moisture, configured watering or at least 20 mm combined historical and forecast rain.
- Mowing suitability from wet conditions, drought risk, growth rate and forecast rain.
- Daily mowing plan from mowing suitability, recent weather history, wet grass, rain forecast, drought stress and growth.
- Robot mower run permission from current mowing suitability, mower-specific blockers and the same moisture support check.
- Scarifying from the seasonal calendar, drought stress, growth, wet conditions and the same moisture support check.

Each advisory entity exposes the decision details in attributes so the result can be inspected and refined.

Recommended grass height and the other advisory sensors update continuously. The mowing recommendation is locked once per day from the configured daily assessment hour, using recent temperature, humidity and rain history together with forecast and current lawn factors.

## HACS

This repository is structured for HACS as a Home Assistant integration. Add it as a custom repository in HACS, then install and restart Home Assistant.

## Languages

The integration includes English and Danish translations for the config flow, options flow, select options and entity names.

## Development

Run a syntax check:

```bash
python -m compileall custom_components
```
