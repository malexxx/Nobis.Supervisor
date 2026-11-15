import os
import sys
import json
import time
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Set

from fastapi import HTTPException

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import (
    MODE_DB_PATHS,
    HOPPER_CAPACITY_KG,
    PELLET_BAG_WEIGHT_KG,
    STOVE_STATES,
    NOBIS_ALARMS,
    TELEMETRY_COLUMNS,
)
from database import (
    get_db,
    get_settings_dict,
    get_public_settings_dict,
    get_active_mode,
    get_registers_dict,
    db_log,
)
from clients import (
    weather_service,
    agua_client,
    local_gateway_client,
    notification_service,
)
from matter_service import matter_manager

logger = logging.getLogger("nobis.telemetry")

# ==============================================================================
# VERROU ASYNCHRONE BUS POÊLE & DIFFUSION TEMPS RÉEL SSE (SERVER-SENT EVENTS)
# ==============================================================================
_stove_io_lock = asyncio.Lock()
sse_subscribers: Set[asyncio.Queue] = set()

# État de communication avec le Cloud Nobis / Passerelle physique (dissocié de l'état du conteneur)
_comm_status: Dict[str, Any] = {
    "connected": True,
    "last_sync_time": None,
    "last_sync_epoch": None,
    "last_error": None,
    "comm_mode": "agua_cloud",
}


