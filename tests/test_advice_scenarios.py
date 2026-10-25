"""End-to-end rule and coordinator scenarios without a Home Assistant install."""

import __future__

import ast
import math
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_lawn_rules import PACKAGE, ROOT, care, load, moisture, mowing, weather

forecasts = load(f"{PACKAGE}.forecasts", ROOT / "forecasts.py")
constants = load(f"{PACKAGE}.const", ROOT / "const.py")


def coordinator_at(now, robot=True):
    """Compile real coordinator methods with a clock and minimal HA substitutes."""
    tree = ast.parse((ROOT / "coordinator.py").read_text())
    source = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LawnControlCoordinator"
    )
    wanted = {
        "_apply_mowing_cadence",
        "_apply_drought_recovery",
        "_robot_catch_up_hard_blocked",
        "_apply_robot_mower_catch_up",
        "_apply_mowing_time_window",
        "_block_mowing_advice",
        "_mark_mowing_time_window_open",
        "_robot_mower_status_data",
        "_update_robot_mower_tracking",
        "_update_growth_history",
        "_apply_robot_mower_status_attributes",
        "_apply_mowing_frequency_lock",
        "_refresh_care_recommendation",
        "_async_update_data",
        "_read_weather_data",
        "_read_float_sensor",
        "_read_observed_rain",
        "_update_weather_history",
        "async_set_last_mowed_date",
        "async_set_estimated_grass_height",
        "estimated_grass_height",
        "last_mowed_date",
    }
    source.body = [
        node for node in source.body if getattr(node, "name", None) in wanted
    ]
    source.bases, source.keywords, source.decorator_list = [], [], []
    namespace = dict(vars(constants))
    namespace.update(vars(forecasts))
    namespace.update(
        Any=object,
        LawnWeatherData=lambda **values: SimpleNamespace(**values),
        math=math,
        datetime=datetime,
        date=datetime.date,
        timedelta=timedelta,
        deepcopy=deepcopy,
        dt_util=SimpleNamespace(now=lambda: now),
        mowing_safety_blocks=mowing.mowing_safety_blocks,
        lacks_moisture_support=moisture.lacks_moisture_support,
        protect_cut_height=mowing.protect_cut_height,
        gradual_cut_plan=mowing.gradual_cut_plan,
        build_advice=care.build_advice,
        general_recommendation=care.general_recommendation,
        ROBOT_CATCH_UP_TRIGGER_MM=3,
        ROBOT_CATCH_UP_COOLDOWN=timedelta(hours=48),
        ROBOT_MOWER_MOWING_STATE="mowing",
        ROBOT_MOWER_FINISHED_STATES={"returning", "docked"},
        ROBOT_MOWER_ERROR_STATE="error",
        UNAVAILABLE_STATES={"unknown", "unavailable"},
        SUN_ENTITY_ID="sun.sun",
        MOWING_LOCK_VERSION=7,
        GROWTH_HISTORY_KEEP_DAYS=21,
        WEATHER_HISTORY_HOURS=24,
        MAX_WEATHER_HISTORY_KEEP_DAYS=10,
    )
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    exec(
        compile(
            ast.Module(body=functions + [source], type_ignores=[]),
            "coordinator.py",
            "exec",
            flags=__future__.annotations.compiler_flag,
        ),
        namespace,
    )
    obj = namespace["LawnControlCoordinator"]()
    obj.config = {"robotic_mower": robot, "robot_mower_allow_night": False}
    obj.daily_update_hour = 8
    obj._stored_data = {}
    obj.data = None
    obj.hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _: None),
        config=SimpleNamespace(language="da"),
    )
    obj._store = SimpleNamespace(async_save=AsyncMock())
    obj.async_request_refresh = AsyncMock()
    return obj, namespace


