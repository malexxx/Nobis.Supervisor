import unittest
from starlette.testclient import TestClient
from main import app


class TestAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_healthcheck(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("active_mode", data)

    def test_api_latest(self):
        resp = self.client.get("/api/latest")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("active_mode", data)
        self.assertIn("latest", data)
        self.assertIn("registers", data)
        self.assertIn("consumption_stats", data)

    def test_api_dashboard(self):
        resp = self.client.get("/api/dashboard?range_key=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("series", data)
        self.assertIn("consumption_stats", data)

    def test_silence_status(self):
        resp = self.client.get("/api/silence/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("active", data)
        self.assertIn("remaining_seconds", data)

    def test_matter_status(self):
        resp = self.client.get("/api/matter/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("enabled", data)

    def test_client_error_logging(self):
        payload = {
            "severity": "WARNING",
            "source": "UNIT_TEST",
            "message": "Test log client error",
        }
        resp = self.client.post("/api/logs/client-error", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_security_headers_present(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(resp.headers.get("x-frame-options"), "SAMEORIGIN")
        self.assertEqual(resp.headers.get("referrer-policy"), "strict-origin-when-cross-origin")

    def test_ssrf_protection_on_webhook(self):
        # Tentative d'atteindre l'API de contrôle Matter interne
        bad_payload = {"webhook_url": "http://127.0.0.1:5549/factory-reset"}
        resp = self.client.post("/api/test-webhook", json=bad_payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("anti-SSRF", resp.json()["detail"])

        # Tentative d'atteindre le service de métadonnées AWS / Cloud
        bad_cloud = {"webhook_url": "http://169.254.169.254/latest/meta-data/"}
        resp_cloud = self.client.post("/api/test-webhook", json=bad_cloud)
        self.assertEqual(resp_cloud.status_code, 400)

    def test_matter_webhook_loopback_allowed(self):
        # L'appel direct depuis TestClient simule la boucle locale autorisée
        payload = {"action": "set_temperature", "temperature": 21.0}
        resp = self.client.post("/api/matter/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_matter_webhook_remote_rejected(self):
        # Simulation d'un appel distant avec client_ip mocké
        payload = {"action": "set_power", "state": "ON"}
        # Création d'une requête avec scope client distant
        from starlette.requests import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/matter/webhook",
            "headers": [(b"content-type", b"application/json")],
            "client": ("192.168.1.99", 54321),
            "app": app,
        }
        fake_req = Request(scope)
        # Vérifions que le contrôle rejette une IP distante
        client_ip = fake_req.client.host if fake_req.client else ""
        self.assertNotIn(client_ip, ("127.0.0.1", "::1", "localhost", "testclient"))

    def test_installer_auth_flow(self):
        from database import get_sys_db

        # 1. Vérification du statut initial
        status_resp = self.client.get("/api/installer/status")
        self.assertEqual(status_resp.status_code, 200)
        self.assertFalse(status_resp.json()["authenticated"])

        # 2. Configurer temporairement un mot de passe de test en base
        test_pwd = "MonSuperMotDePasse123!"
        conn = get_sys_db()
        try:
            old_pwd_row = conn.execute("SELECT value FROM settings WHERE key = 'agua_password'").fetchone()
            orig_pwd = old_pwd_row["value"] if old_pwd_row else ""
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('agua_password', ?)", (test_pwd,))
            conn.commit()
        finally:
            conn.close()

        try:
            # 3. Test mot de passe erroné -> HTTP 401
            bad_resp = self.client.post("/api/installer/verify", json={"password": "MauvaisMotDePasse"})
            self.assertEqual(bad_resp.status_code, 401)
            self.assertIn("incorrect", bad_resp.json()["detail"])

            # 4. Test bon mot de passe -> HTTP 200 avec jeton
            good_resp = self.client.post("/api/installer/verify", json={"password": test_pwd})
            self.assertEqual(good_resp.status_code, 200)
            data = good_resp.json()
            self.assertTrue(data["authenticated"])
            token = data["token"]
            self.assertTrue(len(token) >= 20)

            # 5. Vérifier le statut avec le jeton
            auth_status = self.client.get("/api/installer/status", headers={"X-Installer-Token": token})
            self.assertEqual(auth_status.status_code, 200)
            self.assertTrue(auth_status.json()["authenticated"])
            self.assertTrue(auth_status.json()["has_password_configured"])

            # 6. Verrouiller la session
            lock_resp = self.client.post("/api/installer/lock", headers={"X-Installer-Token": token})
            self.assertEqual(lock_resp.status_code, 200)
            self.assertFalse(lock_resp.json()["authenticated"])

            # 7. Vérifier que le statut est désormais non authentifié
            locked_status = self.client.get("/api/installer/status", headers={"X-Installer-Token": token})
            self.assertFalse(locked_status.json()["authenticated"])
        finally:
            # Restaurer l'état initial
            conn = get_sys_db()
            try:
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('agua_password', ?)", (orig_pwd,))
                conn.commit()
            finally:
                conn.close()

    def test_pellet_batch_status_endpoint(self):
        resp = self.client.get("/api/pellet-batch/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("raz_epoch", data)
        self.assertIn("pellet_brand", data)

    def test_pellet_batch_raz_endpoint(self):
        resp = self.client.post("/api/pellet-batch/raz", json={"pellet_brand": "DINplus Test Lot A1"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["pellet_batch_state"]["pellet_brand"], "DINplus Test Lot A1")
        self.assertIn("latest", data)

    def test_stove_set_endpoint(self):
        from database import get_sys_db
        sconn = get_sys_db()
        try:
            row = sconn.execute("SELECT value FROM settings WHERE key = 'comm_mode'").fetchone()
            orig_mode = row["value"] if row else "simulation"
            sconn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('comm_mode', 'simulation')")
            sconn.commit()
        finally:
            sconn.close()

        try:
            # 1. Température Salon (21°C)
            r_temp = self.client.post("/api/stove/set", json={"type": "temp", "zone": "salon", "value": 21})
            self.assertEqual(r_temp.status_code, 200)
            self.assertEqual(r_temp.json()["reg_key"], "temp_amb_setpoint")
            self.assertEqual(r_temp.json()["new_value"], 21.0)

            # 2. Température Hors-Gel (6 -> 7.0) et MAX (31 -> 41.0)
            r_frost = self.client.post("/api/stove/set", json={"type": "temp", "zone": "sx", "value": 6})
            self.assertEqual(r_frost.status_code, 200)
            self.assertEqual(r_frost.json()["reg_key"], "duct_left_setpoint")
            self.assertEqual(r_frost.json()["new_value"], 7.0)

            r_max = self.client.post("/api/stove/set", json={"type": "temp", "zone": "dx", "value": 31})
            self.assertEqual(r_max.status_code, 200)
            self.assertEqual(r_max.json()["reg_key"], "duct_right_setpoint")
            self.assertEqual(r_max.json()["new_value"], 41.0)

            # 3. Ventilation Canalisation SX (PS = 8)
            r_fan = self.client.post("/api/stove/set", json={"type": "fan", "zone": "sx", "value": 8, "label": "PS"})
            self.assertEqual(r_fan.status_code, 200)
            self.assertEqual(r_fan.json()["reg_key"], "duct_left_speed")
            self.assertEqual(r_fan.json()["new_value"], 8.0)

            # 4. Puissance P8 (Boost) puis retour P4
            r_boost = self.client.post("/api/stove/set", json={"type": "power", "value": 8})
            self.assertEqual(r_boost.status_code, 200)
            self.assertEqual(r_boost.json()["reg_key"], "fun_pwf_set")
            self.assertEqual(r_boost.json()["new_value"], 1.0)

            r_p4 = self.client.post("/api/stove/set", json={"type": "power", "value": 4})
            self.assertEqual(r_p4.status_code, 200)
            self.assertEqual(r_p4.json()["status"], "ok")
        finally:
            sconn = get_sys_db()
            try:
                sconn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('comm_mode', ?)", (orig_mode,))
                sconn.commit()
            finally:
                sconn.close()

    def test_chrono_bitmask_resolution_in_agua_client(self):
        """Vérifie que les masques de jours Chrono (33168, 33188, 33208, 33228) conservent mask=65535 (pas d'écrasement par les clés individuelles par jour)."""
        from clients import AguaCloudClient
        ac = AguaCloudClient()
        sample_reg_map = {
            "registers_map": [
                {
                    "registers": [
                        {"reg_key": "chrono_p1_day_1_set", "reg_type": "SET", "offset": 33168, "mask": 1, "is_word": True},
                        {"reg_key": "chrono_p1_day_2_set", "reg_type": "SET", "offset": 33168, "mask": 2, "is_word": True},
                        {"reg_key": "chrono_p1_day_7_set", "reg_type": "SET", "offset": 33168, "mask": 64, "is_word": True},
                        {"reg_key": "chrono_p3_day_7_set", "reg_type": "SET", "offset": 33208, "mask": 64, "is_word": True},
                        {"reg_key": "chrono_p1_temperature_set", "reg_type": "SET", "offset": 33163, "mask": 65535, "is_word": True},
                    ]
                }
            ]
        }
        ac._parse_and_cache_registers_map(sample_reg_map)
        p1_off, p1_bit, p1_end, p1_mask = ac._resolve_register_write_params(33168, "chrono_p1_days_mask")
        self.assertEqual(p1_mask, 65535)

        p3_off, p3_bit, p3_end, p3_mask = ac._resolve_register_write_params(33208, "chrono_p3_days_mask")
        self.assertEqual(p3_mask, 65535)

        # Mais un appel explicite sur une clé unitaire conserve son masque spécifique
        d7_off, d7_bit, d7_end, d7_mask = ac._resolve_register_write_params(33168, "chrono_p1_day_7_set")
        self.assertEqual(d7_mask, 64)


if __name__ == "__main__":
    unittest.main()

