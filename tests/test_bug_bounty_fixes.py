# -*- coding: utf-8 -*-
"""
Tests de régression généralisés couvrant les 15 correctifs Bug Bounty (BB-01 à BB-15).
Exécution : .venv/Scripts/pytest.exe -v tests/test_bug_bounty_fixes.py
"""

import asyncio
import importlib.util
import json
import os
import socket
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from starlette.requests import Request

import clients
import telemetry
from analytics import evaluate_batch_combustion_and_trim
from config import BASE_DIR, TELEMETRY_COLUMNS
from database import get_db, get_sys_db, init_all_databases
from main import MAX_SSE_SUBSCRIBERS, app
from routers.installer import (
    _clean_expired_installer_sessions,
    _failed_auth_attempts,
    _get_client_ip,
    clear_installer_sessions,
)
from src.calibration.calibration_service import (
    CALIBRATION_SETTINGS_KEY,
    build_sample_from_record,
    get_calibration_service,
)
from src.calibration.nobis_calibration import (
    CalibrationMode,
    CalibrationResult,
    CalibrationState,
    PhaseBreakdownStats,
)


@contextmanager
def temporary_settings(overrides: dict):
    """Sauvegarde et restaure strictement les clés modifiées dans settings."""
    conn = get_sys_db()
    originals = {}
    try:
        for k, v in overrides.items():
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (k,)).fetchone()
            originals[k] = row["value"] if row is not None else None
            if v is None:
                conn.execute("DELETE FROM settings WHERE key = ?", (k,))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (k, str(v)),
                )
        conn.commit()
    finally:
        conn.close()

    try:
        yield
    finally:
        conn = get_sys_db()
        try:
            for k, orig in originals.items():
                if orig is None:
                    conn.execute("DELETE FROM settings WHERE key = ?", (k,))
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                        (k, orig),
                    )
            conn.commit()
        finally:
            conn.close()


def _make_calibration_result(off70: int = 2, off71: int = -1) -> CalibrationResult:
    return CalibrationResult(
        mode=CalibrationMode.COMBINED_BOTH,
        weighed_bag_kg=15.0,
        intrinsic_k_factor=1.0,
        before_flows_kgh={4: 1.51},
        measured_raw_flows_kgh={4: 1.51},
        coupled_effective_flows_kgh={4: 1.51},
        recommended_offset_70=off70,
        recommended_offset_71=off71,
        current_offset_70=0,
        current_offset_71=0,
        time_per_power_hours={4: 2.0},
        phase_stats=PhaseBreakdownStats(),
        mean_smoke_temp_delta_c=0.0,
        mean_pressure_error_pct=0.0,
        diagnostic_summary="Test diagnostic",
        closed_by_al03=True,
    )


class TestBugBountyFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_all_databases()
        cls.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        clear_installer_sessions()
        _failed_auth_attempts.clear()

    # ------------------------------------------------------------------
    # BB-01 : Autorisation installateur sur routes privilégiées
    # ------------------------------------------------------------------
    def test_bb01_privileged_endpoints_require_installer_auth(self):
        clear_installer_sessions()
        _failed_auth_attempts.clear()
        with temporary_settings({"comm_mode": "simulation", "agua_password": "bb01-test-password"}):
            self.assertEqual(self.client.post("/api/snapshots", json={"label": "unauth"}).status_code, 403)
            self.assertEqual(self.client.post("/api/snapshots/1/restore").status_code, 403)
            self.assertEqual(self.client.delete("/api/snapshots/1").status_code, 403)
            self.assertEqual(
                self.client.post("/api/settings", json={"webhook_url": "https://example.com/hook"}).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    "/api/command",
                    json={"reg_key": "RAW_OFFSET_33100", "offset": 33100, "value": 1.0},
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    "/api/command-batch",
                    json={
                        "commands": [
                            {"reg_key": "power_level", "offset": 33120, "value": 3.0},
                            {"reg_key": "unknown_raw_reg", "offset": 33100, "value": 1.0},
                        ]
                    },
                ).status_code,
                403,
            )
            self.assertEqual(self.client.post("/api/matter/factory-reset").status_code, 403)

            non_priv = self.client.post(
                "/api/command",
                json={"reg_key": "power_level", "offset": 33120, "value": 4.0},
            )
            self.assertEqual(non_priv.status_code, 200)

            auth_resp = self.client.post("/api/installer/verify", json={"password": "bb01-test-password"})
            self.assertEqual(auth_resp.status_code, 200)
            token = auth_resp.json()["token"]
            headers = {"X-Installer-Token": token}

            snap_resp = self.client.post("/api/snapshots", json={"label": "bb01-temp"}, headers=headers)
            self.assertEqual(snap_resp.status_code, 200)
            snap_id = snap_resp.json()["id"]
            del_resp = self.client.delete(f"/api/snapshots/{snap_id}", headers=headers)
            self.assertEqual(del_resp.status_code, 200)

    # ------------------------------------------------------------------
    # BB-02 : Calibration await writes & step2 écrit 170.0 (pas 85.0)
    # ------------------------------------------------------------------
    def test_bb02_calibration_step1_and_step2_await_and_send_170_not_85(self):
        srv = get_calibration_service()
        with temporary_settings({"comm_mode": "simulation", CALIBRATION_SETTINGS_KEY: None}):
            srv.reset_session()
            sconn = get_db("simulation")
            row_id = None
            try:
                cur = sconn.execute(
                    "INSERT INTO telemetry (epoch, timestamp, status_code, alarm_code, power_level, power_actual, fun_pwf_set) "
                    "VALUES (?, ?, 10, 3, 4, 4, 1)",
                    (int(time.time()) + 60, "2026-11-15T12:00:00"),
                )
                row_id = cur.lastrowid
                sconn.commit()
            finally:
                sconn.close()

            try:
                with patch("routers.calibration.execute_register_write", new_callable=AsyncMock) as mock_write:
                    mock_write.return_value = {"status": "ok"}

                    r1 = self.client.post("/api/calibration/step1-start", json={"mode": "COMBINED_BOTH"})
                    self.assertEqual(r1.status_code, 200)
                    mock_write.assert_awaited_with("fun_pwf_set", 0.0, 33154, skip_poll=True)

                    mock_write.reset_mock()
                    srv.engine.state = CalibrationState.WAITING_INITIAL_AL03
                    r2 = self.client.post("/api/calibration/step2-confirm-al03")
                    self.assertEqual(r2.status_code, 200)
                    mock_write.assert_awaited_once_with("status_code", 170.0, 232, skip_poll=True)
                    for call in mock_write.await_args_list:
                        self.assertNotEqual(call.args[1], 85.0)
            finally:
                srv.reset_session()
                if row_id is not None:
                    sconn = get_db("simulation")
                    try:
                        sconn.execute("DELETE FROM telemetry WHERE id = ?", (row_id,))
                        sconn.commit()
                    finally:
                        sconn.close()

    # ------------------------------------------------------------------
    # BB-03 : Anti-SSRF webhook & passerelle locale
    # ------------------------------------------------------------------
    def test_bb03_ssrf_webhook_and_local_gateway_validation(self):
        blocked_webhooks = [
            "http://127.0.0.1:8080/hook",
            "http://localhost/hook",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/hook",
            "http://[::ffff:127.0.0.1]/hook",
            "http://10.0.0.5/hook",
            "http://192.168.1.50/hook",
            "http://172.16.0.1/hook",
            "ftp://example.com/hook",
            "file:///etc/passwd",
        ]
        for url in blocked_webhooks:
            self.assertFalse(clients.is_safe_webhook_url(url), msg=f"Should block webhook URL: {url}")

        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]):
            self.assertFalse(clients.is_safe_webhook_url("https://rebind.example.com/hook"))

        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]):
            self.assertTrue(clients.is_safe_webhook_url("https://hooks.example.com/webhook"))

        self.assertTrue(clients.is_safe_gateway_url("http://192.168.1.50"))
        self.assertTrue(clients.is_safe_gateway_url("http://10.0.0.12:8088"))
        for bad_gw in [
            "http://127.0.0.1:8080",
            "http://localhost:5549",
            "http://169.254.169.254",
            "http://[::1]/",
            "http://192.168.1.50:1220",
            "http://192.168.1.50:1221",
            "http://192.168.1.50:8080",
            "http://192.168.1.50:5549",
        ]:
            self.assertFalse(clients.is_safe_gateway_url(bad_gw), msg=f"Should block gateway URL: {bad_gw}")

        with temporary_settings({"comm_mode": "simulation", "agua_password": "bb03-pass"}):
            clear_installer_sessions()
            tok = self.client.post("/api/installer/verify", json={"password": "bb03-pass"}).json()["token"]
            headers = {"X-Installer-Token": tok}
            self.assertEqual(
                self.client.post(
                    "/api/settings",
                    json={"webhook_url": "http://169.254.169.254/latest/meta-data"},
                    headers=headers,
                ).status_code,
                400,
            )
            self.assertEqual(
                self.client.post(
                    "/api/settings",
                    json={"local_gateway_url": "http://127.0.0.1:8080"},
                    headers=headers,
                ).status_code,
                400,
            )

    # ------------------------------------------------------------------
    # BB-04 : Reserve optique (popup_pellet_empty) != AL-03 ni famine 45 mA
    # ------------------------------------------------------------------
    def test_bb04_optical_reserve_not_mapped_to_al03_or_starvation(self):
        sample_reserve = build_sample_from_record(
            {
                "epoch": 1700000000,
                "status_code": 5,
                "alarm_code": 0,
                "popup_pellet_empty": 1,
                "power_level": 4,
                "power_actual": 4,
                "auger_rpm_actual": 3.0,
                "auger_rpm_set": 3.0,
                "depression_pa": 3.0,
            }
        )
        self.assertIsNone(sample_reserve.alarm_code)
        self.assertEqual(sample_reserve.auger_current_ma, 150.0)
        self.assertNotEqual(sample_reserve.auger_current_ma, 45.0)

        sample_al03 = build_sample_from_record(
            {
                "epoch": 1700000060,
                "status_code": 10,
                "alarm_code": 3,
                "popup_pellet_empty": 0,
                "power_level": 4,
                "power_actual": 4,
            }
        )
        self.assertEqual(sample_al03.alarm_code, "AL-03")
        self.assertEqual(sample_al03.auger_current_ma, 45.0)

    # ------------------------------------------------------------------
    # BB-05 : Double échelle consigne pression coaxiale Pa
    # ------------------------------------------------------------------
    def test_bb05_dual_scale_target_coax_pa(self):
        low_p4 = build_sample_from_record({"epoch": 1, "status_code": 5, "power_level": 4, "depression_pa": 3.0})
        self.assertEqual(low_p4.flue_pressure_set_pa, 3.0)

        low_p7 = build_sample_from_record({"epoch": 2, "status_code": 5, "power_level": 7, "depression_pa": 2.5})
        self.assertEqual(low_p7.flue_pressure_set_pa, 3.75)

        bench = build_sample_from_record({"epoch": 3, "status_code": 5, "power_level": 4, "depression_pa": 18.2})
        self.assertEqual(bench.flue_pressure_set_pa, 18.0)

    # ------------------------------------------------------------------
    # BB-06 : test-connection n'écrase pas le singleton ni la BDD
    # ------------------------------------------------------------------
    def test_bb06_test_connection_does_not_overwrite_singleton_or_settings(self):
        orig_attrs = (
            clients.agua_client.token_jwt,
            clients.agua_client.refresh_token,
            clients.agua_client.id_device,
            clients.agua_client.id_product,
            clients.agua_client.working_write_endpoint,
        )
        with temporary_settings(
            {
                "comm_mode": "simulation",
                "agua_id_device": "orig-dev-01",
                "agua_id_product": "orig-prod-01",
            }
        ):
            clients.agua_client.token_jwt = "orig-jwt"
            clients.agua_client.refresh_token = "orig-refresh"
            clients.agua_client.id_device = "orig-dev-01"
            clients.agua_client.id_product = "orig-prod-01"
            clients.agua_client.working_write_endpoint = "/orig"

            seen_kwargs = {}

            async def fake_login(email, password, app_uuid, persist_discovery=True):
                seen_kwargs["persist_discovery"] = persist_discovery
                clients.agua_client.token_jwt = "temp-jwt"
                clients.agua_client.id_device = "temp-dev-99"
                clients.agua_client.id_product = "temp-prod-99"
                return True, "OK"

            try:
                with patch.object(clients.agua_client, "login_and_discover", side_effect=fake_login):
                    resp = self.client.post(
                        "/api/test-connection",
                        json={
                            "comm_mode": "agua_cloud",
                            "agua_email": "test@example.com",
                            "agua_password": "secret",
                            "agua_uuid": "uuid-test",
                        },
                    )
                self.assertEqual(resp.status_code, 200)
                self.assertFalse(seen_kwargs.get("persist_discovery", True))
                self.assertEqual(clients.agua_client.token_jwt, "orig-jwt")
                self.assertEqual(clients.agua_client.id_device, "orig-dev-01")
                self.assertEqual(clients.agua_client.id_product, "orig-prod-01")
                self.assertEqual(clients.agua_client.working_write_endpoint, "/orig")

                conn = get_sys_db()
                try:
                    dev_row = conn.execute("SELECT value FROM settings WHERE key='agua_id_device'").fetchone()
                    self.assertEqual(dev_row["value"], "orig-dev-01")
                finally:
                    conn.close()

                # Vérifie persist_discovery=False vs persist_discovery=True sur un client isolé
                local_client = clients.AguaCloudClient()
                local_client._registered_uuids.add("uuid")
                mock_http = AsyncMock()
                mock_resp_login = MagicMock(status_code=200)
                mock_resp_login.json.return_value = {"token": "jwt-1", "refresh_token": "ref-1"}
                mock_resp_devs = MagicMock(status_code=200)
                mock_resp_devs.json.return_value = {
                    "device": [{"id_device": "disc-dev-2", "id_product": "disc-prod-2", "name": "Nobis A10"}]
                }
                mock_resp_map = MagicMock(status_code=404)

                class _AsyncCtx:
                    async def __aenter__(self):
                        return mock_http

                    async def __aexit__(self, *args):
                        return False

                mock_http.post.side_effect = [mock_resp_login, mock_resp_devs, mock_resp_map]
                with patch("clients.borrow_http_client", return_value=_AsyncCtx()):
                    res_no_persist = asyncio.run(
                        local_client.login_and_discover("user@example.com", "p", "uuid", persist_discovery=False)
                    )
                self.assertTrue(res_no_persist["token_ok"])
                conn = get_sys_db()
                try:
                    dev_row = conn.execute("SELECT value FROM settings WHERE key='agua_id_device'").fetchone()
                    self.assertEqual(dev_row["value"], "orig-dev-01")
                finally:
                    conn.close()

                mock_http.post.side_effect = [mock_resp_login, mock_resp_devs, mock_resp_map]
                with patch("clients.borrow_http_client", return_value=_AsyncCtx()):
                    res_persist = asyncio.run(
                        local_client.login_and_discover("user@example.com", "p", "uuid", persist_discovery=True)
                    )
                self.assertTrue(res_persist["token_ok"])
                conn = get_sys_db()
                try:
                    dev_row = conn.execute("SELECT value FROM settings WHERE key='agua_id_device'").fetchone()
                    prod_row = conn.execute("SELECT value FROM settings WHERE key='agua_id_product'").fetchone()
                    self.assertEqual(dev_row["value"], "disc-dev-2")
                    self.assertEqual(prod_row["value"], "disc-prod-2")
                finally:
                    conn.close()
            finally:
                (
                    clients.agua_client.token_jwt,
                    clients.agua_client.refresh_token,
                    clients.agua_client.id_device,
                    clients.agua_client.id_product,
                    clients.agua_client.working_write_endpoint,
                ) = orig_attrs

    # ------------------------------------------------------------------
    # BB-07 : step5-apply readback réel + drapeau agua_cloud
    # ------------------------------------------------------------------
    def test_bb07_calibration_step5_apply_real_readback_and_agua_cloud_flag(self):
        srv = get_calibration_service()
        with temporary_settings({"comm_mode": "simulation", CALIBRATION_SETTINGS_KEY: None}):
            srv.reset_session()
            try:
                srv.engine.state = CalibrationState.COMPLETED_LOCKED
                srv.engine.result = _make_calibration_result(off70=2, off71=-1)

                with (
                    patch("routers.calibration.execute_registers_batch_write", new_callable=AsyncMock) as mock_batch,
                    patch(
                        "routers.calibration.get_registers_dict",
                        return_value={
                            "trim_pellet_pct": {"current_val": 2.0, "offset": 70},
                            "trim_exhaust_pct": {"current_val": -1.0, "offset": 71},
                        },
                    ),
                ):
                    mock_batch.return_value = {"status": "ok"}
                    r_ok = self.client.post("/api/calibration/step5-apply")
                    self.assertEqual(r_ok.status_code, 200)
                    body_ok = r_ok.json()
                    self.assertTrue(body_ok["success"])
                    self.assertNotIn("remote_manual_required", body_ok)
                    mock_batch.assert_awaited_once()

                with (
                    patch("routers.calibration.execute_registers_batch_write", new_callable=AsyncMock) as mock_batch,
                    patch(
                        "routers.calibration.get_registers_dict",
                        return_value={
                            "trim_pellet_pct": {"current_val": 0.0, "offset": 70},
                            "trim_exhaust_pct": {"current_val": 0.0, "offset": 71},
                        },
                    ),
                ):
                    mock_batch.return_value = {"status": "ok"}
                    r_mismatch = self.client.post("/api/calibration/step5-apply")
                    self.assertEqual(r_mismatch.status_code, 200)
                    body_mismatch = r_mismatch.json()
                    self.assertFalse(body_mismatch["success"])
                    self.assertIn("relecture", body_mismatch["message"])

                with temporary_settings({"comm_mode": "agua_cloud"}):
                    with (
                        patch("routers.calibration.execute_registers_batch_write", new_callable=AsyncMock) as mock_batch,
                        patch(
                            "routers.calibration.get_registers_dict",
                            return_value={
                                "trim_pellet_pct": {"current_val": 2.0, "offset": 70},
                                "trim_exhaust_pct": {"current_val": -1.0, "offset": 71},
                            },
                        ),
                    ):
                        mock_batch.return_value = {"status": "ok"}
                        r_cloud = self.client.post("/api/calibration/step5-apply")
                        self.assertEqual(r_cloud.status_code, 200)
                        body_cloud = r_cloud.json()
                        self.assertTrue(body_cloud["success"])
                        self.assertTrue(body_cloud["remote_manual_required"])
            finally:
                srv.reset_session()

    # ------------------------------------------------------------------
    # BB-08 : Anti-CSRF, CSP et limite SSE MAX_SSE_SUBSCRIBERS
    # ------------------------------------------------------------------
    def test_bb08_anti_csrf_csp_and_sse_limit(self):
        with temporary_settings({"comm_mode": "simulation"}):
            r_form = self.client.post(
                "/api/command",
                data="reg_key=power_level&offset=33120&value=3",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            self.assertIn(r_form.status_code, (403, 415))

            r_evil = self.client.post(
                "/api/command",
                json={"reg_key": "power_level", "offset": 33120, "value": 4.0},
                headers={"Origin": "https://evil.example"},
            )
            self.assertEqual(r_evil.status_code, 403)

            r_null = self.client.post(
                "/api/command",
                json={"reg_key": "power_level", "offset": 33120, "value": 4.0},
                headers={"Origin": "null"},
            )
            self.assertEqual(r_null.status_code, 403)

            sim_conn = get_db("simulation")
            orig_status = sim_conn.execute(
                "SELECT current_val FROM registers_catalog WHERE reg_key = 'status_code'"
            ).fetchone()["current_val"]
            sim_conn.close()
            try:
                r_hook = self.client.post(
                    "/api/matter/webhook",
                    json={"action": "set_power", "state": "ON"},
                    headers={"Origin": "https://matter-bridge.example"},
                )
                self.assertEqual(r_hook.status_code, 200)
            finally:
                sim_conn = get_db("simulation")
                try:
                    sim_conn.execute(
                        "UPDATE registers_catalog SET current_val = ? WHERE reg_key = 'status_code'",
                        (orig_status,),
                    )
                    sim_conn.commit()
                finally:
                    sim_conn.close()

        r_health = self.client.get("/health")
        self.assertEqual(r_health.status_code, 200)
        self.assertIn("content-security-policy", {k.lower(): v for k, v in r_health.headers.items()})

        dummy_queues = [asyncio.Queue(maxsize=1) for _ in range(MAX_SSE_SUBSCRIBERS)]
        telemetry.sse_subscribers.update(dummy_queues)
        try:
            r_sse = self.client.get("/api/events")
            self.assertEqual(r_sse.status_code, 503)
        finally:
            for q in dummy_queues:
                telemetry.sse_subscribers.discard(q)

    # ------------------------------------------------------------------
    # BB-09 : Extraction IP derrière proxy et nettoyage _failed_auth_attempts
    # ------------------------------------------------------------------
    def test_bb09_forwarded_ip_and_failed_attempts_cleanup(self):
        proxied_scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/installer/verify",
            "headers": [(b"x-forwarded-for", b"203.0.113.42, 10.0.0.1")],
            "client": ("127.0.0.1", 54321),
        }
        self.assertEqual(_get_client_ip(Request(proxied_scope)), "203.0.113.42")

        spoofed_scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/installer/verify",
            "headers": [(b"x-forwarded-for", b"1.2.3.4")],
            "client": ("198.51.100.5", 54321),
        }
        self.assertEqual(_get_client_ip(Request(spoofed_scope)), "198.51.100.5")

        _failed_auth_attempts["198.51.100.99"] = (9, time.time() - 3600)
        _failed_auth_attempts["198.51.100.100"] = (2, time.time())
        try:
            _clean_expired_installer_sessions()
            self.assertNotIn("198.51.100.99", _failed_auth_attempts)
            self.assertIn("198.51.100.100", _failed_auth_attempts)
        finally:
            _failed_auth_attempts.pop("198.51.100.99", None)
            _failed_auth_attempts.pop("198.51.100.100", None)

    # ------------------------------------------------------------------
    # BB-10 : Décodage INT8 signé offsets 70/71 et rafraîchissement r_val
    # ------------------------------------------------------------------
    def test_bb10_signed_int8_trims_decoding_and_persistence(self):
        client = clients.AguaCloudClient()
        client.token_jwt = "fake-jwt"
        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "Items": [70, 71, 20, 21],
            "Values": [253, 251, 180, 215],
        }
        mock_http.post.return_value = mock_resp

        class _AsyncCtx:
            async def __aenter__(self):
                return mock_http

            async def __aexit__(self, *args):
                return False

        with patch("clients.borrow_http_client", return_value=_AsyncCtx()):
            buf = asyncio.run(client.read_buffer({"agua_id_device": "d1", "agua_id_product": "p1"}))
        self.assertEqual(buf[70], -3.0)
        self.assertEqual(buf[71], -5.0)

        sconn = get_db("local_gateway")
        max_row = sconn.execute("SELECT COALESCE(MAX(id), 0) FROM telemetry").fetchone()[0]
        orig_regs = {}
        try:
            for k in ("trim_pellet_pct", "trim_exhaust_pct"):
                r = sconn.execute("SELECT current_val FROM registers_catalog WHERE reg_key = ?", (k,)).fetchone()
                orig_regs[k] = r["current_val"] if r else 0.0
        finally:
            sconn.close()

        with temporary_settings({"comm_mode": "local_gateway", "local_gateway_url": "http://192.168.1.50"}):
            mock_buf = {8: 5.0, 1: 21.0, 20: 180.0, 21: 215.0, 70: 253.0, 71: 251.0}
            try:
                with patch(
                    "telemetry.local_gateway_client.read_buffer",
                    new_callable=AsyncMock,
                    return_value=mock_buf,
                ):
                    rec = asyncio.run(telemetry.collect_telemetry_cycle(force_mode="local_gateway"))
                self.assertEqual(rec["trim_pellet_pct"], -3)
                self.assertEqual(rec["trim_exhaust_pct"], -5)
            finally:
                sconn = get_db("local_gateway")
                try:
                    sconn.execute("DELETE FROM telemetry WHERE id > ?", (max_row,))
                    for k, val in orig_regs.items():
                        sconn.execute("UPDATE registers_catalog SET current_val = ? WHERE reg_key = ?", (val, k))
                    sconn.commit()
                finally:
                    sconn.close()

    # ------------------------------------------------------------------
    # BB-11 : Mode Silence préserve initial_state et réinitialise set_silece_set
    # ------------------------------------------------------------------
    def test_bb11_silence_reentry_preserves_initial_state_and_resets_silece(self):
        sim_conn = get_db("simulation")
        orig_regs = {}
        try:
            for k in ("power_level", "fan_front_speed", "set_silece_set"):
                r = sim_conn.execute("SELECT current_val FROM registers_catalog WHERE reg_key = ?", (k,)).fetchone()
                orig_regs[k] = r["current_val"] if r else 0.0
        finally:
            sim_conn.close()

        with temporary_settings(
            {
                "comm_mode": "simulation",
                "silence_mode_active": None,
                "silence_mode_initial_state": None,
                "silence_mode_overrides": None,
            }
        ):
            r_init = self.client.post(
                "/api/command-batch",
                json={
                    "commands": [
                        {"reg_key": "power_level", "offset": 33120, "value": 6.0},
                        {"reg_key": "fan_front_speed", "offset": 33132, "value": 4.0},
                        {"reg_key": "set_silece_set", "offset": 33250, "value": 1.0},
                    ]
                },
            )
            self.assertEqual(r_init.status_code, 200)

            try:
                r_act1 = self.client.post("/api/silence/activate", json={"power_level": 2, "fan_front_speed": 1})
                self.assertEqual(r_act1.status_code, 200)
                first_initial = r_act1.json()["silence_mode"]["initial_state"]
                self.assertEqual(first_initial["power_level"], 6.0)
                self.assertEqual(first_initial["fan_front_speed"], 4.0)

                # Deuxième activation à chaud : conserve initial_state d'origine
                r_act2 = self.client.post("/api/silence/activate", json={"power_level": 1, "fan_front_speed": 0})
                self.assertEqual(r_act2.status_code, 200)
                second_initial = r_act2.json()["silence_mode"]["initial_state"]
                self.assertEqual(second_initial["power_level"], 6.0)
                self.assertEqual(second_initial["fan_front_speed"], 4.0)

                r_deact = self.client.post("/api/silence/deactivate")
                self.assertEqual(r_deact.status_code, 200)

                latest_resp = self.client.get("/api/latest")
                self.assertEqual(latest_resp.status_code, 200)
                regs_map = latest_resp.json()["registers"]
                self.assertEqual(float(regs_map["power_level"]["current_val"]), 6.0)
                self.assertEqual(float(regs_map["set_silece_set"]["current_val"]), 0.0)
            finally:
                self.client.post("/api/silence/deactivate")
                sim_conn = get_db("simulation")
                try:
                    for k, val in orig_regs.items():
                        sim_conn.execute("UPDATE registers_catalog SET current_val = ? WHERE reg_key = ?", (val, k))
                    sim_conn.commit()
                finally:
                    sim_conn.close()

    # ------------------------------------------------------------------
    # BB-12 : Tolérance gap dynamique selon poll_interval_s et probe K delta
    # ------------------------------------------------------------------
    def test_bb12_analytics_dynamic_poll_gap_and_probe_k_delta(self):
        cols_sql = ", ".join(f"{k} {v}" for k, v in TELEMETRY_COLUMNS.items())
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(f"CREATE TABLE telemetry ({cols_sql})")

        with temporary_settings(
            {
                "poll_interval_s": "180",
                "pellet_batch_state": json.dumps({"raz_epoch": 0, "baseline_rpm_pa": 600.0, "pellet_brand": "Test Lot"}),
            }
        ):
            base_epoch = 1793900000
            last_rec = {}
            for cycle in range(4):
                for idx in range(26):
                    ep = base_epoch + cycle * 10000 + idx * 180
                    ts_str = datetime.fromtimestamp(ep).isoformat()
                    conn.execute(
                        """
                        INSERT INTO telemetry (
                            epoch, timestamp, comm_mode, status_code, alarm_code, popup_flags,
                            power_level, power_actual, temp_probe_k, temp_fumes,
                            depression_pa, exhaust_rpm, vent_front_actual,
                            duct_right_actual, duct_left_actual,
                            trim_pellet_pct, trim_exhaust_pct, pellet_rate_kgh
                        ) VALUES (?, ?, 'simulation', 5, 0, 0, 4, 4, 620.0, 150.0, 3.2, 1750, 3, 0, 0, 0, 0, 1.5)
                        """,
                        (ep, ts_str),
                    )
                    last_rec = {
                        "epoch": ep,
                        "comm_mode": "simulation",
                        "status_code": 5,
                        "alarm_code": 0,
                        "popup_flags": 0,
                        "power_level": 4,
                        "power_actual": 4,
                        "temp_probe_k": 620.0,
                        "temp_fumes": 150.0,
                        "depression_pa": 3.2,
                        "exhaust_rpm": 1750,
                        "vent_front_actual": 3,
                        "duct_right_actual": 0,
                        "duct_left_actual": 0,
                        "trim_pellet_pct": 0,
                        "trim_exhaust_pct": 0,
                        "pellet_rate_kgh": 1.5,
                    }
            conn.commit()

            pellet_q, exhaust_q = evaluate_batch_combustion_and_trim(conn, last_rec)
            conn.close()

            self.assertEqual(pellet_q["status"], "active")
            self.assertEqual(exhaust_q["status"], "active")
            self.assertEqual(pellet_q["target_flame_temp"], 590.0)
            self.assertEqual(pellet_q["flame_delta"], 30.0)
            self.assertEqual(pellet_q["flame_delta_avg"], 30.0)

    # ------------------------------------------------------------------
    # BB-13 : Remplissage fractionnaire trémie sans écraser epoch + horloge Paris
    # ------------------------------------------------------------------
    def test_bb13_hopper_refill_fractional_bags_and_paris_clock(self):
        with temporary_settings({"comm_mode": "simulation"}):
            sys_conn = get_sys_db()
            orig_stock_row = sys_conn.execute("SELECT value FROM settings WHERE key = 'pellet_bags_stock'").fetchone()
            orig_stock_val = orig_stock_row["value"] if orig_stock_row else None
            sys_conn.close()

            sconn = get_db("simulation")
            row = sconn.execute("SELECT id, epoch, pellet_hopper_pct FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
            self.assertIsNotNone(row)
            orig_id, orig_epoch, orig_pct = row["id"], row["epoch"], row["pellet_hopper_pct"]
            try:
                sconn.execute("UPDATE telemetry SET pellet_hopper_pct = 20.0 WHERE id = ?", (orig_id,))
                sconn.commit()
                sconn.close()

                r_refill = self.client.post("/api/refill-hopper", json={"bags_added": 0.5})
                self.assertEqual(r_refill.status_code, 200)
                self.assertAlmostEqual(r_refill.json()["pellet_hopper_pct"], 41.4, places=1)

                sconn2 = get_db("simulation")
                try:
                    updated = sconn2.execute(
                        "SELECT epoch, pellet_hopper_pct FROM telemetry WHERE id = ?",
                        (orig_id,),
                    ).fetchone()
                    self.assertEqual(updated["epoch"], orig_epoch)
                    self.assertAlmostEqual(updated["pellet_hopper_pct"], 41.4, places=1)
                finally:
                    sconn2.close()
            finally:
                sconn_restore = get_db("simulation")
                try:
                    sconn_restore.execute(
                        "UPDATE telemetry SET pellet_hopper_pct = ?, epoch = ? WHERE id = ?",
                        (orig_pct, orig_epoch, orig_id),
                    )
                    sconn_restore.commit()
                finally:
                    sconn_restore.close()
                sys_conn2 = get_sys_db()
                try:
                    if orig_stock_val is None:
                        sys_conn2.execute("DELETE FROM settings WHERE key = 'pellet_bags_stock'")
                    else:
                        sys_conn2.execute(
                            "INSERT OR REPLACE INTO settings (key, value) VALUES ('pellet_bags_stock', ?)",
                            (orig_stock_val,),
                        )
                    sys_conn2.commit()
                finally:
                    sys_conn2.close()

            fixed_paris = datetime(2026, 7, 15, 14, 25, 30, tzinfo=ZoneInfo("Europe/Paris"))
            with patch("routers.stove.execute_registers_batch_write", new_callable=AsyncMock) as mock_batch:
                mock_batch.return_value = {"status": "ok"}
                with patch("routers.stove.datetime") as mock_dt:
                    mock_dt.now.return_value = fixed_paris
                    r_clk = self.client.post("/api/sync-clock")
                self.assertEqual(r_clk.status_code, 200)
                mock_dt.now.assert_called_with(ZoneInfo("Europe/Paris"))
                cmds = mock_batch.await_args.args[0]
                by_off = {c["offset"]: c["value"] for c in cmds}
                self.assertEqual(by_off[34048], 14)
                self.assertEqual(by_off[34049], 25)
                self.assertEqual(by_off[34050], 15)
                self.assertEqual(by_off[34051], 7)
                self.assertEqual(by_off[34052], 26)

    # ------------------------------------------------------------------
    # BB-14 : Offsets canalisations 31/32 (droite/gauche) & fallback T° amb [1]
    # ------------------------------------------------------------------
    def test_bb14_duct_offsets_31_32_and_ambient_fallback(self):
        sim_conn = get_db("simulation")
        sim_max = sim_conn.execute("SELECT COALESCE(MAX(id), 0) FROM telemetry").fetchone()[0]
        sim_orig = {}
        try:
            for k in ("status_code", "duct_right_speed", "duct_left_speed"):
                r = sim_conn.execute("SELECT current_val FROM registers_catalog WHERE reg_key = ?", (k,)).fetchone()
                sim_orig[k] = r["current_val"] if r else 0.0
            sim_conn.execute("UPDATE registers_catalog SET current_val = 5.0 WHERE reg_key = 'status_code'")
            sim_conn.execute("UPDATE registers_catalog SET current_val = 3.0 WHERE reg_key = 'duct_right_speed'")
            sim_conn.execute("UPDATE registers_catalog SET current_val = 1.0 WHERE reg_key = 'duct_left_speed'")
            sim_conn.commit()
        finally:
            sim_conn.close()

        try:
            sim_rec = asyncio.run(telemetry.collect_telemetry_cycle(force_mode="simulation"))
            raw_sim = json.loads(sim_rec["raw_buffer_json"])
            self.assertEqual(raw_sim["31"], sim_rec["duct_right_actual"])
            self.assertEqual(raw_sim["32"], sim_rec["duct_left_actual"])
            self.assertEqual(sim_rec["duct_right_actual"], 3)
            self.assertEqual(sim_rec["duct_left_actual"], 1)
        finally:
            sim_conn = get_db("simulation")
            try:
                sim_conn.execute("DELETE FROM telemetry WHERE id > ?", (sim_max,))
                for k, val in sim_orig.items():
                    sim_conn.execute("UPDATE registers_catalog SET current_val = ? WHERE reg_key = ?", (val, k))
                sim_conn.commit()
            finally:
                sim_conn.close()

        gw_conn = get_db("local_gateway")
        gw_max = gw_conn.execute("SELECT COALESCE(MAX(id), 0) FROM telemetry").fetchone()[0]
        gw_conn.close()

        with temporary_settings({"comm_mode": "local_gateway", "local_gateway_url": "http://192.168.1.50"}):
            mock_buf = {8: 5.0, 1: 21.6, 20: 175.0, 21: 215.0, 31: 4.0, 32: 2.0}
            try:
                with patch(
                    "telemetry.local_gateway_client.read_buffer",
                    new_callable=AsyncMock,
                    return_value=mock_buf,
                ):
                    gw_rec = asyncio.run(telemetry.collect_telemetry_cycle(force_mode="local_gateway"))
                self.assertEqual(gw_rec["temp_ambient"], 21.6)
                self.assertEqual(gw_rec["duct_right_actual"], 4)
                self.assertEqual(gw_rec["duct_left_actual"], 2)
            finally:
                gw_conn = get_db("local_gateway")
                try:
                    gw_conn.execute("DELETE FROM telemetry WHERE id > ?", (gw_max,))
                    gw_conn.commit()
                finally:
                    gw_conn.close()

    # ------------------------------------------------------------------
    # BB-15 : Borne batch 64 + hardening Docker/Nginx/Service Worker
    # ------------------------------------------------------------------
    def test_bb15_batch_max_length_and_deployment_hardening(self):
        with temporary_settings({"comm_mode": "simulation"}):
            cmds_65 = [{"reg_key": "power_level", "offset": 33120, "value": 4.0} for _ in range(65)]
            r_65 = self.client.post("/api/command-batch", json={"commands": cmds_65})
            self.assertEqual(r_65.status_code, 422)

        with open(os.path.join(BASE_DIR, "Dockerfile"), "r", encoding="utf-8") as f:
            dockerfile = f.read()
        self.assertIn("--workers", dockerfile)
        self.assertIn("--proxy-headers", dockerfile)
        self.assertIn("--forwarded-allow-ips", dockerfile)
        self.assertIn('VOLUME ["/app/data"]', dockerfile)
        self.assertIn("FROM node:22-trixie-slim AS node-builder", dockerfile)
        self.assertIn("FROM python:3.12-slim-trixie", dockerfile)
        self.assertIn('org.opencontainers.image.licenses="MIT"', dockerfile)
        self.assertIn('org.opencontainers.image.source="https://github.com/malexxx/Nobis.Supervisor"', dockerfile)
        self.assertIn("tini", dockerfile)
        self.assertIn("gosu", dockerfile)
        self.assertIn("urllib.request", dockerfile)
        self.assertNotIn("curl", dockerfile)
        self.assertIn('ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]', dockerfile)

        entrypoint_path = os.path.join(BASE_DIR, "docker-entrypoint.sh")
        self.assertTrue(os.path.isfile(entrypoint_path))
        with open(entrypoint_path, "r", encoding="utf-8") as f:
            entrypoint = f.read()
        self.assertIn('exec gosu nobis:nobis "$@"', entrypoint)

        with open(os.path.join(BASE_DIR, ".github", "workflows", "docker-publish.yml"), "r", encoding="utf-8") as f:
            publish_yml = f.read()
        self.assertIn("sbom: true", publish_yml)
        self.assertIn("provenance: true", publish_yml)

        self.assertFalse(os.path.exists(os.path.join(BASE_DIR, "Dockerfile.txt")))

        with open(os.path.join(BASE_DIR, ".dockerignore"), "r", encoding="utf-8") as f:
            dockerignore = f.read()
        self.assertIn("data/", dockerignore)
        self.assertIn("tests/", dockerignore)
        self.assertIn(".venv", dockerignore)

        with open(os.path.join(BASE_DIR, "docker-compose.yml"), "r", encoding="utf-8") as f:
            compose = f.read()
        self.assertIn("127.0.0.1:1220:8080", compose)
        self.assertIn("nobis_data:/app/data", compose)
        self.assertIn("volumes:\n  nobis_data:", compose)

        with open(os.path.join(BASE_DIR, "static", "sw.js"), "r", encoding="utf-8") as f:
            sw_js = f.read()
        self.assertIn("nobis-cockpit-v2.1.0", sw_js)
        self.assertIn("app.js", sw_js)

        with open(os.path.join(BASE_DIR, "static", "index.html"), "r", encoding="utf-8") as f:
            index_html = f.read()
        self.assertIn('id="hdr-status-pill"', index_html)
        self.assertIn('id="hdr-mode-conn-icon"', index_html)
        self.assertIn('id="hdr-container-version"', index_html)
        self.assertIn('id="hdr-docker-version-badge"', index_html)
        self.assertIn('id="cfg-docker-version"', index_html)
        self.assertNotIn('id="hdr-mode-badge"', index_html)
        self.assertNotIn('id="hdr-conn-cloud"', index_html)
        self.assertNotIn('id="hdr-conn-container"', index_html)

        r_health = self.client.get("/health")
        self.assertEqual(r_health.status_code, 200)
        self.assertTrue(r_health.json().get("docker_version", "").startswith("v"))

    def test_docker_persistence_and_env_settings_fallback(self):
        """Vérifie le fallback DATA_DIR si DB_PATH est relatif et l'initialisation par variables d'env."""
        import database

        with patch.dict(os.environ, {"DB_PATH": "nobis_a10.db"}):
            spec = importlib.util.spec_from_file_location("config_isolated", os.path.join(BASE_DIR, "config.py"))
            cfg_mod = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(cfg_mod)
            self.assertTrue(os.path.isabs(cfg_mod.DATA_DIR))
            self.assertEqual(cfg_mod.DATA_DIR, cfg_mod._DEFAULT_DATA_DIR)

        with tempfile.TemporaryDirectory() as tmpdir:
            sys_db = os.path.join(tmpdir, "nobis_a10_system.db")
            mode_dbs = {
                "simulation": os.path.join(tmpdir, "nobis_a10_simulation.db"),
                "agua_cloud": os.path.join(tmpdir, "nobis_a10_agua_cloud.db"),
                "local_gateway": os.path.join(tmpdir, "nobis_a10_local_gateway.db"),
            }
            with patch.object(database, "DATA_DIR", tmpdir), \
                 patch.object(database, "SYSTEM_DB_PATH", sys_db), \
                 patch.object(database, "MODE_DB_PATHS", mode_dbs):
                # 1. Premier démarrage sans variables d'env : email/mdp vides
                with patch.dict(os.environ, {}, clear=False):
                    for k in ("COMM_MODE", "AGUA_EMAIL", "AGUA_PASSWORD", "AGUA_UUID", "AGUA_ID_DEVICE", "AGUA_ID_PRODUCT", "LOCAL_GATEWAY_URL"):
                        os.environ.pop(k, None)
                    database.init_all_databases()

                conn = sqlite3.connect(sys_db)
                conn.row_factory = sqlite3.Row
                rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings").fetchall()}
                conn.close()
                self.assertEqual(rows["agua_email"], "")
                self.assertEqual(rows["agua_password"], "")

                # 2. Redémarrage avec variables d'env : remplit les champs vides sans écraser les valeurs déjà définies
                conn = sqlite3.connect(sys_db)
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('comm_mode', 'simulation')")
                conn.commit()
                conn.close()

                with patch.dict(os.environ, {
                    "COMM_MODE": "agua_cloud",
                    "AGUA_EMAIL": "user@nobis.test",
                    "AGUA_PASSWORD": "secret-pass",
                    "LOCAL_GATEWAY_URL": "http://192.168.1.200",
                }):
                    database.init_all_databases()

                conn = sqlite3.connect(sys_db)
                conn.row_factory = sqlite3.Row
                rows2 = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings").fetchall()}
                conn.close()
                self.assertEqual(rows2["agua_email"], "user@nobis.test")
                self.assertEqual(rows2["agua_password"], "secret-pass")
                # comm_mode déjà non-vide en base reste préservé
                self.assertEqual(rows2["comm_mode"], "simulation")


if __name__ == "__main__":
    unittest.main()
