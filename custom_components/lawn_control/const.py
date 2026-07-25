"""Constants for Lawn Control."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "lawn_control"

CONF_WEATHER_ENTITY = "weather_entity"
CONF_TEMPERATURE_SENSOR = "temperature_sensor"
CONF_RAIN_SENSOR = "rain_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_SOIL_MOISTURE_SENSOR = "soil_moisture_sensor"
CONF_LAWN_TYPE = "lawn_type"
CONF_ROBOTIC_MOWER = "robotic_mower"
CONF_ROBOT_MOWER_ALLOW_NIGHT = "robot_mower_allow_night"
CONF_SHADE_LEVEL = "shade_level"
CONF_SOIL_TYPE = "soil_type"
CONF_CARE_LEVEL = "care_level"
CONF_MIN_GRASS_HEIGHT = "min_grass_height"
CONF_MAX_GRASS_HEIGHT = "max_grass_height"
CONF_WATER_DURING_DROUGHT = "water_during_drought"
CONF_WATERING_LEVEL = "watering_level"
CONF_FERTILIZER_N_PERCENT = "fertilizer_n_percent"
CONF_FERTILIZER_P_PERCENT = "fertilizer_p_percent"
CONF_FERTILIZER_K_PERCENT = "fertilizer_k_percent"
CONF_DAYS_SINCE_FERTILIZER = "days_since_fertilizer"
CONF_LAST_FERTILIZED_DATE = "last_fertilized_date"
CONF_HISTORICAL_RAIN_THRESHOLD = "historical_rain_threshold"
CONF_FORECAST_RAIN_THRESHOLD = "forecast_rain_threshold"
CONF_HISTORICAL_RAIN_DAYS = "historical_rain_days"
CONF_FORECAST_RAIN_DAYS = "forecast_rain_days"

DEFAULT_MIN_GRASS_HEIGHT = 35
DEFAULT_MAX_GRASS_HEIGHT = 55
DEFAULT_FERTILIZER_NEED_THRESHOLD = 40
DEFAULT_HISTORICAL_RAIN_THRESHOLD = 10
DEFAULT_FORECAST_RAIN_THRESHOLD = 20
DEFAULT_HISTORICAL_RAIN_DAYS = 5
DEFAULT_FORECAST_RAIN_DAYS = 5
DEFAULT_UPDATE_INTERVAL = timedelta(hours=1)

LAWN_TYPES = ["regular", "ornamental", "wear_tolerant", "shade"]
SHADE_LEVELS = ["low", "medium", "high"]
SOIL_TYPES = ["sandy", "normal", "clay"]
CARE_LEVELS = ["low", "normal", "high"]
WATERING_LEVELS = ["low", "normal", "high"]