class AdviceScenarios(unittest.TestCase):
    def config(self, **updates):
        result = dict(
            robotic_mower=True,
            fertilizer_type="regular",
            fertilizer_effect_days=60,
            fertilizer_n_percent=15,
            fertilizer_dose_g_m2=30,
            fertilizer_label_dose_g_m2=30,
            days_since_fertilizer=90,
        )
        result.update(updates)
        return result

    def test_cold_october_never_recommends_fertilizer(self):
        for product in ("regular", "autumn"):
            for temperature in (4, 7, 8, 9, 10):
                result = care.build_advice(
                    self.config(fertilizer_type=product),
                    weather(
                        month=10,
                        temperature=temperature,
                        historical_temperature=temperature,
                    ),
                    "da",
                )
                self.assertFalse(result["fertilizer_day"]["value"])
                self.assertIn(
                    "Vent med gødning", result["care_recommendation"]["value"]
                )
                self.assertNotIn("klippesæson", str(result).lower())

    def test_warm_autumn_requires_explicit_product_and_active_growth(self):
        regular = care.build_advice(self.config(), weather(month=10), "da")
        autumn = care.build_advice(
            self.config(fertilizer_type="autumn"), weather(month=10), "da"
        )
        self.assertFalse(regular["fertilizer_day"]["value"])
        self.assertTrue(autumn["fertilizer_day"]["value"])
        self.assertFalse(autumn["fertilizer_score"]["attributes"]["need_confirmed"])
        self.assertIn("kan overvejes", autumn["care_recommendation"]["value"])

    def test_label_interval_dose_and_missing_application(self):
        for days, duration, expected in (
            (59, 60, False),
            (60, 60, True),
            (75, 90, False),
            (90, 90, True),
        ):
            result = care.build_advice(
                self.config(
                    days_since_fertilizer=days, fertilizer_effect_days=duration
                ),
                weather(),
                "en",
            )
            self.assertIs(result["fertilizer_day"]["value"], expected)
        missing = care.build_advice(
            self.config(fertilizer_dose_g_m2=0), weather(), "en"
        )
        self.assertIsNone(missing["fertilizer_day"]["value"])
        self.assertIsNone(missing["fertilizer_score"]["score"])
        dose = care.build_advice(
            self.config(days_since_fertilizer=0, fertilizer_dose_g_m2=20),
            weather(),
            "en",
        )
        self.assertEqual(dose["fertilizer_score"]["attributes"]["nitrogen_g_m2"], 3)

    def test_forecast_cold_frost_and_insufficient_coverage(self):
        for inputs in (
            dict(forecast_temperature_72h=7),
            dict(forecast_min_temperature_72h=-1),
        ):
            result = care.build_advice(self.config(), weather(**inputs), "en")
            self.assertFalse(result["fertilizer_day"]["value"])
        for inputs in (
            dict(historical_temperature_coverage_hours=2),
            dict(forecast_temperature_coverage_hours=24),
            dict(temperature=None),
        ):
            result = care.build_advice(self.config(), weather(**inputs), "en")
            self.assertIsNone(result["fertilizer_day"]["value"])

    def test_future_rain_and_watering_preference_do_not_wet_soil_now(self):
        for watering in (False, True):
            result = care.build_advice(
                self.config(water_during_drought=watering),
                weather(
                    soil_moisture=None,
                    historical_rain=0,
                    forecast_rain_5_days=40,
                    forecast_rain_next_24h=15,
                ),
                "en",
            )
            self.assertFalse(result["fertilizer_day"]["value"])
            self.assertFalse(result["robot_mower_should_run"]["value"])
            self.assertEqual(result["recommended_grass_height"]["value"], 55)

    def test_monthly_frequency_and_current_dew_not_yesterdays_humidity(self):
        for robot, month, expected in (
            (True, 5, 7),
            (True, 3, 3),
            (True, 10, 3),
            (False, 7, 2),
        ):
            result = care.build_advice(
                self.config(robotic_mower=robot), weather(month=month), "en"
            )
            self.assertEqual(result["recommended_mowing_frequency"]["value"], expected)
        yesterday = care.build_advice(
            self.config(), weather(historical_humidity=99, humidity=60), "en"
        )
        dew = care.build_advice(
            self.config(), weather(historical_humidity=60, humidity=99), "en"
        )
        self.assertTrue(yesterday["robot_mower_should_run"]["value"])
        self.assertFalse(dew["robot_mower_should_run"]["value"])

    def test_october_final_height_and_summer_no_automatic_increase(self):
        october = care.build_advice(self.config(), weather(month=10), "da")
        summer = care.build_advice(self.config(), weather(month=7), "da")
        self.assertEqual(october["recommended_grass_height"]["value"], 55)
        self.assertEqual(summer["recommended_grass_height"]["value"], 40)
        self.assertIn(
            "sidste klipning",
            october["recommended_grass_height"]["attributes"]["reason"],
        )
        self.assertEqual(mowing.gradual_cut_plan(100, 40), [70, 50, 40])

    def test_catch_up_retains_all_weather_stops(self):
        now = datetime(2026, 5, 15, 12, tzinfo=UTC)
        obj, _ = coordinator_at(now)
        for inputs in (
            dict(humidity=99),
            dict(forecast_rain=10),
            dict(soil_moisture=10),
            dict(temperature=4),
            dict(weather_state="rainy"),
        ):
            w = weather(**inputs)
            result = care.build_advice(self.config(), w, "en")
            obj._apply_robot_mower_catch_up(
                result,
                w,
                "en",
                dict(
                    last_registered_run=(now - timedelta(days=8)).isoformat(),
                    estimated_grass_height=90,
                ),
            )
            self.assertFalse(result["robot_mower_should_run"]["value"])
            self.assertTrue(
                result["robot_mower_should_run"]["attributes"]["blocking_factors"]
            )
        # Limited growth alone can still justify a needed cut after a long pause.
        w = weather(temperature=9, historical_temperature=9)
        result = care.build_advice(self.config(), w, "en")
        obj._apply_robot_mower_catch_up(
            result,
            w,
            "en",
            dict(
                last_registered_run=(now - timedelta(days=8)).isoformat(),
                estimated_grass_height=70,
            ),
        )
        self.assertTrue(result["robot_mower_should_run"]["value"])

    def test_time_window_independent_of_assessment_and_night_option(self):
        now = datetime(2026, 5, 15, 8, tzinfo=UTC)
        obj, _ = coordinator_at(now)
        obj.config["robot_mower_allow_night"] = True
        result = care.build_advice(self.config(), weather(), "en")
        obj._apply_mowing_time_window(result, weather(), "en")
        self.assertFalse(result["robot_mower_should_run"]["value"])
        self.assertIn("09:00", result["robot_mower_should_run"]["attributes"]["reason"])
        for hour, permitted in ((9, True), (12, True)):
            obj, _ = coordinator_at(now.replace(hour=hour))
            result = care.build_advice(self.config(), weather(), "en")
            obj._apply_mowing_time_window(result, weather(), "en")
            self.assertEqual(result["robot_mower_should_run"]["value"], permitted)

    def test_actual_cadence_and_safe_height_model(self):
        now = datetime(2026, 5, 15, 12, tzinfo=UTC)
        for robot in (True, False):
            obj, _ = coordinator_at(now, robot)
            result = care.build_advice(
                self.config(robotic_mower=robot), weather(), "en"
            )
            obj._apply_mowing_cadence(
                result,
                dict(
                    last_registered_run=(now - timedelta(hours=12)).isoformat(),
                    estimated_grass_height=55,
                ),
                "en",
            )
            key = "robot_mower_should_run" if robot else "should_mow"
            self.assertFalse(result[key]["value"])
            self.assertIn("next_mowing_not_before", result[key]["attributes"])
        obj, _ = coordinator_at(now)
        obj._stored_data = {
            "robot_mower": dict(
                last_mowed_at=(now - timedelta(days=1)).isoformat(),
                last_mowed_height=40,
            ),
            "growth_history": [
                dict(time=(now - timedelta(days=1)).isoformat(), growth=3)
            ],
        }
        self.assertEqual(obj._robot_mower_status_data({})["estimated_grass_height"], 43)

    def test_drought_recovery_remembers_and_lowers_gradually(self):
        now = datetime(2026, 7, 15, 12, tzinfo=UTC)
        obj, _ = coordinator_at(now)
        dry = care.build_advice(self.config(), weather(soil_moisture=10), "en")
        obj._apply_drought_recovery(dry, "en")
        for days, expected in ((0, 60), (8, 50), (15, 40)):
            next_obj, _ = coordinator_at(now + timedelta(days=days))
            next_obj._stored_data = obj._stored_data
            result = care.build_advice(self.config(), weather(month=7), "en")
            next_obj._apply_drought_recovery(result, "en")
            self.assertEqual(result["recommended_grass_height"]["value"], expected)
            obj = next_obj

    def test_invalid_units_missing_and_stale_values(self):
        self.assertEqual(forecasts.normalize_value(50, "°F", "temperature"), 10)
        self.assertEqual(forecasts.normalize_value(1, "in", "rain"), 25.4)
        for value, unit, kind in (
            (float("nan"), "°C", "temperature"),
            (float("inf"), "mm", "rain"),
            (10, "mm/h", "rain"),
            (130, "%", "humidity"),
        ):
            self.assertIsNone(forecasts.normalize_value(value, unit, kind))
        now = datetime(2026, 5, 15, 12, tzinfo=UTC)
        _, ns = coordinator_at(now)
        self.assertFalse(
            ns["_measurement_is_fresh"](
                SimpleNamespace(last_reported=now - timedelta(hours=7))
            )
        )
        self.assertTrue(ns["_measurement_is_fresh"](SimpleNamespace(last_reported=now)))
        missing = care.build_advice(
            self.config(), weather(temperature=None, historical_temperature=None), "en"
        )
        self.assertIsNone(missing["recommended_mowing_frequency"]["value"])
        self.assertIsNone(missing["growth_rate"]["attributes"]["estimated_mm_per_day"])

    def test_forecast_intervals_boundaries_duplicates_and_gaps(self):
        now = datetime(2026, 5, 15, 12, tzinfo=UTC)
        for interval in (1, 3, 6, 24):
            forecast = [
                dict(
                    datetime=(now + timedelta(hours=h)).isoformat(),
                    precipitation=interval,
                    temperature=16,
                    templow=10,
                )
                for h in range(-interval, 120, interval)
            ]
            self.assertEqual(
                forecasts._forecast_precipitation_days(forecast, 3, now), 72
            )
            self.assertEqual(forecasts._forecast_precipitation(forecast, now), 24)
            summary = forecasts.forecast_temperature(forecast, now)
            self.assertEqual(summary["forecast_temperature_72h"], 13)
            self.assertEqual(summary["forecast_temperature_coverage_hours"], 72)
            self.assertEqual(
                forecasts._forecast_precipitation(forecast + forecast, now), 24
            )
        forecast = [
            dict(datetime=(now - timedelta(hours=12)).isoformat(), precipitation=24),
            dict(datetime=(now + timedelta(hours=12)).isoformat(), precipitation=24),
        ]
        self.assertEqual(
            forecasts._forecast_precipitation_next_hours(forecast, 6, now), 6
        )
        self.assertIsNone(
            forecasts._forecast_precipitation([dict(precipitation=50)], now)
        )
        sparse = [
            dict(datetime=(now + timedelta(hours=h)).isoformat(), temperature=15)
            for h in (0, 1, 70)
        ]
        self.assertEqual(
            forecasts.forecast_temperature(sparse, now)[
                "forecast_temperature_coverage_hours"
            ],
            3,
        )

    def test_bilingual_summaries_fit_home_assistant_state_limit(self):
        for lang in ("da", "en"):
            for month in range(1, 13):
                for inputs in (
                    dict(),
                    dict(temperature=8, historical_temperature=8),
                    dict(soil_moisture=5),
                    dict(weather_state="rainy"),
                ):
                    result = care.build_advice(
                        self.config(), weather(month=month, **inputs), lang
                    )
                    self.assertLessEqual(
                        len(result["care_recommendation"]["value"]), 255
                    )
                    self.assertNotIn("klippesæson", str(result).lower())
                    self.assertNotIn(
                        "mowing season", result["care_recommendation"]["value"].lower()
                    )


