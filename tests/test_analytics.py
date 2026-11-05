import unittest
from telemetry import (
    calculate_forecast_runway,
    evaluate_pellet_quality,
    evaluate_exhaust_quality,
    enrich_telemetry_record,
)
from database import get_db, init_all_databases


class TestAnalytics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_all_databases()

    def test_calculate_forecast_runway_positive(self):
        weather = {
            "weather_temp_ext": 10.0,
            "forecast_daily_means": [8.0, 7.5, 9.0, 10.0, 6.0],
        }
        runway = calculate_forecast_runway(150.0, weather)
        self.assertGreater(runway, 0.0)

    def test_calculate_forecast_runway_empty_stock(self):
        weather = {"weather_temp_ext": 10.0, "forecast_daily_means": []}
        runway = calculate_forecast_runway(0.0, weather)
        self.assertEqual(runway, 0.0)

    def test_evaluate_pellet_quality_active_nominal(self):
        # En travail P4 (cible 430 + 4*40 = 590), sonde flamme à 590°C -> Optimale
        res = evaluate_pellet_quality(
            status_code=5,
            power_actual=4,
            temp_probe_k=590.0,
            temp_fumes=180.0,
            current_trim=0,
            flame_delta_avg=0.0,
            stable_minutes=15,
            power_level=4,
        )
        self.assertEqual(res["status"], "active")
        self.assertEqual(res["quality_color"], "emerald")
        self.assertFalse(res["action_needed"])

    def test_evaluate_pellet_quality_stabilizing(self):
        # Moins de 15 min stables -> status stabilizing
        res = evaluate_pellet_quality(
            status_code=5,
            power_actual=4,
            temp_probe_k=590.0,
            temp_fumes=180.0,
            current_trim=0,
            flame_delta_avg=None,
            stable_minutes=5,
            power_level=4,
        )
        self.assertEqual(res["status"], "stabilizing")

    def test_evaluate_exhaust_quality_nominal(self):
        # En travail P4 (cible 8.5 + 4*0.75 = 11.5), dépression à 11.5 Pa -> Aéraulique optimale
        res = evaluate_exhaust_quality(
            status_code=5,
            power_actual=4,
            depression_pa=11.5,
            exhaust_rpm=2200,
            current_trim=0,
            dep_delta_avg=0.0,
            stable_minutes=15,
            power_level=4,
        )
        self.assertEqual(res["status"], "active")
        self.assertEqual(res["quality_color"], "emerald")
        self.assertFalse(res["action_needed"])

    def test_enrich_telemetry_record(self):
        conn = get_db("simulation")
        try:
            fake_record = {
                "epoch": 1793800000,
                "status_code": 5,
                "power_actual": 4,
                "power_level": 4,
                "temp_probe_k": 590.0,
                "temp_fumes": 180.0,
                "depression_pa": 11.5,
                "exhaust_rpm": 2200,
                "trim_pellet_pct": 0,
                "trim_exhaust_pct": 0,
                "flame_delta_avg": 0.0,
                "dep_delta_avg": 0.0,
            }
            enriched = enrich_telemetry_record(conn, fake_record)
            self.assertIn("conso_week_kg", enriched)
            self.assertIn("conso_month_kg", enriched)
            self.assertIn("pellet_quality", enriched)
            self.assertIn("exhaust_quality", enriched)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

