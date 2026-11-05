import os
import sys
import json
import asyncio
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
    get_rolling_consumption_stats,
    db_log,
)
from clients import (
    borrow_http_client,
    weather_service,
    agua_client,
    local_gateway_client,
    notification_service,
)
from matter_service import matter_manager

# ==============================================================================
# VERROU ASYNCHRONE BUS POÊLE & DIFFUSION TEMPS RÉEL SSE (SERVER-SENT EVENTS)
# ==============================================================================
_stove_io_lock = asyncio.Lock()
sse_subscribers: Set[asyncio.Queue] = set()


async def broadcast_sse_event(event_type: str, payload: Dict[str, Any]):
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


def calculate_forecast_runway(total_kg_avail: float, weather: Dict[str, Any]) -> float:
    """Calcule l'autonomie en jours du stock restant en simulant la consommation jour par jour
    selon les prévisions météo réelles Open-Meteo sur 14 jours (Degrés-Jours Unifiés DJU)."""
    if total_kg_avail <= 0.0:
        return 0.0

    forecast_means = weather.get("forecast_daily_means") or []
    if not forecast_means:
        t_ext = float(weather.get("weather_temp_ext") or 10.0)
        dju = max(0.0, 19.0 - t_ext)
        daily_cons = max(3.5, round(dju * 0.78, 2))
        return round(total_kg_avail / daily_cons, 1)

    rem_kg = total_kg_avail
    accumulated_days = 0.0
    daily_conso_list = []

    for t_mean in forecast_means:
        dju = max(0.0, 19.0 - float(t_mean))
        c_day = max(3.5, round(dju * 0.78, 2))
        daily_conso_list.append(c_day)

        if rem_kg >= c_day:
            rem_kg -= c_day
            accumulated_days += 1.0
        else:
            accumulated_days += round(rem_kg / c_day, 1)
            rem_kg = 0.0
            break

    # Si du stock subsiste au-delà des 14 jours de prévisions, prolonger sur la moyenne des 14 jours
    if rem_kg > 0.0 and daily_conso_list:
        avg_c = sum(daily_conso_list) / len(daily_conso_list)
        accumulated_days += round(rem_kg / max(3.5, avg_c), 1)

    return round(accumulated_days, 1)