class CoordinatorUpdates(unittest.IsolatedAsyncioTestCase):
    async def test_complete_refresh_preserves_recommended_height_after_robot_run(self):
        now = datetime(2026, 5, 15, 12, tzinfo=UTC)
        obj, _ = coordinator_at(now)
        obj.config["robot_mower_entity"] = "lawn_mower.robot"
        obj.hass.states.get = lambda _: SimpleNamespace(state="returning")
        obj._stored_data = {
            "robot_mower": dict(
                entity_id="lawn_mower.robot",
                state="mowing",
                last_mowed_at=(now - timedelta(days=3)).isoformat(),
                last_mowed_height=80,
            ),
            "growth_history": [
                dict(time=(now - timedelta(days=3)).isoformat(), growth=3)
            ],
        }
        obj._async_get_forecasts = AsyncMock(return_value={})
        obj._read_weather_data = lambda _: weather()
        obj._update_weather_history = lambda w: (w, False)
        advice = await obj._async_update_data()
        stored_height = obj._stored_data["robot_mower"]["last_mowed_height"]
        self.assertEqual(stored_height, advice["recommended_grass_height"]["value"])
        self.assertGreaterEqual(stored_height, 89 * 2 / 3)
        self.assertFalse(advice["robot_mower_should_run"]["value"])
        obj._store.async_save.assert_awaited_once()

    async def test_initial_expected_height_does_not_invent_a_completed_run(self):
        now = datetime(2026, 5, 15, 12, tzinfo=UTC)
        obj, _ = coordinator_at(now)
        await obj.async_set_estimated_grass_height(90)
        self.assertNotIn("last_mowed_at", obj._stored_data["robot_mower"])
        obj._stored_data["growth_history"] = [dict(time=now.isoformat(), growth=3)]
        self.assertEqual(obj.estimated_grass_height, 90)

    async def test_manual_registration_uses_recommended_expected_height(self):
        now = datetime(2026, 5, 15, 12, tzinfo=UTC)
        obj, _ = coordinator_at(now, robot=False)
        obj.data = care.build_advice({}, weather(), "en")
        await obj.async_set_last_mowed_date(now.date())
        self.assertEqual(obj.last_mowed_date, now.date())
        self.assertEqual(
            obj._stored_data["manual_mower"]["last_mowed_height"],
            obj.data["recommended_grass_height"]["value"],
        )
        with self.assertRaises(ValueError):
            await obj.async_set_last_mowed_date((now + timedelta(days=1)).date())


