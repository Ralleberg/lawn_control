"""Rule regressions runnable without installing Home Assistant."""

import __future__

import ast
import importlib.util
import json
import sys
import types
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "lawn_control"
PACKAGE = "_lawn_rule_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


care = load(f"{PACKAGE}.rules.care", ROOT / "rules" / "care.py")
mowing = sys.modules[f"{PACKAGE}.rules.mowing"]
moisture = sys.modules[f"{PACKAGE}.rules.moisture"]
maintenance = sys.modules[f"{PACKAGE}.rules.maintenance"]


def weather(**updates):
    values = dict(
        month=5, day=1, seasonal_temperature_72h=None,
        seasonal_temperature_coverage_hours=0, temperature=18, humidity=60, weather_state="cloudy",
        forecast_condition="cloudy", recent_rain=0, recent_hour_rain=0,
        historical_rain=20, historical_rain_24h=0, forecast_rain=0,
        forecast_rain_5_days=0, forecast_rain_next_24h=0,
        forecast_drying_pressure_72h=0, soil_moisture=40,
        historical_temperature=18, historical_humidity=60, sun_is_up=True,
    )
    values.update(updates)
    return SimpleNamespace(**values)


class LawnRulesTest(unittest.TestCase):
    def test_monthly_floors_for_both_mowers(self):
        expected = {
            False: (60, 60, 60, 60, 50, 50, 50, 50, 50, 60, 60, 60),
            True: (50, 50, 50, 40, 40, 40, 40, 40, 40, 50, 50, 50),
        }
        for robot, floors in expected.items():
            for month, floor in enumerate(floors, 1):
                for lawn_type in ("regular", "ornamental", "shade", "wear_tolerant"):
                    for rain in (0, 7, 30):
                        with self.subTest(robot=robot, month=month, lawn=lawn_type, rain=rain):
                            result = care.recommended_grass_height(dict(
                                robotic_mower=robot, lawn_type=lawn_type,
                                min_grass_height=20, max_grass_height=30,
                            ), weather(month=month, historical_rain=rain), "da")
                            attrs = result["attributes"]
                            self.assertEqual(attrs["seasonal_min_height"], floor)
                            self.assertGreaterEqual(result["value"], floor)
                            self.assertLessEqual(attrs["min_height"], result["value"])
                            self.assertLessEqual(result["value"], attrs["max_height"])
                            self.assertTrue(attrs["configured_max_overridden"])

    def test_user_minimum_and_non_multiple_rounding(self):
        for minimum, maximum in ((73, 73), (63, 64), (64, 66), (68, 65)):
            result = care.recommended_grass_height(dict(
                min_grass_height=minimum, max_grass_height=maximum,
                lawn_type="ornamental",
            ), weather(historical_rain=7), "en")
            self.assertGreaterEqual(result["value"], minimum)
            self.assertLessEqual(result["value"], max(minimum, maximum))

    def test_forecast_rain_does_not_defeat_drying_protection(self):
        result = care.recommended_grass_height(dict(
            robotic_mower=True, min_grass_height=40, max_grass_height=70,
        ), weather(forecast_rain_next_24h=15, forecast_drying_pressure_72h=2,
                   soil_moisture=None), "en")
        self.assertEqual(result["value"], 70)

    def test_cold_and_winter_growth_cannot_restart_with_bonuses(self):
        for month, temperature in ((1, 18), (11, 18), (5, 4)):
            result = care.build_advice(dict(
                robotic_mower=True, fertilizer_n_percent=30,
                days_since_fertilizer=0, water_during_drought=True,
            ), weather(month=month, temperature=temperature), "en")
            self.assertEqual(result["growth_rate"]["value"], "stopped")
            self.assertEqual(result["growth_rate"]["attributes"]["estimated_mm_per_day"], 0)
            self.assertFalse(result["robot_mower_should_run"]["value"])
            self.assertFalse(result["fertilizer_day"]["value"])

    def test_measured_dry_soil_overrides_watering_preference(self):
        result = care.build_advice(dict(
            robotic_mower=True, water_during_drought=True,
        ), weather(soil_moisture=10), "da")
        self.assertFalse(result["should_mow"]["value"])
        self.assertFalse(result["robot_mower_should_run"]["value"])
        self.assertFalse(result["fertilizer_day"]["value"])
        self.assertFalse(moisture.has_moisture_support(
            {"water_during_drought": True}, weather(soil_moisture=10)))

    def test_snow_blocks_mowing_even_with_mild_air_temperature(self):
        result = care.build_advice({"robotic_mower": True},
                                   weather(weather_state="snowy", temperature=10), "en")
        self.assertFalse(result["should_mow"]["value"])
        self.assertFalse(result["robot_mower_should_run"]["value"])

    def test_approaching_drought_blocks_both_mowers(self):
        result = care.build_advice({"robotic_mower": True}, weather(
            forecast_drying_pressure_72h=2, soil_moisture=None), "da")
        self.assertFalse(result["should_mow"]["value"])
        self.assertFalse(result["robot_mower_should_run"]["value"])

    def test_scarifying_requires_intensive_care_and_active_growth(self):
        for level in ("low", "normal", "high"):
            for growth in ("stopped", "slow", "normal", "fast"):
                result = maintenance.calculate_verticut_advice(
                    {"care_level": level}, weather(), {"value": "low"},
                    {"value": growth}, "en")
                self.assertEqual(result["value"], level == "high" and growth in ("normal", "fast"))
                self.assertIn("aftercare", result["attributes"])

    def test_scarifying_season_boundaries(self):
        for month, day, allowed in ((4, 30, False), (5, 1, True),
                                    (9, 15, True), (9, 16, False), (10, 1, False)):
            result = maintenance.calculate_verticut_advice(
                {"care_level": "high"}, weather(month=month, day=day),
                {"value": "low"}, {"value": "normal"}, "da")
            self.assertEqual(result["value"], allowed)

    def test_one_third_cut_protection_overrides_preferred_maximum(self):
        height = care.recommended_grass_height(dict(
            robotic_mower=True, min_grass_height=40, max_grass_height=50,
        ), weather(), "da")
        mowing.protect_cut_height(height, 100, "da")
        self.assertEqual(height["value"], 70)
        self.assertGreaterEqual(height["value"], 100 * 2 / 3)
        self.assertTrue(height["attributes"]["configured_max_overridden"])
        self.assertEqual(height["attributes"]["estimated_height_before_cut"], 100)
        for invalid in (None, float("nan"), float("inf"), -1):
            mowing.protect_cut_height(height, invalid, "en")
            self.assertEqual(height["value"], 70)

    def test_catch_up_cannot_bypass_season_snow_or_dry_soil(self):
        tree = ast.parse((ROOT / "coordinator.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LawnControlCoordinator")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_robot_catch_up_hard_blocked")
        namespace = dict(Any=object, LawnWeatherData=object,
                         mowing_safety_blocks=mowing.mowing_safety_blocks)
        exec(compile(ast.Module(body=[method], type_ignores=[]), "coordinator.py", "exec", flags=__future__.annotations.compiler_flag), namespace)
        coordinator = SimpleNamespace(config={"water_during_drought": True})
        for conditions in (dict(month=11), dict(weather_state="snowy"), dict(soil_moisture=10)):
            self.assertTrue(namespace["_robot_catch_up_hard_blocked"](
                coordinator, {}, weather(**conditions)))

    def test_cool_temperature_reduces_growth_and_freezes_at_threshold(self):
        estimates = []
        for temperature in (4, 6, 7, 8, 10, 12, 18):
            result = care.build_advice({"robotic_mower": True}, weather(
                temperature=temperature, historical_temperature=temperature), "da")
            estimates.append(result["growth_rate"]["attributes"]["estimated_mm_per_day"])
        self.assertEqual(estimates[:2], [0, 0])
        self.assertEqual(estimates, sorted(estimates))
        self.assertGreater(estimates[-1], estimates[2])

    def test_warm_spike_does_not_restart_cold_growth(self):
        result = care.build_advice({"robotic_mower": True}, weather(
            temperature=15, historical_temperature=4), "da")
        self.assertEqual(result["growth_rate"]["attributes"]["estimated_mm_per_day"], 0)
        self.assertFalse(result["robot_mower_should_run"]["value"])
        self.assertEqual(result["growth_rate"]["attributes"]["mowing_season_status"], "cold_pause")

    def test_cold_pause_storage_and_spring_restart(self):
        config = {"robotic_mower": True}
        def status(previous=None, **inputs):
            result = care.build_advice(config, weather(**inputs), "da", previous)
            self.assertEqual(result["care_recommendation"]["attributes"]["mowing_season_status"],
                             result["growth_rate"]["attributes"]["mowing_season_status"])
            return result
        cold = status(month=10, temperature=4, historical_temperature=5)
        self.assertEqual(cold["growth_rate"]["attributes"]["mowing_season_status"], "cold_pause")
        self.assertFalse(cold["growth_rate"]["attributes"]["winter_storage_recommended"])
        sustained = status(month=10, temperature=4, historical_temperature=5,
                           seasonal_temperature_72h=5, seasonal_temperature_coverage_hours=71)
        self.assertTrue(sustained["growth_rate"]["attributes"]["winter_storage_recommended"])
        winter = status(month=1, temperature=15, historical_temperature=15)
        self.assertEqual(winter["growth_rate"]["attributes"]["mowing_season_status"], "winter_pause")
        warming = status("winter_pause", month=3, temperature=7, historical_temperature=7)
        self.assertEqual(warming["growth_rate"]["attributes"]["mowing_season_status"], "warming_up")
        ready = status("warming_up", month=3, temperature=8, historical_temperature=8)
        self.assertEqual(ready["growth_rate"]["attributes"]["mowing_season_status"], "ready")
        still_ready = status("ready", month=3, temperature=7, historical_temperature=7)
        self.assertEqual(still_ready["growth_rate"]["attributes"]["mowing_season_status"], "ready")
        cold_again = status("ready", month=3, temperature=6, historical_temperature=7)
        self.assertEqual(cold_again["growth_rate"]["attributes"]["mowing_season_status"], "cold_pause")

    def test_readiness_does_not_override_wet_mowing_blocks(self):
        result = care.build_advice({"robotic_mower": True}, weather(weather_state="rainy"), "en")
        self.assertEqual(result["growth_rate"]["attributes"]["mowing_season_status"], "ready")
        self.assertFalse(result["robot_mower_should_run"]["value"])
        self.assertIn("Ready for the mowing season", result["care_recommendation"]["value"])

    def test_missing_temperature_is_not_reported_as_ready(self):
        result = care.build_advice({"robotic_mower": True}, weather(
            temperature=None, historical_temperature=None), "da")
        self.assertEqual(result["growth_rate"]["attributes"]["mowing_season_status"], "unknown")
        self.assertFalse(result["robot_mower_should_run"]["value"])

    def test_cool_humid_air_reduces_rain_deficit_risk(self):
        drought = sys.modules[f"{PACKAGE}.rules.drought"]
        common = dict(historical_rain=0, recent_rain=0, forecast_rain_5_days=0,
                      soil_moisture=None, weather_state="sunny", month=10)
        cool = drought.calculate_drought_risk({}, weather(**common,
            temperature=10, historical_temperature=10, humidity=90,
            historical_humidity=90), "da")
        hot = drought.calculate_drought_risk({}, weather(**common,
            temperature=30, historical_temperature=30, humidity=30,
            historical_humidity=30), "da")
        self.assertEqual(cool["value"], "low")
        self.assertEqual(hot["value"], "critical")
        self.assertLess(cool["attributes"]["rain_deficit_weight"], hot["attributes"]["rain_deficit_weight"])

    def test_humid_air_and_watering_do_not_hide_measured_dry_soil(self):
        drought = sys.modules[f"{PACKAGE}.rules.drought"]
        for soil, risk in ((20, "medium"), (10, "high")):
            result = drought.calculate_drought_risk({"water_during_drought": True}, weather(
                temperature=5, historical_temperature=5, humidity=95,
                historical_humidity=95, soil_moisture=soil), "en")
            self.assertEqual(result["value"], risk)

    def test_vpd_uses_paired_samples_and_preserves_recent_drying(self):
        drought = sys.modules[f"{PACKAGE}.rules.drought"]
        vpd = drought._observed_vapour_pressure_deficit(weather(
            temperature=5, humidity=95, historical_temperature=20, historical_humidity=40))
        self.assertGreater(vpd, 1)
        self.assertIsNone(drought._observed_vapour_pressure_deficit(weather(
            temperature=None, historical_temperature=None)))

    def test_temperature_history_requires_coverage_not_a_single_sample(self):
        tree = ast.parse((ROOT / "coordinator.py").read_text())
        names = {"_seasonal_temperature_summary", "_average", "_as_float", "_growth_total_since"}
        methods = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        namespace = dict(Any=object, datetime=datetime, timedelta=timedelta,
                         _parse_datetime=lambda value: datetime.fromisoformat(value) if value else None)
        exec(compile(ast.Module(body=methods, type_ignores=[]), "coordinator.py", "exec", flags=__future__.annotations.compiler_flag), namespace)
        now = datetime(2026, 10, 12, 12)
        summary = namespace["_seasonal_temperature_summary"]
        self.assertEqual(summary([dict(time=now.isoformat(), temperature=4)], now), (4, 0))
        samples = [dict(time=(now-timedelta(hours=h)).isoformat(), temperature=4) for h in range(72)]
        self.assertEqual(summary(samples, now), (4, 71))
        # More refreshes within an hour cannot manufacture three days of history.
        samples = [dict(time=(now-timedelta(minutes=m)).isoformat(), temperature=4) for m in range(72)]
        self.assertEqual(summary(samples, now), (4, 0))
        growth_samples = [dict(time=(now-timedelta(hours=24)).isoformat(), growth=2),
                          dict(time=(now-timedelta(hours=12)).isoformat(), growth=0)]
        self.assertEqual(namespace["_growth_total_since"](growth_samples, now-timedelta(hours=24), now), 1)

    def test_season_attributes_have_danish_and_english_labels(self):
        attributes = care.build_advice({}, weather(), "da")["growth_rate"]["attributes"]
        keys = {"mowing_season_status", "mowing_season_label", "mowing_season_reason",
                "winter_storage_recommended", "temperature", "historical_temperature",
                "growth_temperature", "temperature_growth_factor", "seasonal_temperature_72h",
                "seasonal_temperature_coverage_hours", "growth_stop_temperature", "growth_resume_temperature"}
        self.assertTrue(keys <= set(attributes))
        for language in ("da", "en"):
            translation = json.loads((ROOT / "translations" / f"{language}.json").read_text())
            labels = translation["entity"]["sensor"]["growth_rate"]["state_attributes"]
            self.assertTrue(keys <= set(labels))

    def test_localizations_have_matching_keys(self):
        for module in (care, mowing, maintenance, moisture,
                       sys.modules[f"{PACKAGE}.rules.season"], sys.modules[f"{PACKAGE}.rules.drought"]):
            self.assertEqual(set(module._texts("da")), set(module._texts("en")))

    def test_lock_cannot_preserve_unsafe_mowing_or_old_height(self):
        # Compile the actual coordinator method without the Home Assistant runtime.
        tree = ast.parse((ROOT / "coordinator.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LawnControlCoordinator")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_apply_mowing_frequency_lock")
        now = datetime(2026, 10, 10, 12)
        namespace = dict(Any=object, deepcopy=deepcopy, dt_util=SimpleNamespace(now=lambda: now),
                         MOWING_LOCK_VERSION=6, CONF_MOWING_UPDATE_FREQUENCY="mowing_update_frequency",
                         _mowing_update_frequency=lambda _: "daily",
                         _mowing_period_start=lambda *_: now,
                         _next_mowing_period_start=lambda start, _: start + timedelta(days=1))
        exec(compile(ast.Module(body=[method], type_ignores=[]), "coordinator.py", "exec", flags=__future__.annotations.compiler_flag), namespace)
        coordinator = SimpleNamespace(config={}, daily_update_hour=8, _stored_data={})
        advice = dict(should_mow=dict(value=True, attributes=dict(recommended_height=40)),
                      recommended_grass_height=dict(value=50))
        apply = namespace["_apply_mowing_frequency_lock"]
        apply(coordinator, advice)
        live = dict(should_mow=dict(value=False, attributes=dict(
            recommended_height=60, blocking_factors=["Frost"], reason="Frost")),
            recommended_grass_height=dict(value=60))
        apply(coordinator, live)
        self.assertFalse(live["should_mow"]["value"])
        self.assertEqual(live["should_mow"]["attributes"]["recommended_height"], 60)
        self.assertEqual(live["should_mow"]["attributes"]["reason"], "Frost")


if __name__ == "__main__":
    unittest.main()