def check_power_stability(
    conn: Any,
    now_epoch: int,
    status_code: int,
    power_actual: int,
    power_level: int,
    window_seconds: int = 900
) -> Tuple[bool, int, List[Any]]:
    """Vérifie si la consigne de puissance (power_level et power_actual) est restée strictement constante
    sans aucune variation sur l'intervalle de 15 minutes (900 secondes).
    Retourne (is_stable_15m, stable_minutes, rows_in_window)."""
    if status_code != 5 or power_actual <= 0:
        return (False, 0, [])

    cutoff = now_epoch - window_seconds

    # 1. Chercher s'il y a eu une variation de consigne/puissance ou de statut dans les 15 dernières minutes
    var_row = conn.execute("""
        SELECT MAX(epoch)
        FROM telemetry
        WHERE epoch >= ? AND epoch <= ?
          AND (status_code != 5 OR power_actual != ? OR power_level != ?)
    """, (cutoff, now_epoch, power_actual, power_level)).fetchone()

    last_var_epoch = var_row[0] if var_row and var_row[0] is not None else None

    if last_var_epoch is not None:
        stable_seconds = max(0, now_epoch - last_var_epoch)
        stable_start = last_var_epoch + 1
    else:
        # Aucune variation dans les 15 min : chercher le point de départ de ce palier stable
        oldest_row = conn.execute("""
            SELECT MIN(epoch)
            FROM telemetry
            WHERE epoch >= ? AND epoch <= ?
              AND status_code = 5 AND power_actual = ? AND power_level = ?
        """, (cutoff - 3600, now_epoch, power_actual, power_level)).fetchone()
        if oldest_row and oldest_row[0] is not None:
            stable_seconds = max(0, now_epoch - oldest_row[0])
        else:
            stable_seconds = 0
        stable_start = cutoff

    stable_minutes = min(15, max(1, int(stable_seconds // 60)))
    # Stable 15 min sans variation : aucune variation dans la fenêtre et historique d'au moins ~14 min (840s)
    is_stable_15m = (last_var_epoch is None) and (stable_seconds >= 840)

    # Récupérer les lignes enregistrées strictement à ce palier de puissance
    query_since = cutoff if is_stable_15m else stable_start
    stable_rows = conn.execute("""
        SELECT epoch, temp_probe_k, depression_pa
        FROM telemetry
        WHERE status_code = 5 
          AND power_actual = ? 
          AND power_level = ?
          AND epoch >= ? AND epoch <= ?
        ORDER BY epoch ASC
    """, (power_actual, power_level, query_since, now_epoch)).fetchall()

    return (is_stable_15m, stable_minutes, stable_rows)


def evaluate_pellet_quality(
    status_code: int,
    power_actual: int,
    temp_probe_k: float,
    temp_fumes: float,
    current_trim: int,
    flame_delta_avg: Optional[float] = None,
    stable_minutes: Optional[int] = None,
    power_level: Optional[int] = None
) -> Dict[str, Any]:
    """Évalue la qualité du combustible granulé et propose un ajustement de tarage par IA
    en se basant sur l'écart thermique moyenné sur 15 minutes à consigne de puissance strictement constante."""
    if status_code != 5 or power_actual <= 0:
        trim_str = f"{current_trim:+d}%" if current_trim != 0 else "0%"
        if status_code == 5 and power_actual == 0:
            return {
                "status": "modulating",
                "quality_score": None,
                "quality_label": "Modulation P0 (Éco)",
                "quality_color": "sky",
                "target_flame_temp": None,
                "flame_delta": None,
                "flame_delta_avg": None,
                "stable_minutes": 0,
                "current_trim": current_trim,
                "suggested_trim": current_trim,
                "explanation": (
                    f"Poêle en modulation P0 (Ralenti / Consigne atteinte). "
                    f"L'apport de granulés est réduit au strict minimum de maintien de braise. "
                    f"L'analyse IA de calibrage requiert un régime de travail nominal P1 à P7. "
                    f"Tarage granulés en mémoire : {trim_str}."
                ),
                "action_needed": False
            }
        return {
            "status": "idle",
            "quality_score": None,
            "quality_label": "En attente combustion",
            "quality_color": "gray",
            "target_flame_temp": None,
            "flame_delta": None,
            "flame_delta_avg": None,
            "stable_minutes": 0,
            "current_trim": current_trim,
            "suggested_trim": current_trim,
            "explanation": f"L'évaluation thermique requiert que le poêle soit en phase active de travail (P1 à P7). Tarage granulés en mémoire : {trim_str}.",
            "action_needed": False
        }

    # Flamme théorique nominale pour la puissance demandée Nobis A10 (P1..P7)
    target_flame = 430.0 + (power_actual * 40.0)
    instant_delta = round(temp_probe_k - target_flame, 1)

    # Si la consigne de puissance a varié dans l'intervalle de 15 minutes (lissage non stabilisé)
    if flame_delta_avg is None:
        mins_txt = f"{stable_minutes}/15 min" if stable_minutes else "en cours"
        p_txt = f"P{power_actual}"
        return {
            "status": "stabilizing",
            "quality_score": None,
            "quality_label": f"Stabilisation {p_txt} ({mins_txt})",
            "quality_color": "sky",
            "target_flame_temp": round(target_flame, 1),
            "flame_delta": instant_delta,
            "flame_delta_avg": None,
            "stable_minutes": stable_minutes or 0,
            "current_trim": current_trim,
            "suggested_trim": current_trim,
            "explanation": (
                f"Consigne {p_txt} active depuis {stable_minutes or 1} min. "
                f"Le lissage sur 15 minutes requiert que la consigne de puissance ne varie pas dans l'intervalle "
                f"pour atteindre le régime thermique établi avant d'évaluer la qualité du granulé."
            ),
            "action_needed": False
        }

    delta = round(flame_delta_avg, 1)

    if delta > 30.0:
        suggested = max(-20, current_trim - 5)
        score = max(60, round(100 - abs(delta) * 0.5))
        label = "Haute Énergie (Très sec / PCI élevé)"
        color = "amber"
        action_needed = (suggested != current_trim)
        explanation = (
            f"Combustion vive (+{delta}°C vs cible P{power_actual}, moyenne 15 min à puissance constante). Granulé très dense et sec (fort pouvoir calorifique). "
            f"Conseil IA : réduire l'alimentation à {suggested:+d}% pour économiser du combustible et préserver le creuset."
        )
    elif delta < -25.0:
        suggested = min(20, current_trim + 5)
        score = max(50, round(100 - abs(delta) * 0.7))
        label = "Qualité Dégradée (Humide / Faible PCI)"
        color = "rose"
        action_needed = (suggested != current_trim)
        explanation = (
            f"Rendement thermique en retrait ({delta}°C vs cible P{power_actual}, moyenne 15 min à puissance constante). Granulé potentiellement humide (>10%) ou poussiéreux. "
            f"Conseil IA : augmenter le tarage à {suggested:+d}% pour compenser la perte calorifique ou vérifier la propreté du creuset."
        )
    else:
        suggested = current_trim
        score = min(100, round(96 - abs(delta) * 0.3))
        label = "Optimale (Standard DIN+ / ENplus)"
        color = "emerald"
        action_needed = False
        explanation = (
            f"Combustion équilibrée et propre (écart thermique moyen de {delta:+0.1f}°C en P{power_actual} sur 15 min stables). "
            f"Pouvoir calorifique et tirage conformes aux spécifications Nobis. Aucun ajustement de tarage requis."
        )

    return {
        "status": "active",
        "quality_score": score,
        "quality_label": label,
        "quality_color": color,
        "target_flame_temp": round(target_flame, 1),
        "flame_delta": instant_delta,
        "flame_delta_avg": delta,
        "stable_minutes": 15,
        "current_trim": current_trim,
        "suggested_trim": suggested,
        "explanation": explanation,
        "action_needed": action_needed
    }


def evaluate_exhaust_quality(
    status_code: int,
    power_actual: int,
    depression_pa: float,
    exhaust_rpm: int,
    current_trim: int,
    dep_delta_avg: Optional[float] = None,
    stable_minutes: Optional[int] = None,
    power_level: Optional[int] = None
) -> Dict[str, Any]:
    """Évalue la qualité du tirage du conduit et de l'évacuation des fumées, et propose un ajustement de tarage extracteur par IA
    en se basant sur l'écart de dépression Pascal moyenné sur 15 minutes à consigne de puissance strictement constante."""
    if status_code != 5 or power_actual <= 0:
        trim_str = f"{current_trim:+d}%" if current_trim != 0 else "0%"
        if status_code == 5 and power_actual == 0:
            return {
                "status": "modulating",
                "quality_score": None,
                "quality_label": "Modulation P0 (Éco)",
                "quality_color": "sky",
                "target_depression_pa": None,
                "depression_pa": round(depression_pa, 1) if depression_pa else 0.0,
                "dep_delta": None,
                "dep_delta_avg": None,
                "stable_minutes": 0,
                "exhaust_rpm": exhaust_rpm,
                "current_trim": current_trim,
                "suggested_trim": current_trim,
                "explanation": (
                    f"Poêle en modulation P0 (Ralenti / Consigne atteinte). "
                    f"L'extracteur de fumées tourne au régime réduit de maintien ({exhaust_rpm} RPM). "
                    f"L'analyse aéraulique de calibrage requiert un régime nominal P1 à P7. "
                    f"Tarage extracteur en mémoire : {trim_str}."
                ),
                "action_needed": False
            }
        return {
            "status": "idle",
            "quality_score": None,
            "quality_label": "En attente tirage actif",
            "quality_color": "gray",
            "target_depression_pa": None,
            "depression_pa": round(depression_pa, 1) if depression_pa else 0.0,
            "dep_delta": None,
            "dep_delta_avg": None,
            "stable_minutes": 0,
            "exhaust_rpm": exhaust_rpm,
            "current_trim": current_trim,
            "suggested_trim": current_trim,
            "explanation": f"L'analyse aéraulique requiert que le poêle soit en phase active de travail (P1 à P7). Tarage extracteur fumées en mémoire : {trim_str}.",
            "action_needed": False
        }

    # Dépression théorique cible en Pascal pour la puissance demandée Nobis A10 (P1..P7)
    # P1: ~9.3 Pa, P4: ~11.5 Pa, P7: ~13.8 Pa
    target_dep = round(8.5 + (power_actual * 0.75), 1)
    instant_delta = round(depression_pa - target_dep, 1)

    # Si la consigne de puissance a varié dans l'intervalle de 15 minutes (lissage non stabilisé)
    if dep_delta_avg is None:
        mins_txt = f"{stable_minutes}/15 min" if stable_minutes else "en cours"
        p_txt = f"P{power_actual}"
        return {
            "status": "stabilizing",
            "quality_score": None,
            "quality_label": f"Stabilisation {p_txt} ({mins_txt})",
            "quality_color": "sky",
            "target_depression_pa": target_dep,
            "depression_pa": round(depression_pa, 1),
            "dep_delta": instant_delta,
            "dep_delta_avg": None,
            "stable_minutes": stable_minutes or 0,
            "exhaust_rpm": exhaust_rpm,
            "current_trim": current_trim,
            "suggested_trim": current_trim,
            "explanation": (
                f"Consigne {p_txt} active depuis {stable_minutes or 1} min. "
                f"Le lissage sur 15 minutes requiert que la consigne de puissance ne varie pas dans l'intervalle "
                f"pour stabiliser la dépression du conduit avant d'évaluer le tirage."
            ),
            "action_needed": False
        }

    delta = round(dep_delta_avg, 1)

    if delta < -1.8 or depression_pa < 7.5:
        suggested = min(30, current_trim + 5)
        score = max(50, round(100 - abs(delta) * 12.0))
        label = "Tirage Insuffisant (Pertes de charge)"
        color = "rose"
        action_needed = (suggested != current_trim)
        explanation = (
            f"Dépression dans le foyer inférieure à la consigne dynamique ({delta:+.1f} Pa vs cible {target_dep} Pa, moyenne 15 min à puissance constante). "
            f"Évacuation des fumées ralentie, risque de noircissement de la vitre ou d'encrassement. "
            f"Conseil IA : augmenter le tarage extracteur à {suggested:+d}% ou vérifier l'état du conduit/terminal coaxial."
        )
    elif delta > 2.2 or depression_pa > 15.0:
        suggested = max(-30, current_trim - 5)
        score = max(55, round(100 - abs(delta) * 10.0))
        label = "Tirage Excessif (Sur-tirage)"
        color = "amber"
        action_needed = (suggested != current_trim)
        explanation = (
            f"Dépression élevée dans le foyer ({delta:+.1f} Pa vs cible {target_dep} Pa, moyenne 15 min à puissance constante). "
            f"Sur-tirage naturel du conduit ou vent soutenu évacuant trop vite les calories vers l'extérieur. "
            f"Conseil IA : réduire le tarage extracteur à {suggested:+d}% pour optimiser le rendement thermique."
        )
    else:
        suggested = current_trim
        score = min(100, round(98 - abs(delta) * 5.0))
        label = "Aéraulique Optimale (Dépression Conforme)"
        color = "emerald"
        action_needed = False
        explanation = (
            f"Dépression et tirage parfaitement équilibrés ({depression_pa} Pa, écart moyen de {delta:+.1f} Pa en P{power_actual} sur 15 min stables). "
            f"Évacuation des fumées et apport comburant conformes aux spécifications Nobis. Aucun ajustement de tarage requis."
        )

    return {
        "status": "active",
        "quality_score": score,
        "quality_label": label,
        "quality_color": color,
        "target_depression_pa": target_dep,
        "depression_pa": round(depression_pa, 1),
        "dep_delta": instant_delta,
        "dep_delta_avg": delta,
        "stable_minutes": 15,
        "exhaust_rpm": exhaust_rpm,
        "current_trim": current_trim,
        "suggested_trim": suggested,
        "explanation": explanation,
        "action_needed": action_needed
    }


def enrich_telemetry_record(conn: Any, record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrichit un enregistrement de télémétrie avec les statistiques de consommation glissantes (7j / 30j),
    le diagnostic de stabilité de consigne et les évaluations thermiques/aérauliques IA.
    Centralise cette logique métier pour éliminer toute duplication (DRY).
    """
    ref_epoch = int(record.get("epoch") or int(datetime.now().timestamp()))
    stats = get_rolling_consumption_stats(conn, ref_epoch)
    record["conso_week_kg"] = stats["conso_week_kg"]
    record["conso_month_kg"] = stats["conso_month_kg"]

    p_state = int(record.get("status_code") or 0)
    p_power = int(record.get("power_actual") or record.get("power_level") or 0)
    p_cmd = int(record.get("power_level") or p_power)
    p_flame = float(record.get("temp_probe_k") or 0.0)
    p_fumes = float(record.get("temp_fumes") or 0.0)
    p_trim = int(record.get("trim_pellet_pct") or 0)
    p_delta_avg = float(record["flame_delta_avg"]) if record.get("flame_delta_avg") is not None else None

    _, stable_min, _ = check_power_stability(conn, ref_epoch, p_state, p_power, p_cmd, 900)
    pellet_quality = evaluate_pellet_quality(p_state, p_power, p_flame, p_fumes, p_trim, p_delta_avg, stable_min, p_cmd)
    record["pellet_quality"] = pellet_quality

    p_dep = float(record.get("depression_pa") or 0.0)
    p_rpm = int(record.get("exhaust_rpm") or 0)
    p_trim_exh = int(record.get("trim_exhaust_pct") or 0)
    p_dep_delta_avg = float(record["dep_delta_avg"]) if record.get("dep_delta_avg") is not None else None
    exhaust_quality = evaluate_exhaust_quality(p_state, p_power, p_dep, p_rpm, p_trim_exh, p_dep_delta_avg, stable_min, p_cmd)
    record["exhaust_quality"] = exhaust_quality

    return record


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
    async with _stove_io_lock:
        try:
            return await _collect_telemetry_unlocked(force_mode)
        except Exception as e:
            active_m = force_mode or get_active_mode()
            db_log("ERROR", "TELEMETRY", f"Échec inattendu cycle télémétrie : {str(e)}", active_m)
            raise


async def _collect_telemetry_unlocked(force_mode: Optional[str] = None) -> Dict[str, Any]:
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

    if comm_mode == "agua_cloud" and settings.get("agua_email") and settings.get("agua_password"):
        try:
            raw_buf = await agua_client.read_buffer(settings)
        except Exception as e:
            db_log("ERROR", "AGUA_CLOUD", f"Échec lecture buffer Agua IOT : {str(e)}", "agua_cloud")
    elif comm_mode == "local_gateway":
        gw_url = settings.get("local_gateway_url", "http://192.168.1.150")
        try:
            raw_buf = await local_gateway_client.read_buffer(gw_url)
        except Exception as e:
            db_log("ERROR", "LOCAL_GATEWAY", f"Échec lecture passerelle UART : {str(e)}", "local_gateway")

    if raw_buf:
        if prev.get("status_code") == -1:
            db_log("INFO", "COMMUNICATION", f"Communication avec le poêle rétablie avec succès ({comm_mode}).", comm_mode)

        state = int(raw_buf.get(8, 5))
        status_managed = int(raw_buf.get(232, 85))
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

        temp_amb = float(raw_buf.get(103, prev.get("temp_ambient") or 19.0))
        setpoint_amb = float(raw_buf.get(33122, r_val.get("temp_amb_setpoint", 19.0)))
        temp_fumes = float(raw_buf.get(2, prev.get("temp_fumes") or 185.0))
        temp_probe_k = float(raw_buf.get(0, prev.get("temp_probe_k") or 309.0))
        rpm = int(round(float(raw_buf.get(47, 0.0)) * 10.0))
        dep_pa = round(float(raw_buf.get(37, 0.0)) / 10.0, 1)

        p_cmd = int(raw_buf.get(33120, r_val.get("power_level", 4)))
        p_actual = int(raw_buf.get(11, p_cmd))
        fun_auto = int(raw_buf.get(33128, r_val.get("fun_auto_set", 1)))
        standby_en = int(raw_buf.get(33130, r_val.get("standby_set", 0)))
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

        if state in (2, 3, 4, 5, 6) and p_actual > 0:
            auger_s = round(1.0 + p_actual * 0.28, 2)
            duty_pct = round((auger_s / 4.0) * 100.0, 1)
            rate_kgh = round(0.60 + p_actual * 0.22, 2)
        else:
            auger_s = 0.0
            duty_pct = 0.0
            rate_kgh = 0.0

        consumed_kg = rate_kgh * dt_hours
        prev_hopper = float(prev.get("pellet_hopper_pct") or 78.0)
        prev_pellet_empty = int(prev.get("popup_pellet_empty") or 0)

        # Auto-recalibrage intelligent sur la sonde physique de réserve (fond de trémie)
        if popup_pellet_empty:
            if not prev_pellet_empty:
                # Recalibrage précis sur le seuil matériel (~3.6 kg = 12% d'une trémie de 30 kg)
                hopper = 12.0
                db_log("WARNING", "PELLET", "Sonde physique réserve granulés activée : recalibrage automatique de la jauge à 12% (~3.6 kg restant)", comm_mode)
            else:
                hopper = round(max(0.0, min(12.0, prev_hopper - (consumed_kg / HOPPER_CAPACITY_KG) * 100.0)), 1)
        else:
            if prev_pellet_empty:
                # La sonde de réserve était vide et vient d'être recouverte de granulés -> recharge complète de la trémie (100% / 30 kg)
                hopper = 100.0
                db_log("INFO", "PELLET", "Sonde physique réserve granulés réinitialisée (recharge détectée) : recalibrage automatique de la trémie à 100% (30 kg)", comm_mode)
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
        # de non-variation de la consigne de puissance (power_level et power_actual) sur l'intervalle de 15 min
        is_stable_15m = False
        stable_min = 0
        if state == 5 and p_actual > 0:
            target_flame = 430.0 + (p_actual * 40.0)
            instant_delta = round(temp_probe_k - target_flame, 1)
            target_dep = 8.5 + (p_actual * 0.75)
            instant_dep_delta = round(dep_pa - target_dep, 1)

            conn_prev = get_db(comm_mode)
            try:
                is_stable_15m, stable_min, stable_rows = check_power_stability(
                    conn_prev, int(now.timestamp()), state, p_actual, p_cmd, 900
                )
            finally:
                conn_prev.close()

            # Le lissage sur 15 min n'est validé QUE si la consigne de puissance n'a pas varié dans l'intervalle
            if is_stable_15m and stable_rows:
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
                if off in raw_buf:
                    conn.execute(
                        "UPDATE registers_catalog SET current_val = ?, updated_at = ? WHERE reg_key = ?",
                        (float(raw_buf[off]), now_str, rk)
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
            "temp_board_ntc": temp_probe_k,
            "exhaust_rpm": rpm,
            "exhaust_target_rpm": rpm,
            "depression_pa": dep_pa,
            "flame_light_idx": int(temp_probe_k),
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
            "auger_on_time_s": auger_s,
            "auger_cycle_s": 4.0,
            "auger_duty_pct": duty_pct,
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
        if prev.get("status_code") != -1:
            db_log("ERROR", "COMMUNICATION", f"Poêle injoignable ({comm_mode}) : rupture de communication ou absence de trame reçue.", comm_mode)
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
        cmd_on = int(r_val.get("status_code", 85))
        sp_amb = float(r_val.get("temp_amb_setpoint", 19.0))
        prev_amb = float(prev.get("temp_ambient") or 19.5)
        state = 5 if cmd_on == 85 else 0
        standby_en = int(r_val.get("standby_set", 0))
        p_cmd = int(r_val.get("power_level", 4))
        fun_pwf = int(r_val.get("fun_pwf_set", 0))
        p_actual = (8 if fun_pwf in (1, 2) else p_cmd) if state == 5 else 0
        temp_amb = round(prev_amb + (0.05 if state == 5 else -0.05), 1)
        temp_fumes = round(110.0 + p_actual * 18.0, 1) if state == 5 else 25.0
        temp_k = round(440.0 + p_actual * 42.0, 1) if state == 5 else 28.0
        rpm = int(1400 + p_actual * 160) if state == 5 else 0

        c_hr = int(r_val.get("clock_hour_set", now.hour))
        c_mn = int(r_val.get("clock_minute_set", now.minute))
        c_day = int(r_val.get("calendar_day_set", now.day))
        c_mon = int(r_val.get("calendar_month_set", now.month))
        c_yr = int(r_val.get("calendar_year_set", now.year - 2000)) + 2000
        c_wday = int(r_val.get("calendar_day_week_set", now.isoweekday()))
        days_fr = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
        wday_str = days_fr[c_wday - 1] if 1 <= c_wday <= 7 else "---"
        stove_clock_str = f"{wday_str} {c_hr:02d}:{c_mn:02d} {c_day:02d}/{c_mon:02d}/{c_yr}"

        rate_kgh = round(0.60 + p_actual * 0.22, 2) if state == 5 else 0.0
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
        # de non-variation de la consigne de puissance (power_level et power_actual) sur l'intervalle de 15 min
        is_stable_15m = False
        stable_min = 0
        if state == 5 and p_actual > 0:
            target_flame = 430.0 + (p_actual * 40.0)
            instant_delta = round(temp_k - target_flame, 1)
            sim_dep = round(8.5 + p_actual * 0.75 - 0.2, 1)
            target_dep = round(8.5 + p_actual * 0.75, 1)
            instant_dep_delta = round(sim_dep - target_dep, 1)

            conn_prev = get_db(comm_mode)
            try:
                is_stable_15m, stable_min, stable_rows = check_power_stability(
                    conn_prev, int(now.timestamp()), state, p_actual, p_cmd, 900
                )
            finally:
                conn_prev.close()

            # Le lissage sur 15 min n'est validé QUE si la consigne de puissance n'a pas varié dans l'intervalle
            if is_stable_15m and stable_rows:
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
            "temp_board_ntc": temp_k,
            "exhaust_rpm": rpm,
            "exhaust_target_rpm": rpm,
            "depression_pa": round(8.5 + p_actual * 0.75 - 0.2, 1) if state == 5 else 0.0,
            "flame_light_idx": int(temp_k),
            "glow_plug_on": 1 if state in (2, 3, 4) else 0,
            "power_level": p_cmd,
            "power_actual": p_actual,
            "fun_auto_set": int(r_val.get("fun_auto_set", 1)),
            "standby_enabled": standby_en,
            "fun_pwf_set": fun_pwf,
            "set_silece_set": int(r_val.get("set_silece_set", 0)),
            "fan_front_speed": int(r_val.get("fan_front_speed", 3)),
            "vent_front_actual": int(r_val.get("fan_front_speed", 3)),
            "duct_left_temp": float(r_val.get("duct_left_setpoint", 20.0)),
            "duct_left_setpoint": float(r_val.get("duct_left_setpoint", 20.0)),
            "duct_left_speed": int(r_val.get("duct_left_speed", 0)),
            "duct_left_actual": int(r_val.get("duct_left_speed", 0)),
            "duct_right_temp": float(r_val.get("duct_right_setpoint", 20.0)),
            "duct_right_setpoint": float(r_val.get("duct_right_setpoint", 20.0)),
            "duct_right_speed": int(r_val.get("duct_right_speed", 0)),
            "duct_right_actual": int(r_val.get("duct_right_speed", 0)),
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
            "auger_on_time_s": 1.8 if state == 5 else 0.0,
            "auger_cycle_s": 4.0,
            "auger_duty_pct": 45.0 if state == 5 else 0.0,
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
            "raw_buffer_json": prev.get("raw_buffer_json", "{}")
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
        "active_db_file": os.path.basename(MODE_DB_PATHS[comm_mode]),
        "latest": record,
        "settings": get_public_settings_dict(),
        "registers": get_registers_dict(comm_mode),
        "consumption_stats": consumption_stats,
        "pellet_quality": record.get("pellet_quality"),
        "exhaust_quality": record.get("exhaust_quality")
    })
    await matter_manager.sync_telemetry(record)
    return record


# ==============================================================================
# EXÉCUTION D'ÉCRITURES UNITAIRES ET GROUPÉES (BATCH) SUR LES REGISTRES
# ==============================================================================
async def execute_registers_batch_write(
    commands: List[Dict[str, Any]],
    skip_poll: bool = False
) -> Dict[str, Any]:
    if not commands:
        raise HTTPException(status_code=400, detail="Liste de commandes vide")

    async with _stove_io_lock:
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

            normalized_cmds.append({
                "reg_key": rk,
                "offset": offset,
                "label": label,
                "old_val": old_val,
                "new_val": val,
                "in_catalog": rk in regs
            })
            cloud_items_spec.append((offset, val, rk))

        status_str = "SUCCESS"
        details_str = f"Écriture {comm_mode}"

        if comm_mode == "agua_cloud" and cloud_items_spec:
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
                conn.execute("""
                    INSERT INTO command_audit (timestamp, reg_key, offset, label, old_value, new_value, comm_mode, status, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (now_str, c["reg_key"], c["offset"], c["label"], c["old_val"], c["new_val"], comm_mode, status_str, details_str))
            conn.commit()
        finally:
            conn.close()

        for c in normalized_cmds:
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


async def execute_register_write(reg_key: str, new_value: float, custom_offset: Optional[int] = None, skip_poll: bool = False) -> Dict[str, Any]:
    return await execute_registers_batch_write(
        [{"reg_key": reg_key, "value": new_value, "offset": custom_offset}],
        skip_poll=skip_poll
    )
