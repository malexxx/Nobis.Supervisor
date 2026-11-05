import os
import sys
import json
import time
import math
import uuid
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Iterator
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import (
    DATA_DIR,
    SYSTEM_DB_PATH,
    MODE_DB_PATHS,
    HOPPER_CAPACITY_KG,
    STOVE_STATES,
    DEFAULT_REGISTERS_CATALOG,
    TELEMETRY_COLUMNS,
)

# ==============================================================================
# GESTION MULTI-BASES SQLITE, CONTEXT MANAGERS & SEEDER DE SIMULATION
# ==============================================================================
_DB_INITIALIZED = False


def get_sys_db() -> sqlite3.Connection:
    conn = sqlite3.connect(SYSTEM_DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return conn


@contextmanager
def sys_db_session() -> Iterator[sqlite3.Connection]:
    """Gestionnaire de contexte pour la base système avec commit/rollback automatique."""
    conn = get_sys_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_settings_dict() -> Dict[str, str]:
    with sys_db_session() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def get_public_settings_dict() -> Dict[str, str]:
    s = get_settings_dict()
    if "agua_password" in s:
        s["agua_password"] = "********" if s["agua_password"] else ""
    return s


def get_active_mode() -> str:
    try:
        s = get_settings_dict()
        m = s.get("comm_mode", "agua_cloud")
        return m if m in MODE_DB_PATHS else "agua_cloud"
    except Exception:
        return "agua_cloud"


def _ensure_mode_tables(conn: sqlite3.Connection):
    cur = conn.cursor()
    cols_sql = ", ".join([f"{k} {v}" for k, v in TELEMETRY_COLUMNS.items()])
    cur.execute(f"CREATE TABLE IF NOT EXISTS telemetry ({cols_sql})")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_epoch ON telemetry(epoch)")

    cur.execute("PRAGMA table_info(telemetry)")
    existing_cols = {row["name"] for row in cur.fetchall()}
    for col_name, col_def in TELEMETRY_COLUMNS.items():
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE telemetry ADD COLUMN {col_name} {col_def}")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS registers_catalog (
            reg_key TEXT PRIMARY KEY,
            offset INTEGER NOT NULL,
            mem_type TEXT NOT NULL,
            label TEXT NOT NULL,
            unit TEXT,
            min_val REAL,
            max_val REAL,
            step REAL,
            current_val REAL,
            default_val REAL,
            category TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS command_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            reg_key TEXT NOT NULL,
            offset INTEGER NOT NULL,
            label TEXT NOT NULL,
            old_value REAL,
            new_value REAL,
            comm_mode TEXT NOT NULL,
            status TEXT NOT NULL,
            details TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON command_audit(timestamp DESC)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            severity TEXT NOT NULL,
            source TEXT NOT NULL,
            message TEXT NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_severity_id ON app_logs(severity, id DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_id ON app_logs(id DESC)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS registers_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            name TEXT NOT NULL,
            comm_mode TEXT NOT NULL,
            registers_json TEXT NOT NULL,
            raw_buffer_json TEXT NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_id ON registers_snapshots(id DESC)")
    conn.commit()


def get_db(mode: Optional[str] = None) -> sqlite3.Connection:
    global _DB_INITIALIZED
    if not _DB_INITIALIZED:
        init_all_databases()
    target_mode = mode if (mode and mode in MODE_DB_PATHS) else get_active_mode()
    db_file = MODE_DB_PATHS[target_mode]
    conn = sqlite3.connect(db_file, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


@contextmanager
def db_session(mode: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    """Gestionnaire de contexte pour la base du mode actif avec commit/rollback automatique."""
    conn = get_db(mode)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_all_databases():
    global _DB_INITIALIZED
    _DB_INITIALIZED = True
    sconn = get_sys_db()
    try:
        scur = sconn.cursor()
        default_settings = {
            "comm_mode": "agua_cloud",
            "poll_interval_s": "120",
            "agua_email": "",
            "agua_password": "",
            "agua_uuid": str(uuid.uuid4()),
            "agua_id_device": "597775E6-C29D-4CB0-ABF8-36F6C8E704ED",
            "agua_id_product": "CCC78CA4-E701-4976-A981-1F8F61C9D4F7",
            "local_gateway_url": "http://192.168.1.150",
            "weather_city": "Bazoches-les-Gallerandes (45480)",
            "weather_lat": "48.1636",
            "weather_lon": "2.0436",
            "pellet_bag_price_eur": "4.80",
            "pellet_bags_stock": "40",
            "pellet_bags_used_season": "0",
            "webhook_url": "",
            "matter_enabled": "1",
            "matter_device_name": "Poêle Nobis A10",
            "matter_port": "5540"
        }
        old_db = os.path.join(DATA_DIR, "nobis_a10.db")
        if os.path.exists(old_db):
            try:
                oconn = sqlite3.connect(old_db)
                try:
                    oconn.row_factory = sqlite3.Row
                    for r in oconn.execute("SELECT key, value FROM settings").fetchall():
                        if r["value"]:
                            default_settings[r["key"]] = r["value"]
                finally:
                    oconn.close()
            except Exception:
                pass

        for k, v in default_settings.items():
            scur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        sconn.commit()
    finally:
        sconn.close()

    now_iso = datetime.now().isoformat(timespec="seconds")
    for mode_name, db_file in MODE_DB_PATHS.items():
        conn = sqlite3.connect(db_file, timeout=30.0, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            _ensure_mode_tables(conn)
            cur = conn.cursor()
            cur.execute("DELETE FROM registers_catalog WHERE mem_type = 'SUPERVISOR'")
            for reg in DEFAULT_REGISTERS_CATALOG:
                cur.execute("""
                    INSERT OR REPLACE INTO registers_catalog
                    (reg_key, offset, mem_type, label, unit, min_val, max_val, step, current_val, default_val, category, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT current_val FROM registers_catalog WHERE reg_key = ?), ?), ?, ?, ?)
                """, (
                    reg["reg_key"], reg["offset"], reg["mem_type"], reg["label"], reg["unit"],
                    reg["min_val"], reg["max_val"], reg["step"], reg["reg_key"], reg["default_val"], reg["default_val"],
                    reg["category"], now_iso
                ))
            conn.commit()
        finally:
            conn.close()


def db_log(severity: str, source: str, message: str, mode: Optional[str] = None):
    s = (severity or "INFO").strip().upper()
    if s == "WARN":
        s = "WARNING"
    with db_session(mode) as conn:
        conn.execute(
            "INSERT INTO app_logs (timestamp, severity, source, message) VALUES (?, ?, ?, ?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), s, source, str(message))
        )


def get_registers_dict(mode: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    with db_session(mode) as conn:
        rows = conn.execute("SELECT * FROM registers_catalog").fetchall()
        return {r["reg_key"]: dict(r) for r in rows}


def seed_simulation_db_if_empty():
    conn = get_db("simulation")
    try:
        count = conn.execute("SELECT COUNT(*) as c FROM telemetry").fetchone()["c"]
        if count > 0:
            return

        now = datetime.now()
        timestamps: List[datetime] = []
        t_cursor = now - timedelta(days=30)
        cutoff_high_res = now - timedelta(hours=24)
        while t_cursor < cutoff_high_res:
            timestamps.append(t_cursor)
            t_cursor += timedelta(minutes=15)
        while t_cursor <= now:
            timestamps.append(t_cursor)
            t_cursor += timedelta(minutes=2)

        hopper = 92.0
        daily_kg = 0.0
        last_day = timestamps[0].day
        cols = [c for c in TELEMETRY_COLUMNS.keys() if c != "id"]
        placeholders = ", ".join(["?"] * len(cols))
        batch_rows = []

        for dt in timestamps:
            if dt.day != last_day:
                daily_kg = 0.0
                last_day = dt.day
            hour_dec = dt.hour + dt.minute / 60.0
            day_offset = (now - dt).total_seconds() / 86400.0
            ext_temp = round(8.5 + 4.2 * math.sin((hour_dec - 9.0) * math.pi / 12.0) + 2.5 * math.sin(day_offset * 0.4), 1)
            humidity = round(max(45.0, min(96.0, 78.0 - 15.0 * math.sin((hour_dec - 9.0) * math.pi / 12.0))), 1)
            wind_kmh = round(max(4.0, 14.0 + 6.0 * math.sin(day_offset * 0.8)), 1)

            active_heat = (6.5 <= hour_dec <= 9.5) or (17.5 <= hour_dec <= 22.5)
            state = 5 if active_heat else 8
            p_actual = 4 if active_heat else 0

            if active_heat:
                # Température fumées certifiée Nobis A10 Coax : 101°C (P1) à 168°C (P7) -> ~134.5°C en P4
                temp_fumes = round(101.0 + (p_actual - 1) * 11.15 + random.uniform(-3.0, 3.0), 1)
                # Température sonde K foyer : ~500°C en P4 (ratio Flamme/Fumées sain ~3.7)
                temp_k = round(380.0 + p_actual * 30.0 + random.uniform(-8.0, 8.0), 1)
                # Extracteur fumées (pas de 10 RPM sur OFFSET_47) : ~1700 RPM en P4
                rpm = int(round((1420.0 + p_actual * 70.0 + random.uniform(-20.0, 20.0)) / 10.0) * 10)
                # Dépression dynamique coaxiale Ø 80/130 (OFFSET_37 / 10) : ~3.0 Pa en P4
                dep_pa = round(2.3 + p_actual * 0.18 + random.uniform(-0.15, 0.15), 1)
                # Consommation horaire certifiée : 0.86 kg/h (P1) à 2.16 kg/h (P7) -> 1.51 kg/h en P4
                rate_kgh = round(0.86 + (p_actual - 1) * (1.30 / 6.0), 2)
                vent_front_act = 4
            else:
                temp_fumes = round(ext_temp + 15.0, 1)
                temp_k = round(ext_temp + 18.0, 1)
                rpm = 0
                dep_pa = 0.1
                rate_kgh = 0.0
                vent_front_act = 0

            temp_amb = round(19.5 + (1.2 if active_heat else -0.6) + random.uniform(-0.2, 0.2), 1)
            dt_h = (15.0 / 60.0) if dt < cutoff_high_res else (2.0 / 60.0)
            consumed_kg = rate_kgh * dt_h
            daily_kg = round(daily_kg + consumed_kg, 2)

            # Décrémentation sur la capacité certifiée de 35.0 kg (HOPPER_CAPACITY_KG)
            hopper = round(max(10.0, hopper - (consumed_kg / HOPPER_CAPACITY_KG) * 100.0), 1)
            if hopper <= 12.0:
                hopper = 98.0

            # Construction d'un buffer brut réaliste pour l'arbre de décision et l'inspecteur d'offsets
            sim_raw_buf = {
                "0": round(temp_k, 1),
                "2": round(temp_fumes, 1),
                "8": state,
                "11": p_actual,
                "16": vent_front_act,
                "22": 64,
                "28": 40,
                "29": 40,
                "31": 0,
                "32": 0,
                "34": 0,
                "37": int(round(dep_pa * 10.0)),
                "40": 0,
                "47": int(round(rpm / 10.0)),
                "70": 0,
                "71": 0,
                "103": round(temp_amb, 1),
                "105": 18479,
                "107": 2,
                "115": 0,
                "232": 85,
                "33120": 4,
                "33122": 19,
                "33124": 20,
                "33126": 20,
                "33128": 1,
                "33132": 8,
                "33134": 0,
                "33136": 0,
                "33154": 0,
                "33162": 0,
                "33250": 0,
                "34048": dt.hour,
                "34049": dt.minute,
                "34050": dt.day,
                "34051": dt.month,
                "34052": dt.year - 2000,
                "34053": dt.isoweekday()
            }

            row_data = {c: None for c in cols}
            row_data.update({
                "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "epoch": int(dt.timestamp()),
                "comm_mode": "simulation",
                "status_code": state,
                "status_label": STOVE_STATES.get(state, "TRAVAIL"),
                "status_managed": 85,
                "alarm_code": 0,
                "alarm_label": "Aucune (____)",
                "blackout_popup": 0,
                "popup_flags": 0,
                "popup_door_open": 0,
                "popup_pellet_empty": 0,
                "popup_pellet_open": 0,
                "popup_pa_fault": 0,
                "popup_tcj_fault": 0,
                "popup_tck_fault": 0,
                "temp_ambient": temp_amb,
                "temp_amb_setpoint": 19.0,
                "temp_fumes": temp_fumes,
                "temp_probe_k": temp_k,
                "temp_board_ntc": None,
                "exhaust_rpm": rpm,
                "exhaust_target_rpm": None,
                "depression_pa": dep_pa,
                "flame_light_idx": None,
                "glow_plug_on": 0,
                "power_level": 4,
                "power_actual": p_actual,
                "fun_auto_set": 1,
                "standby_enabled": 1,
                "fun_pwf_set": 0,
                "set_silece_set": 0,
                "fan_front_speed": 8,
                "vent_front_actual": vent_front_act,
                "duct_left_temp": 20.0,
                "duct_left_setpoint": 20.0,
                "duct_left_speed": 0,
                "duct_left_actual": 0,
                "duct_right_temp": 20.0,
                "duct_right_setpoint": 20.0,
                "duct_right_speed": 0,
                "duct_right_actual": 0,
                "thermostat_flags": 64,
                "thermostat_main": 0,
                "thermostat_dx": 0,
                "thermostat_sx": 0,
                "enable_mask_105": 18479,
                "enable_mask_107": 2,
                "chrono_week_enable": 0,
                "chrono_p1_start": 48,
                "chrono_p1_stop": 63,
                "chrono_p1_days": 127,
                "chrono_p1_power": 7,
                "chrono_p1_temp": 19.0,
                "chrono_p2_start": 126,
                "chrono_p2_stop": 138,
                "chrono_p2_days": 127,
                "chrono_p2_power": 7,
                "chrono_p2_temp": 19.0,
                "chrono_p3_start": 144,
                "chrono_p3_stop": 144,
                "chrono_p3_days": 0,
                "chrono_p3_power": 7,
                "chrono_p3_temp": 20.0,
                "chrono_p4_start": 144,
                "chrono_p4_stop": 144,
                "chrono_p4_days": 0,
                "chrono_p4_power": 7,
                "chrono_p4_temp": 20.0,
                "stove_clock_str": dt.strftime("%H:%M %d/%m/%Y"),
                "auger_on_time_s": None,
                "auger_cycle_s": None,
                "auger_duty_pct": None,
                "pellet_rate_kgh": rate_kgh,
                "pellet_hopper_pct": hopper,
                "pellet_daily_kg": daily_kg,
                "trim_pellet_pct": 0,
                "trim_exhaust_pct": 0,
                "cat_service_hours": 1420.0,
                "total_work_hours": 2840.0,
                "ignitions_count": 612,
                "weather_temp_ext": ext_temp,
                "weather_humidity": humidity,
                "weather_wind_kmh": wind_kmh,
                "runway_days_est": 15.0,
                "flame_delta_avg": round(temp_k - (380.0 + p_actual * 30.0), 1) if active_heat else 0.0,
                "dep_delta_avg": round(dep_pa - (2.3 + p_actual * 0.18), 1) if active_heat else 0.0,
                "raw_buffer_json": json.dumps(sim_raw_buf)
            })
            batch_rows.append(tuple(row_data[c] for c in cols))

        conn.executemany(f"INSERT INTO telemetry ({', '.join(cols)}) VALUES ({placeholders})", batch_rows)
        conn.commit()
    finally:
        conn.close()
    db_log("INFO", "SEEDER", f"Base simulation initialisée avec {len(batch_rows)} points (30j, réservoir certifié {int(HOPPER_CAPACITY_KG)} kg).", "simulation")


def get_rolling_consumption_stats(conn: sqlite3.Connection, ref_epoch: Optional[int] = None) -> Dict[str, float]:
    """Calcule la consommation cumulée de granulés (en kg) sur 7 jours et 30 jours glissants."""
    if ref_epoch is None:
        ref_epoch = int(time.time())

    # 7 jours glissants (168h)
    min_epoch_7d = ref_epoch - (7 * 86400)
    row_7d = conn.execute("""
        SELECT COALESCE(SUM(daily_max), 0.0) FROM (
            SELECT substr(timestamp, 1, 10) as d, MAX(COALESCE(pellet_daily_kg, 0.0)) as daily_max
            FROM telemetry
            WHERE epoch >= ?
            GROUP BY d
        )
    """, (min_epoch_7d,)).fetchone()
    conso_week_kg = round(float(row_7d[0]) if row_7d else 0.0, 1)

    # 30 jours glissants (720h)
    min_epoch_30d = ref_epoch - (30 * 86400)
    row_30d = conn.execute("""
        SELECT COALESCE(SUM(daily_max), 0.0) FROM (
            SELECT substr(timestamp, 1, 10) as d, MAX(COALESCE(pellet_daily_kg, 0.0)) as daily_max
            FROM telemetry
            WHERE epoch >= ?
            GROUP BY d
        )
    """, (min_epoch_30d,)).fetchone()
    conso_month_kg = round(float(row_30d[0]) if row_30d else 0.0, 1)

    # Cohérence hiérarchique : mois >= semaine
    conso_month_kg = max(conso_month_kg, conso_week_kg)

    return {
        "conso_week_kg": conso_week_kg,
        "conso_month_kg": conso_month_kg
    }


