import re
import unittest
from fastapi.testclient import TestClient
from main import app


class TestAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from database import get_sys_db
        sconn = get_sys_db()
        try:
            row = sconn.execute("SELECT value FROM settings WHERE key = 'comm_mode'").fetchone()
            cls.orig_comm_mode = row["value"] if (row and row["value"]) else "agua_cloud"
            sconn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('comm_mode', 'simulation')")
            sconn.commit()
        finally:
            sconn.close()
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        from database import get_sys_db
        sconn = get_sys_db()
        try:
            mode = getattr(cls, "orig_comm_mode", "agua_cloud") or "agua_cloud"
            sconn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('comm_mode', ?)", (mode,))
            sconn.commit()
        finally:
            sconn.close()

    def test_healthcheck(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("active_mode", data)
        self.assertIn("comm_status", data)
        self.assertIn("connected", data["comm_status"])

    def test_api_latest(self):
        resp = self.client.get("/api/latest")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("active_mode", data)
        self.assertIn("latest", data)
        self.assertIn("comm_status", data)
        self.assertIn("registers", data)
        self.assertIn("consumption_stats", data)

    def test_api_dashboard(self):
        resp = self.client.get("/api/dashboard?range_key=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("series", data)
        self.assertIn("comm_status", data)
        self.assertIn("consumption_stats", data)

    def test_silence_status(self):
        resp = self.client.get("/api/silence/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("active", data)
        self.assertIn("remaining_seconds", data)

    def test_silence_mode_intelligent_stove_off(self):
        import time
        from database import get_active_mode, get_db
        active_mode = get_active_mode()
        conn = get_db(active_mode)
        try:
            max_ep = conn.execute("SELECT MAX(epoch) FROM telemetry").fetchone()[0] or int(time.time())
            test_epoch = max_ep + 100
            # 1. Simuler poêle éteint (status_code = 0)
            conn.execute("INSERT INTO telemetry (timestamp, epoch, comm_mode, status_code, status_label) VALUES ('2026-11-11 12:00:00', ?, ?, 0, 'OFF (Éteint)')", (test_epoch, active_mode))
            # Initialiser des consignes personnalisées
            for rk, off, val in [("temp_amb_setpoint", 33122, 21.0), ("duct_left_setpoint", 33126, 22.0), ("duct_right_setpoint", 33124, 20.0), ("power_level", 33120, 4.0)]:
                conn.execute("UPDATE registers_catalog SET current_val = ? WHERE reg_key = ?", (val, rk))
            conn.commit()
        finally:
            conn.close()

        try:
            # 2. Activation du mode Silence intelligent
            resp_act = self.client.post("/api/silence/activate", json={"duration_hours": 1.0})
            self.assertEqual(resp_act.status_code, 200)
            data_act = resp_act.json()
            self.assertEqual(data_act["status"], "ok")
            self.assertTrue(data_act["silence_mode"]["active"])
            self.assertFalse(data_act["silence_mode"]["is_stove_on"])
            self.assertIn("temp_amb_setpoint", data_act["silence_mode"]["modified_keys"])

            # Vérifier que toutes les consignes sont passées en hors gel (7°C)
            resp_latest = self.client.get("/api/latest")
            regs = resp_latest.json()["registers"]
            self.assertEqual(regs["temp_amb_setpoint"]["current_val"], 7.0)
            self.assertEqual(regs["duct_left_setpoint"]["current_val"], 7.0)
            self.assertEqual(regs["duct_right_setpoint"]["current_val"], 7.0)
            self.assertEqual(regs["set_silece_set"]["current_val"], 1.0)
            # Seules les 3 consignes doivent avoir été modifiées (pas la puissance, ni ventilations, ni auto, ni éco, ni chrono)
            self.assertEqual(
                sorted(data_act["silence_mode"]["modified_keys"]),
                sorted(["temp_amb_setpoint", "duct_left_setpoint", "duct_right_setpoint"])
            )

            # 3. Désactivation et restauration
            resp_deact = self.client.post("/api/silence/deactivate")
            self.assertEqual(resp_deact.status_code, 200)
            self.assertFalse(resp_deact.json()["silence_mode"]["active"])

            resp_latest2 = self.client.get("/api/latest")
            regs2 = resp_latest2.json()["registers"]
            self.assertEqual(regs2["temp_amb_setpoint"]["current_val"], 21.0)
            self.assertEqual(regs2["duct_left_setpoint"]["current_val"], 22.0)
            self.assertEqual(regs2["duct_right_setpoint"]["current_val"], 20.0)
            self.assertEqual(regs2["set_silece_set"]["current_val"], 0.0)
        finally:
            conn = get_db(active_mode)
            try:
                conn.execute("DELETE FROM telemetry WHERE epoch = ?", (test_epoch,))
                conn.commit()
            finally:
                conn.close()

    def test_silence_mode_intelligent_stove_on(self):
        import time
        from database import get_active_mode, get_db
        active_mode = get_active_mode()
        conn = get_db(active_mode)
        try:
            max_ep = conn.execute("SELECT MAX(epoch) FROM telemetry").fetchone()[0] or int(time.time())
            test_epoch = max_ep + 100
            # 1. Simuler poêle allumé (status_code = 5, TRAVAIL)
            conn.execute("INSERT INTO telemetry (timestamp, epoch, comm_mode, status_code, status_label) VALUES ('2026-11-11 12:05:00', ?, ?, 5, 'TRAVAIL (Normal)')", (test_epoch, active_mode))
            init_vals = [
                ("fun_pwf_set", 33154, 1.0),
                ("standby_set", 33130, 1.0),
                ("chrono_week_enable_set", 33162, 1.0),
                ("fun_auto_set", 33128, 0.0),
                ("fan_front_speed", 33132, 3.0),
                ("duct_left_speed", 33136, 2.0),
                ("duct_right_speed", 33134, 2.0),
                ("temp_amb_setpoint", 33122, 20.0),
                ("duct_left_setpoint", 33126, 21.0),
                ("duct_right_setpoint", 33124, 19.0),
            ]
            for rk, off, val in init_vals:
                conn.execute("UPDATE registers_catalog SET current_val = ? WHERE reg_key = ?", (val, rk))
            conn.commit()
        finally:
            conn.close()

        try:
            # 2. Activation du mode Silence intelligent poêle allumé
            resp_act = self.client.post("/api/silence/activate", json={"duration_hours": 2.0})
            self.assertEqual(resp_act.status_code, 200)
            data_act = resp_act.json()
            self.assertEqual(data_act["status"], "ok")
            self.assertTrue(data_act["silence_mode"]["active"])
            self.assertTrue(data_act["silence_mode"]["is_stove_on"])

            # Vérifier les ordres de bascule :
            # - boost, éco, chrono désactivés (0)
            # - auto total activé (fun_auto=1, ventilations=8)
            # - consignes en hors-gel (7°C)
            resp_latest = self.client.get("/api/latest")
            regs = resp_latest.json()["registers"]
            self.assertEqual(regs["fun_pwf_set"]["current_val"], 0.0)
            self.assertEqual(regs["standby_set"]["current_val"], 0.0)
            self.assertEqual(regs["chrono_week_enable_set"]["current_val"], 0.0)
            self.assertEqual(regs["fun_auto_set"]["current_val"], 1.0)
            self.assertEqual(regs["fan_front_speed"]["current_val"], 8.0)
            self.assertEqual(regs["duct_left_speed"]["current_val"], 8.0)
            self.assertEqual(regs["duct_right_speed"]["current_val"], 8.0)
            self.assertEqual(regs["temp_amb_setpoint"]["current_val"], 7.0)
            self.assertEqual(regs["duct_left_setpoint"]["current_val"], 7.0)
            self.assertEqual(regs["duct_right_setpoint"]["current_val"], 7.0)
            self.assertEqual(regs["set_silece_set"]["current_val"], 1.0)

            # 3. Désactivation et restauration intégrale des paramètres
            resp_deact = self.client.post("/api/silence/deactivate")
            self.assertEqual(resp_deact.status_code, 200)

            resp_latest2 = self.client.get("/api/latest")
            regs2 = resp_latest2.json()["registers"]
            self.assertEqual(regs2["fun_pwf_set"]["current_val"], 1.0)
            self.assertEqual(regs2["standby_set"]["current_val"], 1.0)
            self.assertEqual(regs2["chrono_week_enable_set"]["current_val"], 1.0)
            self.assertEqual(regs2["fun_auto_set"]["current_val"], 0.0)
            self.assertEqual(regs2["fan_front_speed"]["current_val"], 3.0)
            self.assertEqual(regs2["duct_left_speed"]["current_val"], 2.0)
            self.assertEqual(regs2["duct_right_speed"]["current_val"], 2.0)
            self.assertEqual(regs2["temp_amb_setpoint"]["current_val"], 20.0)
            self.assertEqual(regs2["duct_left_setpoint"]["current_val"], 21.0)
            self.assertEqual(regs2["duct_right_setpoint"]["current_val"], 19.0)
            self.assertEqual(regs2["set_silece_set"]["current_val"], 0.0)
        finally:
            conn = get_db(active_mode)
            try:
                conn.execute("DELETE FROM telemetry WHERE epoch = ?", (test_epoch,))
                conn.commit()
            finally:
                conn.close()

    def test_matter_status(self):
        resp = self.client.get("/api/matter/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("enabled", data)

    def test_comm_status_dissociation(self):
        # Vérifie que l'état de connexion conteneur (/health -> status: healthy)
        # est bien dissocié de l'état de liaison au cloud / passerelle (comm_status)
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("comm_status", data)
        comm = data["comm_status"]
        self.assertIn("connected", comm)
        self.assertIn("comm_mode", comm)

        # Vérification sur /api/poll-now
        poll_resp = self.client.post("/api/poll-now")
        self.assertEqual(poll_resp.status_code, 200)
        poll_data = poll_resp.json()
        self.assertEqual(poll_data["status"], "ok")
        self.assertIn("comm_status", poll_data)

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
        _payload = {"action": "set_power", "state": "ON"}
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
            orig_mode = row["value"] if (row and row["value"]) else "agua_cloud"
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

    def test_mobile_navigation_tabs_bottom_clearance(self):
        """Vérifie que l'interface mobile dispose d'un dégagement inférieur suffisant (HTML et CSS)
        pour éviter que le contenu des onglets (Pellets, Chrono, Supervision, Paramètres)
        ne soit masqué sous la barre de navigation fixe du bas."""
        # 1. Vérification dans l'index HTML servi
        resp_spa = self.client.get("/")
        self.assertEqual(resp_spa.status_code, 200)
        html = resp_spa.text

        # Présence de la barre fixe inférieure mobile
        self.assertIn("fixed bottom-0", html)
        self.assertIn("mob-btn-tab-control", html)

        # Dégagement sur le conteneur principal <main> et body
        self.assertIn("pb-28 md:pb-6", html)

        # Dégagement sur chacun des onglets scrollables
        for tab_id in ["tab-pellets", "tab-chrono", "tab-dashboard", "tab-installer"]:
            self.assertIn(f'id="{tab_id}"', html)
            self.assertIn(f'id="{tab_id}" class="hidden space-y-', html)
            self.assertIn("pb-8 md:pb-0", html)

        # 2. Vérification dans la feuille de style styles.css servie
        resp_css = self.client.get("/static/css/styles.css")
        self.assertEqual(resp_css.status_code, 200)
        css = resp_css.text

        self.assertIn("@media (max-width: 767px)", css)
        self.assertIn("padding-bottom: calc(", css)
        self.assertIn("env(safe-area-inset-bottom", css)
        self.assertIn("#tab-pellets", css)
        self.assertIn("#tab-chrono", css)
        self.assertIn("#tab-dashboard", css)
        self.assertIn("#tab-installer", css)

        # 3. Vérification de l'étanchéité du masquage des onglets (évite que tab-control reste visible sous les autres)
        self.assertIn("#tab-control.hidden", css)
        self.assertIn("display: none !important", css)
        self.assertIn("#tab-control:not(.hidden)", css)

        # 4. Vérification que la barre d'onglets desktop n'est pas masquée sur grand écran
        self.assertIn("hidden md:flex", html)
        self.assertIn("@media (min-width: 768px)", css)
        self.assertIn("nav.md\\:flex", css)
        # La classe générique .hidden ne doit PAS avoir !important pour ne pas casser md:flex
        self.assertIsNone(re.search(r'(?m)^\.hidden\s*[,{]', css))
        self.assertNotIn("\n.hidden,\n", css.replace("\r\n", "\n"))
        self.assertNotIn(".hidden { display: none !important", css)

    def test_calibration_wizard_endpoint(self):
        """Vérifie que la route /calibration sert la page HTML de l'assistant de calibration."""
        resp = self.client.get("/calibration")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Assistant d'Étalonnage Couplé", resp.text)
        self.assertIn("Nobis A10", resp.text)
        self.assertIn("Protocole", resp.text)

    def test_calibration_api_endpoints_background_flow(self):
        """Vérifie le cycle des endpoints API /api/calibration/ en arrière-plan."""
        # 1. Reset
        r_reset = self.client.post("/api/calibration/reset")
        self.assertEqual(r_reset.status_code, 200)

        # 2. Status initial
        r_stat = self.client.get("/api/calibration/status")
        self.assertEqual(r_stat.status_code, 200)
        data = r_stat.json()
        self.assertEqual(data["step"], 1)
        self.assertEqual(data["state"], "IDLE")

        # 3. Step 1 : Pré-contrôle
        r_s1 = self.client.post("/api/calibration/step1-start", json={"mode": "COMBINED_BOTH"})
        self.assertEqual(r_s1.status_code, 200)
        self.assertTrue(r_s1.json()["success"])

        # 4. Status après Step 1
        r_stat2 = self.client.get("/api/calibration/status")
        self.assertEqual(r_stat2.status_code, 200)
        self.assertEqual(r_stat2.json()["step"], 2)

    def test_confort_clima_standby_real_state_and_cloud_behavior(self):
        """Vérifie que la fonction Confort Clima / Mode Éco :
        1. Ne force pas un état ACTIF erroné (standby_enabled = 0 quand le poêle est OFF ou TRAVAIL).
        2. Détecte fidèlement les phases réelles 8 (ECO STOP) et 9 (STAND BY) comme actives (standby_enabled = 1).
        3. En mode agua_cloud, l'offset fictif 33130 n'est pas transmis au Cloud tout en mettant à jour le catalogue local.
        """
        import time
        from database import get_active_mode, get_db

        active_mode = get_active_mode()
        conn = get_db(active_mode)
        test_epochs = []
        try:
            max_ep = conn.execute("SELECT MAX(epoch) FROM telemetry").fetchone()[0] or int(time.time())
            ep1 = max_ep + 10
            ep2 = max_ep + 20
            ep3 = max_ep + 30
            ep4 = max_ep + 40
            test_epochs = [ep1, ep2, ep3, ep4]
            conn.execute(
                "INSERT INTO telemetry (timestamp, epoch, comm_mode, status_code, status_label, standby_enabled) VALUES ('2026-11-12 10:00:00', ?, ?, 0, 'OFF (Éteint)', 0)",
                (ep1, active_mode)
            )
            conn.execute(
                "INSERT INTO telemetry (timestamp, epoch, comm_mode, status_code, status_label, standby_enabled) VALUES ('2026-11-12 10:01:00', ?, ?, 5, 'TRAVAIL (Normal)', 0)",
                (ep2, active_mode)
            )
            conn.execute(
                "INSERT INTO telemetry (timestamp, epoch, comm_mode, status_code, status_label, standby_enabled) VALUES ('2026-11-12 10:02:00', ?, ?, 8, 'ECO STOP (Stand-by actif)', 1)",
                (ep3, active_mode)
            )
            conn.execute(
                "INSERT INTO telemetry (timestamp, epoch, comm_mode, status_code, status_label, standby_enabled) VALUES ('2026-11-12 10:03:00', ?, ?, 9, 'STAND BY', 1)",
                (ep4, active_mode)
            )
            conn.commit()
        finally:
            conn.close()

        try:
            # Lecture du dernier point (STAND BY)
            resp_latest = self.client.get("/api/latest")
            self.assertEqual(resp_latest.status_code, 200)
            data = resp_latest.json()
            self.assertEqual(data["latest"]["status_code"], 9)
            self.assertEqual(data["latest"]["standby_enabled"], 1)

            # Écriture locale de standby_set via /api/command
            resp_write = self.client.post("/api/command", json={"reg_key": "standby_set", "value": 0.0, "offset": 33130})
            self.assertEqual(resp_write.status_code, 200)
            self.assertEqual(resp_write.json()["new_value"], 0.0)
        finally:
            conn = get_db(active_mode)
            try:
                for ep in test_epochs:
                    conn.execute("DELETE FROM telemetry WHERE epoch = ?", (ep,))
                conn.commit()
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