class DataAndCadenceScenarios(unittest.TestCase):
    def test_sensor_units_are_normalized_before_the_rules(self):
        now = datetime(2026, 5, 15, 12, tzinfo=UTC)
        obj, _ = coordinator_at(now)
        obj.config.update(
            weather_entity="weather.lawn",
            temperature_sensor="sensor.temp",
            rain_sensor="sensor.rain",
        )
        states = {
            "weather.lawn": SimpleNamespace(
                state="cloudy",
                attributes=dict(
                    temperature=65,
                    temperature_unit="°F",
                    precipitation_unit="in",
                    humidity=60,
                ),
            ),
            "sensor.temp": SimpleNamespace(
                state="50", attributes={"unit_of_measurement": "°F"}
            ),
            "sensor.rain": SimpleNamespace(
                state="1", attributes={"unit_of_measurement": "in"}
            ),
            "sun.sun": SimpleNamespace(state="above_horizon"),
        }
        obj.hass.states.get = states.get
        forecast = [
            dict(
                datetime=(now + timedelta(days=day)).isoformat(),
                temperature=68,
                templow=50,
                precipitation=1,
            )
            for day in range(3)
        ]
        result = obj._read_weather_data({"daily": forecast})
        self.assertEqual(result.temperature, 10)
        self.assertEqual(result.recent_rain, 25.4)
        self.assertEqual(result.forecast_rain, 25.4)
        self.assertEqual(result.forecast_temperature_72h, 15)
        self.assertEqual(result.forecast_min_temperature_72h, 10)
        self.assertEqual(result.forecast_temperature_coverage_hours, 72)
        states["sensor.rain"].attributes["unit_of_measurement"] = "mm/h"
        self.assertIsNone(obj._read_weather_data({}).recent_rain)
        states["weather.lawn"].last_reported = now - timedelta(hours=7)
        self.assertEqual(
            obj._read_weather_data({}).unavailable_required_entities, ("weather.lawn",)
        )

    def test_long_running_height_model_survives_history_pruning(self):
        now = datetime(2026, 5, 15, 12, tzinfo=UTC)
        obj, _ = coordinator_at(now)
        obj._stored_data = {
            "robot_mower": dict(
                last_mowed_at=(now - timedelta(days=30)).isoformat(),
                last_mowed_height=40,
            ),
            "growth_history": [
                dict(time=(now - timedelta(days=30)).isoformat(), growth=2),
                dict(time=(now - timedelta(hours=1)).isoformat(), growth=2),
            ],
        }
        before = obj._robot_mower_status_data({})["estimated_grass_height"]
        obj._update_growth_history(care.build_advice({}, weather(), "en"))
        after = obj._robot_mower_status_data({})["estimated_grass_height"]
        self.assertEqual(before, 100)
        self.assertEqual(before, after)
        self.assertEqual(obj._stored_data["robot_mower"]["last_mowed_height"], 40)

    def test_manual_low_growth_cut_requires_height_and_obeys_weather(self):
        now = datetime(2026, 10, 15, 12, tzinfo=UTC)
        obj, _ = coordinator_at(now, robot=False)
        for wet, expected in ((False, True), (True, False)):
            w = weather(
                month=10,
                temperature=9,
                historical_temperature=9,
                weather_state="rainy" if wet else "cloudy",
            )
            result = care.build_advice({}, w, "da")
            obj._apply_mowing_cadence(
                result,
                dict(
                    last_registered_run=(now - timedelta(days=10)).isoformat(),
                    estimated_grass_height=75,
                ),
                "da",
            )
            self.assertEqual(result["should_mow"]["value"], expected)
            if wet:
                self.assertIn("vådt", result["should_mow"]["attributes"]["reason"])

    def test_locked_false_has_an_honest_reason_when_weather_improves(self):
        now = datetime(2026, 5, 15, 12, tzinfo=UTC)
        obj, _ = coordinator_at(now, robot=False)
        obj.config["mowing_update_frequency"] = "daily"
        rainy = care.build_advice({}, weather(weather_state="rainy"), "da")
        obj._apply_mowing_frequency_lock(rainy)
        dry = care.build_advice({}, weather(), "da")
        obj._apply_mowing_frequency_lock(dry)
        self.assertFalse(dry["should_mow"]["value"])
        self.assertIn(
            "Afventer næste vurdering", dry["should_mow"]["attributes"]["reason"]
        )
        obj._refresh_care_recommendation(dry, "da")
        self.assertIn("Vent med klipning", dry["care_recommendation"]["value"])

    def test_new_options_have_safe_defaults_and_reject_invalid_values(self):
        tree = ast.parse((ROOT / "config_flow.py").read_text())
        wanted = {
            "_clean_user_input",
            "_care_setting_errors",
            "_is_whole_number_in_range",
            "_is_valid_hour",
        }
        methods = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]
        ns = dict(vars(constants), Any=object)
        exec(
            compile(
                ast.Module(body=methods, type_ignores=[]),
                "config_flow.py",
                "exec",
                flags=__future__.annotations.compiler_flag,
            ),
            ns,
        )
        cleaned = ns["_clean_user_input"]({"robot_mower_allow_night": True})
        self.assertTrue(cleaned["robot_mower_allow_night"])
        self.assertEqual(cleaned["mowing_start_hour"], 9)
        self.assertEqual(ns["_care_setting_errors"](cleaned), {})
        for key, value in (
            ("mowing_start_hour", 24),
            ("fertilizer_type", "unknown"),
            ("fertilizer_effect_days", 0),
            ("fertilizer_dose_g_m2", float("nan")),
            ("fertilizer_label_dose_g_m2", -1),
        ):
            self.assertIn(key, ns["_care_setting_errors"]({**cleaned, key: value}))