def get_comm_status() -> Dict[str, Any]:
    """Retourne l'état de communication instantané du conteneur avec le Cloud Nobis ou la passerelle."""
    global _comm_status
    mode = get_active_mode()
    # Si non initialisé depuis le démarrage, charger l'état depuis le dernier enregistrement en base
    if _comm_status.get("last_sync_time") is None:
        try:
            conn = get_db(mode)
            try:
                row = conn.execute("SELECT epoch, timestamp, status_code, comm_mode FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
                if row:
                    r_dict = dict(row)
                    st = int(r_dict.get("status_code", 0))
                    _comm_status["connected"] = (st != -1)
                    _comm_status["last_sync_time"] = r_dict.get("timestamp")
                    _comm_status["last_sync_epoch"] = r_dict.get("epoch")
                    _comm_status["comm_mode"] = r_dict.get("comm_mode") or mode
                    if st == -1:
                        _comm_status["last_error"] = "Poêle hors ligne (perte de liaison)"
                else:
                    _comm_status["comm_mode"] = mode
                    _comm_status["connected"] = (mode == "simulation")
            finally:
                conn.close()
        except Exception:
            _comm_status["comm_mode"] = mode
    return dict(_comm_status)


async def broadcast_sse_event(event_type: str, payload: Dict[str, Any]):
    """Diffuse un événement formaté en Server-Sent Events (SSE) à tous les abonnés connectés."""
    if not sse_subscribers:
        return
    dead_queues: List[asyncio.Queue] = []
    msg = json.dumps({"event": event_type, **payload})
    for q in list(sse_subscribers):
        try:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(msg)
        except Exception:
            dead_queues.append(q)
    for dq in dead_queues:
        sse_subscribers.discard(dq)


# ==============================================================================
# ALGORITHMES D'ÉVALUATION, QUALITÉ COMBUSTION & ANALYTICS (DÉLÉGUÉS À ANALYTICS)
# ==============================================================================
from analytics import (
    BATCH_MIN_CYCLES,
    BATCH_MIN_EVAL_MINUTES,
    NOBIS_TRIM_MAX,
    NOBIS_TRIM_MIN,
    STABILIZATION_WARMUP_S,
    _percentile,
    calculate_forecast_runway,
    check_power_stability,
    clamp_nobis_trim,
    enrich_telemetry_record,
    evaluate_batch_combustion_and_trim,
    evaluate_exhaust_quality,
    evaluate_pellet_quality,
    get_pellet_batch_state,
    save_pellet_batch_state,
)

__all__ = [
    "BATCH_MIN_CYCLES",
    "BATCH_MIN_EVAL_MINUTES",
    "NOBIS_TRIM_MAX",
    "NOBIS_TRIM_MIN",
    "STABILIZATION_WARMUP_S",
    "_percentile",
    "calculate_forecast_runway",
    "check_power_stability",
    "clamp_nobis_trim",
    "enrich_telemetry_record",
    "evaluate_batch_combustion_and_trim",
    "evaluate_exhaust_quality",
    "evaluate_pellet_quality",
    "get_pellet_batch_state",
    "save_pellet_batch_state",
]


def prune_database_retention(comm_mode: Optional[str] = None, retention_days: int = 95, max_logs: int = 5000) -> None:
    """
    Nettoyage périodique asynchrone des données historiques (rétention télémétrie 95 jours et limite logs 5000).
    Exécuté hors du chemin critique d'acquisition pour garantir une latence d'écriture minimale sur SQLite.
    """
    active_m = comm_mode or get_active_mode()
    retention_cutoff = int(datetime.now().timestamp()) - (retention_days * 86400)
    conn = get_db(active_m)
    try:
        conn.execute("DELETE FROM telemetry WHERE epoch < ?", (retention_cutoff,))
        conn.execute(
            "DELETE FROM app_logs WHERE id NOT IN (SELECT id FROM app_logs ORDER BY id DESC LIMIT ?)",
            (max_logs,)
        )
        conn.commit()
    finally:
        conn.close()



# ==============================================================================
# COLLECTE ET DÉCODAGE EXHAUSTIF DES 76 OFFSETS DU NOBIS A10 C LIGHT STYLE COAX
# ==============================================================================
async def collect_telemetry_cycle(force_mode: Optional[str] = None) -> Dict[str, Any]:
    """Exécute un cycle complet de collecte et décodage de télémétrie protégé par verrou d'I/O."""
    try:
        await asyncio.wait_for(_stove_io_lock.acquire(), timeout=5.0)
    except asyncio.TimeoutError:
        active_m = force_mode or get_active_mode()
        conn = get_db(active_m)
        try:
            last_row = conn.execute("SELECT * FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
            rec = dict(last_row) if last_row else {}
            if rec:
                enrich_telemetry_record(conn, rec)
            return rec
        finally:
            conn.close()

    try:
        try:
            return await _collect_telemetry_unlocked(force_mode)
        except Exception as e:
            active_m = force_mode or get_active_mode()
            db_log("ERROR", "TELEMETRY", f"Échec inattendu cycle télémétrie : {str(e)}", active_m)
            raise
    finally:
        _stove_io_lock.release()


async def _collect_telemetry_unlocked(force_mode: Optional[str] = None) -> Dict[str, Any]:
    """Lecture du buffer physique/cloud, décodage des 76 registres, enrichissement et diffusion SSE."""
    settings = get_settings_dict()
    comm_mode = force_mode or settings.get("comm_mode", "agua_cloud")
    regs = get_registers_dict(comm_mode)
    weather = await weather_service.fetch_current(settings.get("weather_lat", "48.1636"), settings.get("weather_lon", "2.0436"))

    conn = get_db(comm_mode)
    try:
        last_row = conn.execute("SELECT * FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    prev = dict(last_row) if last_row else {}

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    dt_hours = max(0.005, (int(now.timestamp()) - int(prev.get("epoch", int(now.timestamp()) - 120))) / 3600.0)
    r_val = {k: float(v["current_val"]) for k, v in regs.items()}

    raw_buf: Dict[int, float] = {}
    cloud_error: Optional[str] = None

    if comm_mode == "agua_cloud":
        if settings.get("agua_email") and settings.get("agua_password"):
            try:
                raw_buf = await agua_client.read_buffer(settings)
            except Exception as e:
                cloud_error = str(e)
                db_log("ERROR", "AGUA_CLOUD", f"Échec lecture buffer Agua IOT : {str(e)}", "agua_cloud")
        else:
            cloud_error = "Identifiants Cloud Nobis Agua IOT non renseignés"
    elif comm_mode == "local_gateway":
        gw_url = settings.get("local_gateway_url", "http://192.168.1.150")
        try:
            raw_buf = await local_gateway_client.read_buffer(gw_url)
        except Exception as e:
            cloud_error = str(e)
            db_log("ERROR", "LOCAL_GATEWAY", f"Échec lecture passerelle UART : {str(e)}", "local_gateway")

    if raw_buf:
        for signed_off in (70, 71):
            if signed_off in raw_buf:
                sv = float(raw_buf[signed_off])
                if 128.0 <= sv <= 255.0:
                    raw_buf[signed_off] = sv - 256.0
        for rk, meta in regs.items():
            off = int(meta["offset"])
            if off in raw_buf:
                r_val[rk] = float(raw_buf[off])

        _comm_status["connected"] = True
        _comm_status["last_sync_time"] = now_str
        _comm_status["last_sync_epoch"] = int(now.timestamp())
        _comm_status["last_error"] = None
        _comm_status["comm_mode"] = comm_mode
        if prev.get("status_code") == -1:
            db_log("INFO", "COMMUNICATION", f"Communication avec le poêle rétablie avec succès ({comm_mode}).", comm_mode)

        # Décodage sécurisé de l'état physique du poêle :
        # Si le registre 8 (status_get) n'est pas fourni dans le buffer, ne JAMAIS supposer 5 (TRAVAIL).
        # On utilise le code précédent (si valide et != -1) ou 0 (OFF / Éteint).
        prev_st = prev.get("status_code")
        fallback_st = int(prev_st) if prev_st is not None and prev_st != -1 else 0
        state = int(raw_buf.get(8, raw_buf.get("8", fallback_st)))

        prev_sm = prev.get("status_managed")
        fallback_sm = int(prev_sm) if prev_sm is not None else 0
        status_managed = int(raw_buf.get(232, raw_buf.get("232", fallback_sm)))
        alarm_code = int(raw_buf.get(34, 0))
        alarm_label = NOBIS_ALARMS.get(alarm_code, f"AL{alarm_code}")
        status_label = STOVE_STATES.get(state, f"Code {state}")
        if alarm_code > 0:
            status_label = f"{status_label} [{alarm_label}]"
            prev_alarm = int(prev.get("alarm_code") or 0)
            if alarm_code != prev_alarm:
                db_log("ERROR", "ALARME", f"Alarme poêle active : {alarm_label} (Code {alarm_code})", comm_mode)
        elif int(prev.get("alarm_code") or 0) > 0:
            db_log("INFO", "ALARME", f"Alarme résolue / acquittée (retour état normal, code précédent: {prev.get('alarm_code')}).", comm_mode)

        blackout_popup = int(raw_buf.get(40, 0))
        popup_flags = int(raw_buf.get(115, 0))
        popup_tck = 1 if (popup_flags & 1) else 0
        popup_tcj = 1 if (popup_flags & 2) else 0
        popup_pa = 1 if (popup_flags & 4) else 0
        popup_pellet_open = 1 if (popup_flags & 8) else 0
        popup_door_open = 1 if (popup_flags & 16) else 0
        popup_pellet_empty = 1 if (popup_flags & 64) else 0

        raw_amb = raw_buf.get(103) if 103 in raw_buf else raw_buf.get(1)
        temp_amb = float(raw_amb if raw_amb is not None else (prev.get("temp_ambient") or 19.0))
        setpoint_amb = float(raw_buf.get(33122, r_val.get("temp_amb_setpoint", 19.0)))
        temp_fumes = float(raw_buf.get(2, prev.get("temp_fumes") or 25.0))
        temp_probe_k = float(raw_buf.get(0, prev.get("temp_probe_k") or 25.0))
        rpm = int(round(float(raw_buf.get(47, 0.0)) * 10.0))
        dep_pa = round(float(raw_buf.get(37, 0.0)) / 10.0, 1)

        p_cmd = int(raw_buf.get(33120, r_val.get("power_level", 4)))
        p_actual = int(raw_buf.get(11, p_cmd))
        fun_auto = int(raw_buf.get(33128, r_val.get("fun_auto_set", 1)))
        # Le mode Comfort Clima / Stand-by est géré en local par la radiocommande Nobis et n'est pas exposé
        # en écriture par l'API Agua Cloud. Le poêle signale son état effectif via ses codes de phase (8 = ECO STOP, 9 = STAND BY).
        standby_en = 1 if (state in (8, 9)) else int(raw_buf.get(33130, r_val.get("standby_set", 0) if comm_mode == "simulation" else 0))
        fun_pwf = int(raw_buf.get(33154, r_val.get("fun_pwf_set", 0)))
        set_silence = int(raw_buf.get(33250, r_val.get("set_silece_set", 0)))
        fan_front_set = int(raw_buf.get(33132, r_val.get("fan_front_speed", 3)))
        vent_front_get = int(raw_buf.get(16, 0))

        duct_l_temp = round(float(raw_buf.get(29, 0.0)) / 2.0, 1)
        duct_l_sp = float(raw_buf.get(33126, r_val.get("duct_left_setpoint", 20.0)))
        duct_l_spd = int(raw_buf.get(33136, r_val.get("duct_left_speed", 0)))
        duct_l_act = int(raw_buf.get(32, 0))

        duct_r_temp = round(float(raw_buf.get(28, 0.0)) / 2.0, 1)
        duct_r_sp = float(raw_buf.get(33124, r_val.get("duct_right_setpoint", 20.0)))
        duct_r_spd = int(raw_buf.get(33134, r_val.get("duct_right_speed", 0)))
        duct_r_act = int(raw_buf.get(31, 0))

        t_flags = int(raw_buf.get(22, 0))
        t_sx = 1 if (t_flags & 16) else 0
        t_dx = 1 if (t_flags & 32) else 0
        t_main = 1 if (t_flags & 128) else 0
        mask_105 = int(raw_buf.get(105, 18479))
        mask_107 = int(raw_buf.get(107, 2))

        c_hr = int(raw_buf.get(34048, now.hour))
        c_mn = int(raw_buf.get(34049, now.minute))
        c_day = int(raw_buf.get(34050, now.day))
        c_mon = int(raw_buf.get(34051, now.month))
        c_yr = int(raw_buf.get(34052, now.year - 2000)) + 2000
        c_wday = int(raw_buf.get(34053, now.isoweekday()))
        days_fr = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
        wday_str = days_fr[c_wday - 1] if 1 <= c_wday <= 7 else "---"
        stove_clock_str = f"{wday_str} {c_hr:02d}:{c_mn:02d} {c_day:02d}/{c_mon:02d}/{c_yr}"

        # Débit théorique de granulés estimé selon la consigne de puissance Nobis A10 (OFFSET_11: P1..P8)
        # Formule fixe constructeur : P1 = 0,82 kg/h, P2 = 1,04 kg/h, P3 = 1,26 kg/h, P6 = 1,92 kg/h, P7 = 2,14 kg/h, P8 = 2,36 kg/h
        if state in (2, 3, 4, 5, 6) and p_actual > 0:
            rate_kgh = round(0.60 + p_actual * 0.22, 2)
        else:
            rate_kgh = 0.0

        # Le motoréducteur de vis sans fin Nobis A10 est un moteur Brushless 24V (VIS S. FIN / SET V.S.F. / AMP. V.S.F.)
        # et non un moteur alternatif cadencé ON/OFF. Ces registres d'encodeur/courant n'étant pas dans les 76 offsets
        # du buffer Agua IOT, auger_on_time_s, auger_cycle_s et auger_duty_pct sont à None (NULL) pour éviter toute valeur fictive.
        consumed_kg = rate_kgh * dt_hours
        prev_hopper = float(prev.get("pellet_hopper_pct") or 78.0)
        prev_pellet_empty = int(prev.get("popup_pellet_empty") or 0)

        # Auto-recalibrage intelligent sur la sonde physique de réserve (fond de trémie)
        if popup_pellet_empty:
            if not prev_pellet_empty:
                # Recalibrage précis sur le seuil matériel (~4.2 kg = 12% d'une trémie de 35 kg)
                hopper = 12.0
                db_log("WARNING", "PELLET", "Sonde physique réserve granulés activée : recalibrage automatique de la jauge à 12% (~4.2 kg restant)", comm_mode)
            else:
                hopper = round(max(0.0, min(12.0, prev_hopper - (consumed_kg / HOPPER_CAPACITY_KG) * 100.0)), 1)
        else:
            if prev_pellet_empty:
                # La sonde de réserve était vide et vient d'être recouverte de granulés -> recharge complète de la trémie (100% / 35 kg)
                hopper = 100.0
                db_log("INFO", "PELLET", "Sonde physique réserve granulés réinitialisée (recharge détectée) : recalibrage automatique de la trémie à 100% (35 kg)", comm_mode)
            else:
                hopper = round(max(0.0, min(100.0, prev_hopper - (consumed_kg / HOPPER_CAPACITY_KG) * 100.0)), 1)

        # Prédiction météo DJU 14 jours & Date de rupture (Runway stock disponible)
        try:
            s_dict = get_settings_dict()
            cur_stock_bags = float(s_dict.get("pellet_bags_stock", "40"))
        except (ValueError, TypeError):
            cur_stock_bags = 40.0
        total_kg_avail = (cur_stock_bags * PELLET_BAG_WEIGHT_KG) + (hopper / 100.0 * HOPPER_CAPACITY_KG)
        runway_days = calculate_forecast_runway(total_kg_avail, weather)

        # Calcul de l'écart thermique et de l'écart de dépression avec vérification stricte
        # de non-variation de la consigne de puissance (power_level et power_actual) et de ventilation active sur l'intervalle de 30 min
        is_stable_30m = False
        stable_min = 0
        fans_active = True
        if state == 5 and p_actual > 0:
            target_flame = 430.0 + (p_actual * 40.0)
            instant_delta = round(temp_probe_k - target_flame, 1)
            target_dep = round(2.0 + (p_actual * 0.25), 1)
            instant_dep_delta = round(dep_pa - target_dep, 1)

            conn_prev = get_db(comm_mode)
            try:
                is_stable_30m, stable_min, stable_rows, fans_active = check_power_stability(
                    conn_prev, int(now.timestamp()), state, p_actual, p_cmd, 1800
                )
            finally:
                conn_prev.close()

            # Le lissage sur 30 min n'est validé QUE si la consigne de puissance n'a pas varié dans l'intervalle ET que les ventilateurs ne sont pas coupés
            if is_stable_30m and stable_rows and fans_active:
                past_deltas = [float(r[1]) - target_flame for r in stable_rows if r[1] is not None]
                past_deltas.append(instant_delta)
                flame_delta_avg = round(sum(past_deltas) / len(past_deltas), 1)

                past_dep_deltas = [float(r[2]) - target_dep for r in stable_rows if r[2] is not None]
                past_dep_deltas.append(instant_dep_delta)
                dep_delta_avg = round(sum(past_dep_deltas) / len(past_dep_deltas), 1)
            else:
                flame_delta_avg = None
                dep_delta_avg = None
        else:
            flame_delta_avg = None
            dep_delta_avg = None

        prev_date_str = str(prev.get("timestamp") or "")[:10]
        base_daily_kg = float(prev.get("pellet_daily_kg") or 0.0) if prev_date_str == now_str[:10] else 0.0
        daily_kg = round(base_daily_kg + consumed_kg, 2)
        work_dt_h = dt_hours if state in (2, 3, 4, 5, 6) else 0.0
        prev_state = int(prev.get("status_code") if prev.get("status_code") is not None else state)
        ignitions = int(prev.get("ignitions_count") or 612) + (1 if state in (2, 3, 4, 5) and prev_state in (0, 8, 9) else 0)

        conn = get_db(comm_mode)
        try:
            for rk, meta in regs.items():
                off = int(meta["offset"])
                val_to_store = raw_buf.get(off)
                if val_to_store is None and rk == "temp_ambient" and 1 in raw_buf:
                    val_to_store = raw_buf[1]
                if val_to_store is not None:
                    conn.execute(
                        "UPDATE registers_catalog SET current_val = ?, updated_at = ? WHERE reg_key = ?",
                        (float(val_to_store), now_str, rk)
                    )
            conn.commit()
        finally:
            conn.close()

        record = {
            "timestamp": now_str,
            "epoch": int(now.timestamp()),
            "comm_mode": comm_mode,
            "status_code": state,
            "status_label": status_label,
            "status_managed": status_managed,
            "alarm_code": alarm_code,
            "alarm_label": alarm_label,
            "blackout_popup": blackout_popup,
            "popup_flags": popup_flags,
            "popup_door_open": popup_door_open,
            "popup_pellet_empty": popup_pellet_empty,
            "popup_pellet_open": popup_pellet_open,
            "popup_pa_fault": popup_pa,
            "popup_tcj_fault": popup_tcj,
            "popup_tck_fault": popup_tck,
            "temp_ambient": temp_amb,
            "temp_amb_setpoint": setpoint_amb,
            "temp_fumes": temp_fumes,
            "temp_probe_k": temp_probe_k,
            "temp_board_ntc": None,
            "exhaust_rpm": rpm,
            "exhaust_target_rpm": None,
            "depression_pa": dep_pa,
            "flame_light_idx": None,
            "glow_plug_on": 1 if state in (2, 3, 4) else 0,
            "power_level": p_cmd,
            "power_actual": p_actual,
            "fun_auto_set": fun_auto,
            "standby_enabled": standby_en,
            "fun_pwf_set": fun_pwf,
            "set_silece_set": set_silence,
            "fan_front_speed": fan_front_set,
            "vent_front_actual": vent_front_get,
            "duct_left_temp": duct_l_temp,
            "duct_left_setpoint": duct_l_sp,
            "duct_left_speed": duct_l_spd,
            "duct_left_actual": duct_l_act,
            "duct_right_temp": duct_r_temp,
            "duct_right_setpoint": duct_r_sp,
            "duct_right_speed": duct_r_spd,
            "duct_right_actual": duct_r_act,
            "thermostat_flags": t_flags,
            "thermostat_main": t_main,
            "thermostat_dx": t_dx,
            "thermostat_sx": t_sx,
            "enable_mask_105": mask_105,
            "enable_mask_107": mask_107,
            "chrono_week_enable": int(raw_buf.get(33162, r_val.get("chrono_week_enable_set", 0))),
            "chrono_p1_start": int(raw_buf.get(33164, r_val.get("chrono_p1_start_set", 48))),
            "chrono_p1_stop": int(raw_buf.get(33166, r_val.get("chrono_p1_stop_set", 63))),
            "chrono_p1_days": int(raw_buf.get(33168, r_val.get("chrono_p1_days_mask", 127))),
            "chrono_p1_power": int(raw_buf.get(33170, r_val.get("chrono_p1_power_set", 7))),
            "chrono_p1_temp": float(raw_buf.get(33172, r_val.get("chrono_p1_temperature_set", 19.0))),
            "chrono_p2_start": int(raw_buf.get(33184, r_val.get("chrono_p2_start_set", 126))),
            "chrono_p2_stop": int(raw_buf.get(33186, r_val.get("chrono_p2_stop_set", 138))),
            "chrono_p2_days": int(raw_buf.get(33188, r_val.get("chrono_p2_days_mask", 127))),
            "chrono_p2_power": int(raw_buf.get(33190, r_val.get("chrono_p2_power_set", 7))),
            "chrono_p2_temp": float(raw_buf.get(33192, r_val.get("chrono_p2_temperature_set", 19.0))),
            "chrono_p3_start": int(raw_buf.get(33204, r_val.get("chrono_p3_start_set", 144))),
            "chrono_p3_stop": int(raw_buf.get(33206, r_val.get("chrono_p3_stop_set", 144))),
            "chrono_p3_days": int(raw_buf.get(33208, r_val.get("chrono_p3_days_mask", 0))),
            "chrono_p3_power": int(raw_buf.get(33210, r_val.get("chrono_p3_power_set", 7))),
            "chrono_p3_temp": float(raw_buf.get(33212, r_val.get("chrono_p3_temperature_set", 20.0))),
            "chrono_p4_start": int(raw_buf.get(33224, r_val.get("chrono_p4_start_set", 144))),
            "chrono_p4_stop": int(raw_buf.get(33226, r_val.get("chrono_p4_stop_set", 144))),
            "chrono_p4_days": int(raw_buf.get(33228, r_val.get("chrono_p4_days_mask", 0))),
            "chrono_p4_power": int(raw_buf.get(33230, r_val.get("chrono_p4_power_set", 7))),
            "chrono_p4_temp": float(raw_buf.get(33232, r_val.get("chrono_p4_temperature_set", 20.0))),
            "stove_clock_str": stove_clock_str,
            "auger_on_time_s": None,
            "auger_cycle_s": None,
            "auger_duty_pct": None,
            "pellet_rate_kgh": rate_kgh,
            "pellet_hopper_pct": hopper,
            "pellet_daily_kg": daily_kg,
            "trim_pellet_pct": int(r_val.get("trim_pellet_pct", 0)),
            "trim_exhaust_pct": int(r_val.get("trim_exhaust_pct", 0)),
            "cat_service_hours": float(raw_buf.get(260, r_val.get("cat_service_hours", 1420.0))),
            "total_work_hours": round(float(prev.get("total_work_hours") or 2840.0) + work_dt_h, 2),
            "ignitions_count": ignitions,
            "weather_temp_ext": weather["weather_temp_ext"],
            "weather_humidity": weather["weather_humidity"],
            "weather_wind_kmh": weather["weather_wind_kmh"],
            "runway_days_est": runway_days,
            "flame_delta_avg": flame_delta_avg,
            "dep_delta_avg": dep_delta_avg,
            "raw_buffer_json": json.dumps({str(k): v for k, v in sorted(raw_buf.items())})
        }
    elif comm_mode != "simulation":
        # En mode réel (agua_cloud / local_gateway), une absence de buffer correspond à une perte de connexion :
        # on enregistre des valeurs NULL sur les courbes du poêle pour créer un espace vide (rupture) sur les graphiques.
        _comm_status["connected"] = False
        _comm_status["last_error"] = cloud_error or f"Poêle injoignable ({comm_mode}) : rupture de communication ou absence de trame reçue."
        _comm_status["comm_mode"] = comm_mode
        if prev.get("status_code") != -1:
            db_log("ERROR", "COMMUNICATION", _comm_status["last_error"], comm_mode)
        prev_hopper = float(prev.get("pellet_hopper_pct") or 78.0)
        prev_date_str = str(prev.get("timestamp") or "")[:10]
        daily_kg = float(prev.get("pellet_daily_kg") or 0.0) if prev_date_str == now_str[:10] else 0.0
        record = {c: None for c in TELEMETRY_COLUMNS.keys() if c != "id"}
        record.update({
            "timestamp": now_str,
            "epoch": int(now.timestamp()),
            "comm_mode": comm_mode,
            "status_code": -1,
            "status_label": "HORS LIGNE (Perte connexion)",
            "status_managed": int(prev.get("status_managed") or 0),
            "alarm_code": 0,
            "alarm_label": "Perte de connexion",
            "stove_clock_str": str(prev.get("stove_clock_str") or "--:--"),
            "pellet_hopper_pct": prev_hopper,
            "pellet_daily_kg": daily_kg,
            "cat_service_hours": float(r_val.get("cat_service_hours", 1420.0)),
            "total_work_hours": float(prev.get("total_work_hours") or 2840.0),
            "ignitions_count": int(prev.get("ignitions_count") or 612),
            "weather_temp_ext": weather["weather_temp_ext"],
            "weather_humidity": weather["weather_humidity"],
            "weather_wind_kmh": weather["weather_wind_kmh"],
            "runway_days_est": float(prev.get("runway_days_est") or 15.0),
            "flame_delta_avg": None,
            "dep_delta_avg": None,
            "raw_buffer_json": "{}"
        })
    else:
        _comm_status["connected"] = True
        _comm_status["last_sync_time"] = now_str
        _comm_status["last_sync_epoch"] = int(now.timestamp())
        _comm_status["last_error"] = None
        _comm_status["comm_mode"] = "simulation"
        cmd_on = int(r_val.get("status_code", 85))
        sp_amb = float(r_val.get("temp_amb_setpoint", 19.0))
        prev_amb = float(prev.get("temp_ambient") or 19.5)
        state = 5 if cmd_on == 85 else 0
        standby_en = 1 if (state in (8, 9)) else int(r_val.get("standby_set", 0))
        p_cmd = int(r_val.get("power_level", 4))
        fun_pwf = int(r_val.get("fun_pwf_set", 0))
        fun_auto = int(r_val.get("fun_auto_set", 1))

        # En mode auto total (fun_auto=1), si la température ambiante atteint ou dépasse la consigne,
        # le poêle bascule en modulation minimale P0 (ralenti/économie)
        if state == 5:
            if fun_pwf in (1, 2):
                p_actual = 8
            elif fun_auto == 1 and prev_amb >= sp_amb:
                p_actual = 0
            else:
                p_actual = p_cmd
        else:
            p_actual = 0

        fan_front_cmd = int(r_val.get("fan_front_speed", 3))
        if fan_front_cmd == 8:
            vent_front_actual = 0 if (state == 5 and p_actual == 0) else min(7, max(1, p_actual))
        else:
            vent_front_actual = fan_front_cmd

        duct_left_cmd = int(r_val.get("duct_left_speed", 0))
        if duct_left_cmd == 8:
            duct_left_actual = 0 if (state == 5 and p_actual == 0) else min(7, max(1, p_actual))
        else:
            duct_left_actual = duct_left_cmd

        duct_right_cmd = int(r_val.get("duct_right_speed", 0))
        if duct_right_cmd == 8:
            duct_right_actual = 0 if (state == 5 and p_actual == 0) else min(7, max(1, p_actual))
        else:
            duct_right_actual = duct_right_cmd

        temp_amb = round(prev_amb + (0.01 if state == 5 and p_actual > 0 else (-0.02 if state == 5 and p_actual == 0 else -0.05)), 1)
        temp_fumes = round(101.0 + max(0, p_actual - 1) * 11.15, 1) if (state == 5 and p_actual > 0) else (85.0 if state == 5 and p_actual == 0 else 25.0)
        temp_k = round(380.0 + p_actual * 30.0, 1) if (state == 5 and p_actual > 0) else (320.0 if state == 5 and p_actual == 0 else 28.0)
        rpm = int(1420 + p_actual * 70) if (state == 5 and p_actual > 0) else (1200 if state == 5 and p_actual == 0 else 0)
        sim_dep = round(2.3 + p_actual * 0.18, 1) if (state == 5 and p_actual > 0) else (1.8 if state == 5 and p_actual == 0 else 0.1)
        rate_kgh = round(0.86 + max(0, p_actual - 1) * (1.30 / 6.0), 2) if (state == 5 and p_actual > 0) else (0.45 if state == 5 and p_actual == 0 else 0.0)

        c_hr = int(r_val.get("clock_hour_set", now.hour))
        c_mn = int(r_val.get("clock_minute_set", now.minute))
        c_day = int(r_val.get("calendar_day_set", now.day))
        c_mon = int(r_val.get("calendar_month_set", now.month))
        c_yr = int(r_val.get("calendar_year_set", now.year - 2000)) + 2000
        c_wday = int(r_val.get("calendar_day_week_set", now.isoweekday()))
        days_fr = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
        wday_str = days_fr[c_wday - 1] if 1 <= c_wday <= 7 else "---"
        stove_clock_str = f"{wday_str} {c_hr:02d}:{c_mn:02d} {c_day:02d}/{c_mon:02d}/{c_yr}"

        consumed_kg = rate_kgh * dt_hours
        prev_hopper = float(prev.get("pellet_hopper_pct", 78.0))
        hopper = round(max(0.0, min(100.0, prev_hopper - (consumed_kg / HOPPER_CAPACITY_KG) * 100.0)), 1)
        prev_date_str = str(prev.get("timestamp", ""))[:10]
        base_daily_kg = float(prev.get("pellet_daily_kg", 0.0)) if prev_date_str == now_str[:10] else 0.0
        daily_kg = round(base_daily_kg + consumed_kg, 2)
        work_dt_h = dt_hours if state == 5 else 0.0
        prev_state = int(prev.get("status_code", state))
        ignitions = int(prev.get("ignitions_count", 612)) + (1 if state == 5 and prev_state in (0, 8) else 0)

        try:
            s_dict = get_settings_dict()
            cur_stock_bags = float(s_dict.get("pellet_bags_stock", "40"))
        except (ValueError, TypeError):
            cur_stock_bags = 40.0
        total_kg_avail = (cur_stock_bags * PELLET_BAG_WEIGHT_KG) + (hopper / 100.0 * HOPPER_CAPACITY_KG)
        runway_days = calculate_forecast_runway(total_kg_avail, weather)

        # Calcul de l'écart thermique et de l'écart de dépression avec vérification stricte
        # de non-variation de la consigne de puissance (power_level et power_actual) et de ventilation active sur l'intervalle de 30 min
        is_stable_30m = False
        stable_min = 0
        fans_active = True
        if state == 5 and p_actual > 0:
            target_flame = round(380.0 + p_actual * 30.0, 1)
            instant_delta = round(temp_k - target_flame, 1)
            target_dep = round(2.3 + p_actual * 0.18, 1)
            instant_dep_delta = round(sim_dep - target_dep, 1)

            conn_prev = get_db(comm_mode)
            try:
                is_stable_30m, stable_min, stable_rows, fans_active = check_power_stability(
                    conn_prev, int(now.timestamp()), state, p_actual, p_cmd, 1800
                )
            finally:
                conn_prev.close()

            # Le lissage sur 30 min n'est validé QUE si la consigne de puissance n'a pas varié dans l'intervalle ET que les ventilateurs ne sont pas coupés
            if is_stable_30m and stable_rows and fans_active:
                past_deltas = [float(r[1]) - target_flame for r in stable_rows if r[1] is not None]
                past_deltas.append(instant_delta)
                flame_delta_avg = round(sum(past_deltas) / len(past_deltas), 1)

                past_dep_deltas = [float(r[2]) - target_dep for r in stable_rows if r[2] is not None]
                past_dep_deltas.append(instant_dep_delta)
                dep_delta_avg = round(sum(past_dep_deltas) / len(past_dep_deltas), 1)
            else:
                flame_delta_avg = None
                dep_delta_avg = None
        else:
            flame_delta_avg = None
            dep_delta_avg = None

        sim_raw_buf = {
            "0": round(temp_k, 1),
            "2": round(temp_fumes, 1),
            "8": state,
            "11": p_actual,
            "16": vent_front_actual,
            "22": 64,
            "28": 40,
            "29": 40,
            "31": duct_right_actual,
            "32": duct_left_actual,
            "34": 0,
            "37": int(round(sim_dep * 10.0)),
            "40": 0,
            "47": int(round(rpm / 10.0)),
            "70": int(r_val.get("trim_pellet_pct", 0)),
            "71": int(r_val.get("trim_exhaust_pct", 0)),
            "103": round(temp_amb, 1),
            "105": 18479,
            "107": 2,
            "115": 0,
            "232": cmd_on,
            "33120": p_cmd,
            "33122": int(sp_amb),
            "33128": int(r_val.get("fun_auto_set", 1)),
            "33130": standby_en,
            "33132": int(r_val.get("fan_front_speed", 3)),
            "33134": int(r_val.get("duct_right_speed", 0)),
            "33136": int(r_val.get("duct_left_speed", 0)),
            "33154": fun_pwf,
            "33162": int(r_val.get("chrono_week_enable_set", 0)),
            "33164": int(r_val.get("chrono_p1_start_set", 48)),
            "33166": int(r_val.get("chrono_p1_stop_set", 63)),
            "33168": int(r_val.get("chrono_p1_days_mask", 127)),
            "33170": int(r_val.get("chrono_p1_power_set", 7)),
            "33172": int(r_val.get("chrono_p1_temperature_set", 19)),
            "33174": int(r_val.get("chrono_p1_tcanr_set", 20)),
            "33176": int(r_val.get("chrono_p1_tcanl_set", 20)),
            "33178": int(r_val.get("chrono_p1_fan_front_set", 0)),
            "33180": int(r_val.get("chrono_p1_canr_set", 0)),
            "33182": int(r_val.get("chrono_p1_canl_set", 0)),
            "33184": int(r_val.get("chrono_p2_start_set", 126)),
            "33186": int(r_val.get("chrono_p2_stop_set", 138)),
            "33188": int(r_val.get("chrono_p2_days_mask", 127)),
            "33190": int(r_val.get("chrono_p2_power_set", 7)),
            "33192": int(r_val.get("chrono_p2_temperature_set", 19)),
            "33194": int(r_val.get("chrono_p2_tcanr_set", 20)),
            "33196": int(r_val.get("chrono_p2_tcanl_set", 20)),
            "33198": int(r_val.get("chrono_p2_fan_front_set", 0)),
            "33200": int(r_val.get("chrono_p2_canr_set", 0)),
            "33202": int(r_val.get("chrono_p2_canl_set", 0)),
            "33204": int(r_val.get("chrono_p3_start_set", 144)),
            "33206": int(r_val.get("chrono_p3_stop_set", 144)),
            "33208": int(r_val.get("chrono_p3_days_mask", 0)),
            "33210": int(r_val.get("chrono_p3_power_set", 7)),
            "33212": int(r_val.get("chrono_p3_temperature_set", 20)),
            "33214": int(r_val.get("chrono_p3_tcanr_set", 20)),
            "33216": int(r_val.get("chrono_p3_tcanl_set", 20)),
            "33218": int(r_val.get("chrono_p3_fan_front_set", 0)),
            "33220": int(r_val.get("chrono_p3_canr_set", 0)),
            "33222": int(r_val.get("chrono_p3_canl_set", 0)),
            "33224": int(r_val.get("chrono_p4_start_set", 144)),
            "33226": int(r_val.get("chrono_p4_stop_set", 144)),
            "33228": int(r_val.get("chrono_p4_days_mask", 0)),
            "33230": int(r_val.get("chrono_p4_power_set", 7)),
            "33232": int(r_val.get("chrono_p4_temperature_set", 20)),
            "33234": int(r_val.get("chrono_p4_tcanr_set", 20)),
            "33236": int(r_val.get("chrono_p4_tcanl_set", 20)),
            "33238": int(r_val.get("chrono_p4_fan_front_set", 0)),
            "33240": int(r_val.get("chrono_p4_canr_set", 0)),
            "33242": int(r_val.get("chrono_p4_canl_set", 0)),
            "33250": int(r_val.get("set_silece_set", 0)),
            "34048": now.hour,
            "34049": now.minute,
            "34050": now.day,
            "34051": now.month,
            "34052": now.year - 2000,
            "34053": now.isoweekday()
        }

        record = {c: 0 for c in TELEMETRY_COLUMNS.keys() if c != "id"}
        record.update({
            "timestamp": now_str,
            "epoch": int(now.timestamp()),
            "comm_mode": comm_mode,
            "status_code": state,
            "status_label": STOVE_STATES.get(state, "TRAVAIL"),
            "status_managed": cmd_on,
            "alarm_code": 0,
            "alarm_label": "Aucune (____)",
            "temp_ambient": temp_amb,
            "temp_amb_setpoint": sp_amb,
            "temp_fumes": temp_fumes,
            "temp_probe_k": temp_k,
            "temp_board_ntc": None,
            "exhaust_rpm": rpm,
            "exhaust_target_rpm": None,
            "depression_pa": sim_dep,
            "flame_light_idx": None,
            "glow_plug_on": 1 if state in (2, 3, 4) else 0,
            "power_level": p_cmd,
            "power_actual": p_actual,
            "fun_auto_set": fun_auto,
            "standby_enabled": standby_en,
            "fun_pwf_set": fun_pwf,
            "set_silece_set": int(r_val.get("set_silece_set", 0)),
            "fan_front_speed": fan_front_cmd,
            "vent_front_actual": vent_front_actual,
            "duct_left_temp": float(r_val.get("duct_left_setpoint", 20.0)),
            "duct_left_setpoint": float(r_val.get("duct_left_setpoint", 20.0)),
            "duct_left_speed": duct_left_cmd,
            "duct_left_actual": duct_left_actual,
            "duct_right_temp": float(r_val.get("duct_right_setpoint", 20.0)),
            "duct_right_setpoint": float(r_val.get("duct_right_setpoint", 20.0)),
            "duct_right_speed": duct_right_cmd,
            "duct_right_actual": duct_right_actual,
            "chrono_week_enable": int(r_val.get("chrono_week_enable_set", 0)),
            "chrono_p1_start": int(r_val.get("chrono_p1_start_set", 48)),
            "chrono_p1_stop": int(r_val.get("chrono_p1_stop_set", 63)),
            "chrono_p1_days": int(r_val.get("chrono_p1_days_mask", 127)),
            "chrono_p1_power": int(r_val.get("chrono_p1_power_set", 7)),
            "chrono_p1_temp": float(r_val.get("chrono_p1_temperature_set", 19.0)),
            "chrono_p2_start": int(r_val.get("chrono_p2_start_set", 126)),
            "chrono_p2_stop": int(r_val.get("chrono_p2_stop_set", 138)),
            "chrono_p2_days": int(r_val.get("chrono_p2_days_mask", 127)),
            "chrono_p2_power": int(r_val.get("chrono_p2_power_set", 7)),
            "chrono_p2_temp": float(r_val.get("chrono_p2_temperature_set", 19.0)),
            "chrono_p3_start": int(r_val.get("chrono_p3_start_set", 144)),
            "chrono_p3_stop": int(r_val.get("chrono_p3_stop_set", 144)),
            "chrono_p3_days": int(r_val.get("chrono_p3_days_mask", 0)),
            "chrono_p3_power": int(r_val.get("chrono_p3_power_set", 7)),
            "chrono_p3_temp": float(r_val.get("chrono_p3_temperature_set", 20.0)),
            "chrono_p4_start": int(r_val.get("chrono_p4_start_set", 144)),
            "chrono_p4_stop": int(r_val.get("chrono_p4_stop_set", 144)),
            "chrono_p4_days": int(r_val.get("chrono_p4_days_mask", 0)),
            "chrono_p4_power": int(r_val.get("chrono_p4_power_set", 7)),
            "chrono_p4_temp": float(r_val.get("chrono_p4_temperature_set", 20.0)),
            "stove_clock_str": stove_clock_str,
            "auger_on_time_s": None,
            "auger_cycle_s": None,
            "auger_duty_pct": None,
            "pellet_rate_kgh": rate_kgh,
            "pellet_hopper_pct": hopper,
            "pellet_daily_kg": daily_kg,
            "trim_pellet_pct": int(r_val.get("trim_pellet_pct", 0)),
            "trim_exhaust_pct": int(r_val.get("trim_exhaust_pct", 0)),
            "cat_service_hours": float(r_val.get("cat_service_hours", 1420.0)),
            "total_work_hours": round(float(prev.get("total_work_hours", 2840.5)) + work_dt_h, 2),
            "ignitions_count": ignitions,
            "weather_temp_ext": weather["weather_temp_ext"],
            "weather_humidity": weather["weather_humidity"],
            "weather_wind_kmh": weather["weather_wind_kmh"],
            "runway_days_est": runway_days,
            "flame_delta_avg": flame_delta_avg,
            "dep_delta_avg": dep_delta_avg,
            "raw_buffer_json": json.dumps(sim_raw_buf)
        })

    cols = [c for c in TELEMETRY_COLUMNS.keys() if c != "id"]
    placeholders = ", ".join(["?"] * len(cols))
    conn = get_db(comm_mode)
    try:
        conn.execute(
            f"INSERT INTO telemetry ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(record[c] for c in cols)
        )
        conn.commit()
        enrich_telemetry_record(conn, record)
    finally:
        conn.close()

    consumption_stats = {
        "conso_week_kg": record.get("conso_week_kg", 0.0),
        "conso_month_kg": record.get("conso_month_kg", 0.0)
    }

    await notification_service.evaluate_telemetry_alerts(record, settings)
    await broadcast_sse_event("telemetry", {
        "active_mode": comm_mode,
        "active_db_file": os.path.basename(MODE_DB_PATHS.get(comm_mode, MODE_DB_PATHS["simulation"])),
        "latest": record,
        "comm_status": get_comm_status(),
        "settings": get_public_settings_dict(),
        "registers": get_registers_dict(comm_mode),
        "consumption_stats": consumption_stats,
        "pellet_quality": record.get("pellet_quality"),
        "exhaust_quality": record.get("exhaust_quality")
    })
    await matter_manager.sync_telemetry(record)

    # Traitement en tâche de fond de l'étalonnage précis (A10)
    try:
        from src.calibration.calibration_service import get_calibration_service
        calib_srv = get_calibration_service()
        calib_srv.process_telemetry_record(record, raw_buf)
    except Exception as e:
        logger.debug(f"Erreur d'ingestion calibration en tâche de fond : {e}")

    return record


# ==============================================================================
# EXÉCUTION D'ÉCRITURES UNITAIRES ET GROUPÉES (BATCH) SUR LES REGISTRES
# ==============================================================================
async def execute_registers_batch_write(
    commands: List[Dict[str, Any]],
    skip_poll: bool = True
) -> Dict[str, Any]:
    """
    Exécute une transaction groupée (batch) d'écritures de registres :
    - Validation des bornes min/max et du pas (step).
    - Dispatch vers le client approprié (Agua Cloud RWMSmaster, Passerelle locale UART ou Simulation).
    - Traçabilité dans command_audit et diffusion SSE.
    """
    if not commands:
        raise HTTPException(status_code=400, detail="Liste de commandes vide")

    try:
        await asyncio.wait_for(_stove_io_lock.acquire(), timeout=5.0)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="Le bus de communication poêle est actuellement occupé. Veuillez réessayer."
        )
    try:
        settings = get_settings_dict()
        comm_mode = get_active_mode()
        regs = get_registers_dict(comm_mode)

        normalized_cmds: List[Dict[str, Any]] = []
        cloud_items_spec: List[Tuple[int, float, Optional[str]]] = []

        for cmd in commands:
            rk = str(cmd.get("reg_key") or "").strip()
            if not rk:
                raise HTTPException(status_code=400, detail="Paramètre reg_key requis pour chaque commande")
            try:
                val = float(cmd.get("value", 0))
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"Valeur numérique invalide pour {rk}")

            custom_offset = cmd.get("offset")
            if rk == "status_code" and val in (6, 9):
                val = 85.0 if val == 6 else 170.0

            if rk in regs:
                meta = regs[rk]
                offset = int(meta["offset"])
                label = meta["label"]
                old_val = float(meta["current_val"])
                if val < meta["min_val"] or val > meta["max_val"]:
                    raise HTTPException(status_code=400, detail=f"Valeur hors limites pour {rk} [{meta['min_val']} .. {meta['max_val']}]")
            else:
                try:
                    offset = int(custom_offset or 0)
                except (ValueError, TypeError):
                    raise HTTPException(status_code=400, detail="Offset invalide")
                label = f"Offset Brut #{offset}"
                old_val = 0.0

            is_trim = (offset in (70, 71) or rk in ("trim_pellet_pct", "trim_exhaust_pct"))
            is_standby = (offset == 33130 or rk == "standby_set")
            normalized_cmds.append({
                "reg_key": rk,
                "offset": offset,
                "label": label,
                "old_val": old_val,
                "new_val": val,
                "in_catalog": rk in regs,
                "is_trim": is_trim,
                "is_standby": is_standby
            })
            # En mode Agua Cloud, les offsets 70 et 71 (tarages MÉLANGE G/A) ainsi que 33130 (Confort Clima)
            # ne figurent pas dans la table Cloud Agua IOT. On ne les injecte pas dans cloud_items_spec
            # pour éviter d'envoyer des commandes inexistantes ou génératrices d'erreurs au Cloud.
            if not (comm_mode == "agua_cloud" and (is_trim or is_standby)):
                cloud_items_spec.append((offset, val, rk))

        status_str = "SUCCESS"
        details_str = f"Écriture {comm_mode}"

        if comm_mode == "agua_cloud":
            if cloud_items_spec:
                if not settings.get("agua_email") or not settings.get("agua_password"):
                    db_log("WARNING", "WRITE_CMD", f"Identifiants Agua IOT absents : écriture locale appliquée ({len(normalized_cmds)} reg.).", comm_mode)
                    details_str = "Appliqué en base locale (identifiants Cloud absents)"
                else:
                    try:
                        w_res = await agua_client.write_registers_batch(settings, cloud_items_spec)
                        details_str = f"Écriture Agua IOT via {w_res.get('endpoint', '/deviceRequestWriting')}"
                    except Exception as e:
                        first = normalized_cmds[0]
                        db_log("ERROR", "WRITE_CMD", f"Échec écriture Cloud {first['label']} (#{first['offset']}) : {str(e)}", comm_mode)
                        raise HTTPException(status_code=502, detail=f"Erreur Cloud Agua IOT : {str(e)}")
            else:
                details_str = "Mémorisé localement (à valider sur radiocommande: MENU ▸ PROG. UTILISATEUR ▸ MÉLANGE G/A ou COMFORT CLIMA)"
        elif comm_mode == "local_gateway" and cloud_items_spec:
            gw_url = settings.get("local_gateway_url", "http://192.168.1.150")
            try:
                await local_gateway_client.write_registers(gw_url, normalized_cmds)
            except Exception as e:
                db_log("ERROR", "WRITE_CMD", f"Échec écriture UART : {str(e)}", comm_mode)
                raise HTTPException(status_code=502, detail=f"Erreur passerelle UART : {str(e)}")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db(comm_mode)
        try:
            for c in normalized_cmds:
                if c["in_catalog"]:
                    conn.execute(
                        "UPDATE registers_catalog SET current_val = ?, updated_at = ? WHERE reg_key = ?",
                        (c["new_val"], now_str, c["reg_key"])
                    )
                c_details = details_str
                if comm_mode == "agua_cloud":
                    if c.get("is_trim"):
                        c_details = "Mémorisé localement (à valider sur radiocommande: MENU ▸ PROG. UTILISATEUR ▸ MÉLANGE G/A)"
                    elif c.get("is_standby"):
                        c_details = "Mémorisé localement (à régler sur radiocommande: MENU ▸ UTILISATEUR ▸ COMFORT CLIMA)"
                conn.execute("""
                    INSERT INTO command_audit (timestamp, reg_key, offset, label, old_value, new_value, comm_mode, status, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (now_str, c["reg_key"], c["offset"], c["label"], c["old_val"], c["new_val"], comm_mode, status_str, c_details))
            conn.commit()
        finally:
            conn.close()

        for c in normalized_cmds:
            if comm_mode == "agua_cloud" and c.get("is_trim"):
                db_log("COMMAND", "PILOTAGE", f"{c['label']} [Offset {c['offset']}] : mémorisé à {c['new_val']:+g}%. Validez sur votre radiocommande physique (MENU ▸ PROGRAMMATION UTILISATEUR ▸ MÉLANGE G/A) car les offsets 70/71 ne figurent pas dans la table Cloud Agua IOT.", comm_mode)
            elif comm_mode == "agua_cloud" and c.get("is_standby"):
                db_log("COMMAND", "PILOTAGE", f"{c['label']} [Offset {c['offset']}] : consigne mémorisée à {c['new_val']}. La fonction Comfort Clima est régie en local par la radiocommande Nobis (MENU ▸ UTILISATEUR ▸ COMFORT CLIMA) et n'est pas modifiable à distance via l'API Cloud Agua IOT.", comm_mode)
            else:
                db_log("COMMAND", "PILOTAGE", f"{c['label']} [Offset {c['offset']}] : {c['old_val']} -> {c['new_val']} ({details_str})", comm_mode)

        latest = {} if skip_poll else await _collect_telemetry_unlocked(comm_mode)
        first = normalized_cmds[0]
        return {
            "status": "ok",
            "count": len(normalized_cmds),
            "reg_key": first["reg_key"],
            "old_value": first["old_val"],
            "new_value": first["new_val"],
            "latest": latest
        }
    finally:
        _stove_io_lock.release()


async def execute_register_write(reg_key: str, new_value: float, custom_offset: Optional[int] = None, skip_poll: bool = True) -> Dict[str, Any]:
    """Écriture unitaire d'un registre en déléguant à la transaction batch unifiée."""
    return await execute_registers_batch_write(
        [{"reg_key": reg_key, "value": new_value, "offset": custom_offset}],
        skip_poll=skip_poll
    )


async def reset_pellet_batch_calibration(pellet_brand: str = "Nouveau Lot Certifié A1") -> Dict[str, Any]:
    """
    Action du bouton 'RAZ Nouveau Lot de Pellets' :
    1. Réinitialise l'horodatage d'évaluation (raz_epoch = maintenant) et la référence aéraulique.
    2. Remet les tarages trim_pellet_pct et trim_exhaust_pct à 0% dans le catalogue.
    3. Journalise l'événement dans app_logs et command_audit.
    """
    active_mode = get_active_mode()
    now_ts = int(time.time())
    now_str = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")
    clean_brand = (pellet_brand or "Nouveau Lot Certifié A1").strip()[:60]

    new_state = {
        "raz_epoch": now_ts,
        "raz_timestamp": now_str,
        "pellet_brand": clean_brand,
        "baseline_rpm_per_pa": None,
        "last_applied_epoch": now_ts
    }
    save_pellet_batch_state(new_state)

    # Remise à 0% des registres de tarage dans la base active
    await execute_registers_batch_write([
        {"reg_key": "trim_pellet_pct", "value": 0.0, "offset": 70},
        {"reg_key": "trim_exhaust_pct", "value": 0.0, "offset": 71},
    ], skip_poll=True)

    db_log(
        "COMMAND",
        "PELLET_RAZ",
        f"RAZ Algorithme Mélange P/A exécuté pour le lot « {clean_brand} » : tarages remis à 0% et fenêtre de {int(BATCH_MIN_EVAL_MINUTES)} min réinitialisée.",
        active_mode
    )
    latest = await collect_telemetry_cycle(active_mode)
    return {
        "status": "ok",
        "pellet_batch_state": new_state,
        "latest": latest
    }
