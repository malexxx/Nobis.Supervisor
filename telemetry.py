import os
import sys
import json
import time
import asyncio
import statistics
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
    get_sys_db,
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
    window_seconds: int = 1800
) -> Tuple[bool, int, List[Any], bool]:
    """Vérifie si la combustion est en régime établi sur l'intervalle spécifié (défaut 30 minutes / 1800s) :
    1. Consigne de puissance et puissance réelle restées strictement constantes sans variation.
    2. Sécurité ventilation : vérifie que les ventilateurs d'air chaud (vent_front_actual offset 16,
       duct_right_actual offset 31, duct_left_actual offset 32) ne sont pas tous coupés (au moins un ventilateur actif).
    Retourne (is_stable_window, stable_minutes, rows_in_window, fans_active)."""
    if status_code != 5 or power_actual <= 0:
        return (False, 0, [], False)

    cutoff = now_epoch - window_seconds

    # 1. Chercher s'il y a eu une variation de consigne/puissance ou de statut dans les 30 dernières minutes
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
        # Aucune variation dans les 30 min : chercher le point de départ de ce palier stable
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

    window_mins = max(1, window_seconds // 60)
    stable_minutes = min(window_mins, max(1, int(stable_seconds // 60)))

    # Récupérer les lignes enregistrées strictement à ce palier de puissance avec les ventilateurs
    query_since = cutoff if (last_var_epoch is None and stable_seconds >= (window_seconds - 120)) else stable_start
    stable_rows = conn.execute("""
        SELECT epoch, temp_probe_k, depression_pa,
               COALESCE(vent_front_actual, 0),
               COALESCE(duct_right_actual, 0),
               COALESCE(duct_left_actual, 0)
        FROM telemetry
        WHERE status_code = 5 
          AND power_actual = ? 
          AND power_level = ?
          AND epoch >= ? AND epoch <= ?
        ORDER BY epoch ASC
    """, (power_actual, power_level, query_since, now_epoch)).fetchall()

    # Sécurité ventilation : vérifier si les ventilateurs de convection/canalisations sont coupés
    fans_active = True
    if stable_rows:
        latest_r = stable_rows[-1]
        vf = int(latest_r[3] or 0)
        vdr = int(latest_r[4] or 0)
        vdl = int(latest_r[5] or 0)
        # Si TOUS les ventilateurs de convection sont à 0 -> ventilation coupée (convection naturelle)
        if vf == 0 and vdr == 0 and vdl == 0:
            fans_active = False

    # Stable 30 min sans variation : aucune variation dans la fenêtre et historique d'au moins 28 min (window_seconds - 120s)
    # ET sécurité ventilation active
    is_power_stable = (last_var_epoch is None) and (stable_seconds >= (window_seconds - 120))
    is_stable_window = is_power_stable and fans_active

    return (is_stable_window, stable_minutes, stable_rows, fans_active)


def evaluate_pellet_quality(
    status_code: int,
    power_actual: int,
    temp_probe_k: float,
    temp_fumes: float,
    current_trim: int,
    flame_delta_avg: Optional[float] = None,
    stable_minutes: Optional[int] = None,
    power_level: Optional[int] = None,
    fans_active: bool = True
) -> Dict[str, Any]:
    """Évalue la qualité du combustible granulé et propose un ajustement de tarage par IA
    en se basant sur l'écart thermique moyenné sur 30 minutes à consigne de puissance et ventilation constantes."""
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

    # Sécurité ventilation : si tous les ventilateurs de convection sont coupés (convection naturelle / silence)
    if not fans_active:
        return {
            "status": "natural_convection",
            "quality_score": None,
            "quality_label": "Ventilation coupée (Convection)",
            "quality_color": "amber",
            "target_flame_temp": round(target_flame, 1),
            "flame_delta": instant_delta,
            "flame_delta_avg": None,
            "stable_minutes": stable_minutes or 0,
            "current_trim": current_trim,
            "suggested_trim": current_trim,
            "explanation": (
                f"Ventilateurs de convection frontale et canalisations coupés (offsets 16, 31, 32 à 0). "
                f"En convection naturelle, l'absence d'échange forcé élève la température du foyer (sonde K {temp_probe_k:g}°C vs cible {target_flame:g}°C) sans refléter la qualité réelle du combustible. "
                f"L'ajustement automatique du tarage est verrouillé par sécurité."
            ),
            "action_needed": False
        }

    # Si la consigne de puissance a varié dans l'intervalle de 30 minutes (lissage non stabilisé)
    if flame_delta_avg is None:
        mins_txt = f"{stable_minutes}/30 min" if stable_minutes else "en cours"
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
                f"Le lissage sur 30 minutes requiert une consigne de puissance et une ventilation stables "
                f"pour atteindre le régime thermique établi avant d'évaluer la qualité du granulé."
            ),
            "action_needed": False
        }

    delta = round(flame_delta_avg, 1)

    if delta > 30.0:
        suggested = max(-5, current_trim - (2 if delta > 50.0 else 1))
        score = max(60, round(100 - abs(delta) * 0.5))
        label = "Haute Énergie (Très sec / PCI élevé)"
        color = "amber"
        action_needed = (suggested != current_trim)
        explanation = (
            f"Combustion vive (+{delta:+.1f}°C vs cible P{power_actual}, moyenne 30 min stables). Granulé très dense et sec (fort pouvoir calorifique). "
            f"Conseil : reporter le tarage granulés à {suggested:+d}% sur votre radiocommande (MENU ▸ PROGRAMMATION UTILISATEUR ▸ MÉLANGE G/A) pour préserver le creuset."
        )
    elif delta < -25.0:
        suggested = min(5, current_trim + (2 if delta < -45.0 else 1))
        score = max(50, round(100 - abs(delta) * 0.7))
        label = "Qualité Dégradée (Humide / Faible PCI)"
        color = "rose"
        action_needed = (suggested != current_trim)
        explanation = (
            f"Rendement thermique en retrait ({delta:+.1f}°C vs cible P{power_actual}, moyenne 30 min stables). Granulé potentiellement humide (>10%) ou poussiéreux. "
            f"Conseil : reporter le tarage granulés à {suggested:+d}% sur votre radiocommande (MENU ▸ PROGRAMMATION UTILISATEUR ▸ MÉLANGE G/A) pour compenser la perte calorifique."
        )
    else:
        suggested = current_trim
        score = min(100, round(96 - abs(delta) * 0.3))
        label = "Optimale (Standard DIN+ / ENplus)"
        color = "emerald"
        action_needed = False
        explanation = (
            f"Combustion équilibrée et propre (écart thermique moyen de {delta:+.1f}°C en P{power_actual} sur 30 min stables). "
            f"Pouvoir calorifique conforme aux spécifications Nobis. Aucun ajustement de tarage requis."
        )

    return {
        "status": "active",
        "quality_score": score,
        "quality_label": label,
        "quality_color": color,
        "target_flame_temp": round(target_flame, 1),
        "flame_delta": instant_delta,
        "flame_delta_avg": delta,
        "stable_minutes": stable_minutes or 30,
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
    power_level: Optional[int] = None,
    fans_active: bool = True
) -> Dict[str, Any]:
    """Évalue la qualité du tirage du conduit et de l'évacuation des fumées, et propose un ajustement de tarage extracteur par IA
    en se basant sur l'écart de dépression dynamique Pascal (capteur d'entrée offset 37) moyenné sur 30 minutes."""
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

    # Dépression théorique cible en Pascal pour l'air comburant dynamique à l'entrée Nobis A10 (P1..P7)
    # Mesuré par le capteur dynamique embarqué (OFFSET 37 pascal_get / 10). Plage nominale : 2.0 à 4.5 Pa
    # P1: ~2.3 Pa, P4: ~3.0 Pa, P7: ~3.8 Pa
    target_dep = round(2.0 + (power_actual * 0.25), 1)
    instant_delta = round(depression_pa - target_dep, 1)

    # Si la consigne de puissance a varié dans l'intervalle de 30 minutes (lissage non stabilisé)
    if dep_delta_avg is None:
        mins_txt = f"{stable_minutes}/30 min" if stable_minutes else "en cours"
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
                f"Le lissage sur 30 minutes requiert une consigne de puissance et une ventilation stables "
                f"pour stabiliser le débit d'air comburant mesuré par le capteur embarqué avant d'évaluer le tirage."
            ),
            "action_needed": False
        }

    delta = round(dep_delta_avg, 1)

    if delta < -1.0 or depression_pa < 1.2:
        suggested = min(5, current_trim + (2 if delta < -2.0 else 1))
        score = max(50, round(100 - abs(delta) * 20.0))
        label = "Débit d'Air Insuffisant (Pertes de charge)"
        color = "rose"
        action_needed = (suggested != current_trim)
        explanation = (
            f"Dépression dynamique comburant inférieure à la consigne ({delta:+.1f} Pa vs cible {target_dep} Pa, moyenne 30 min stables). "
            f"Mesure capteur embarqué (offset 37) à {depression_pa:g} Pa (seuil d'alerte AL11 à 1,0 Pa). Évacuation ralentie ou pertes de charge. "
            f"Conseil : reporter le tarage extracteur à {suggested:+d}% sur votre radiocommande (MENU ▸ PROGRAMMATION UTILISATEUR ▸ MÉLANGE G/A) ou vérifier l'encrassement du conduit/coaxial."
        )
    elif delta > 1.5 or depression_pa > 6.0:
        suggested = max(-5, current_trim - (2 if delta > 2.5 else 1))
        score = max(55, round(100 - abs(delta) * 15.0))
        label = "Débit d'Air Élevé (Sur-tirage dynamique)"
        color = "amber"
        action_needed = (suggested != current_trim)
        explanation = (
            f"Dépression dynamique comburant élevée ({delta:+.1f} Pa vs cible {target_dep} Pa, moyenne 30 min stables). "
            f"Sur-tirage dynamique ou vent soutenu évacuant trop vite les calories ({depression_pa:g} Pa). "
            f"Conseil : réduire le tarage extracteur à {suggested:+d}% sur votre radiocommande (MENU ▸ PROGRAMMATION UTILISATEUR ▸ MÉLANGE G/A) pour optimiser le rendement."
        )
    else:
        suggested = current_trim
        score = min(100, round(98 - abs(delta) * 10.0))
        label = "Aéraulique Optimale (Débit Conforme)"
        color = "emerald"
        action_needed = False
        explanation = (
            f"Dépression dynamique et débit comburant parfaitement équilibrés ({depression_pa:g} Pa, écart moyen de {delta:+.1f} Pa en P{power_actual} sur 30 min stables). "
            f"Écoulement d'air comburant (capteur embarqué offset 37) conforme aux spécifications Nobis. Aucun ajustement de tarage requis."
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
        "stable_minutes": stable_minutes or 30,
        "exhaust_rpm": exhaust_rpm,
        "current_trim": current_trim,
        "suggested_trim": suggested,
        "explanation": explanation,
        "action_needed": action_needed
    }


# ==============================================================================
# ALGORITHME D'ÉVALUATION PAR LOT NOBIS A10 C LIGHT STYLE COAX (MÉLANGE P/A)
# ==============================================================================
BATCH_MIN_EVAL_MINUTES: float = 180.0     # 3 heures cumulées en TRAVAIL stabilisé avant proposition
BATCH_MIN_CYCLES: int = 3                 # Minimum 3 cycles d'allumage distincts
STABILIZATION_WARMUP_S: int = 1200        # Exclusion des 20 premières minutes en TRAVAIL (inertie fonte/vermiculite)
NOBIS_TRIM_MIN: int = -5                  # Limite basse officielle menu MÉLANGE P/A (-5%)
NOBIS_TRIM_MAX: int = 5                   # Limite haute officielle menu MÉLANGE P/A (+5%)


def clamp_nobis_trim(val: float) -> int:
    """Force toute valeur de tarage dans la plage officielle Nobis [-5%, +5%]."""
    return max(NOBIS_TRIM_MIN, min(NOBIS_TRIM_MAX, int(round(val))))


def get_pellet_batch_state() -> Dict[str, Any]:
    """Récupère l'état du lot de granulés en cours depuis la table settings (nobis_a10_system.db)."""
    conn = get_sys_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'pellet_batch_state'").fetchone()
        if row and row["value"]:
            try:
                return json.loads(row["value"])
            except Exception:
                pass
    finally:
        conn.close()
    return {
        "raz_epoch": 0,
        "raz_timestamp": "Initialisation",
        "pellet_brand": "Lot Standard DINplus / ENplus A1",
        "baseline_rpm_per_pa": None,
        "last_applied_epoch": 0
    }


def save_pellet_batch_state(state: Dict[str, Any]) -> None:
    """Sauvegarde l'état du lot de granulés dans settings."""
    conn = get_sys_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('pellet_batch_state', ?)",
            (json.dumps(state),)
        )
        conn.commit()
    finally:
        conn.close()


def _percentile(sorted_vals: List[float], pct: float) -> float:
    """Calcule un centile (ex: P90 avec pct=0.90) sans dépendance externe."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * pct
    f = int(k)
    c = min(len(sorted_vals) - 1, f + 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def evaluate_batch_combustion_and_trim(conn: Any, current_record: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Arbre de décision différé pour le Nobis A10 C Light Style Coax :
    - Filtre les trames synthétiques (id <= 27) et hors ligne (status_code == -1).
    - Ne conserve que la phase TRAVAIL (OFFSET_8 == 5), sans alarme (OFFSET_34 == 0) ni pop-up (OFFSET_115 == 0).
    - Ignore les 20 premières minutes de chaque cycle de TRAVAIL (inertie fonte/vermiculite et préchauffage coaxial).
    - Attend 180 minutes cumulées et 3 cycles distincts depuis le dernier RAZ (ou dernier ajustement) avant de proposer un réglage.
    - Retourne deux dictionnaires compatibles avec l'UI existante : (pellet_quality, exhaust_quality).
    """
    batch_state = get_pellet_batch_state()
    eval_start_epoch = max(
        int(batch_state.get("raz_epoch") or 0),
        int(batch_state.get("last_applied_epoch") or 0)
    )
    brand_name = str(batch_state.get("pellet_brand") or "Lot Standard DINplus / ENplus A1")
    baseline_rpm_pa = batch_state.get("baseline_rpm_per_pa")

    cur_p_trim = clamp_nobis_trim(current_record.get("trim_pellet_pct") or 0)
    cur_e_trim = clamp_nobis_trim(current_record.get("trim_exhaust_pct") or 0)
    cur_flame = float(current_record.get("temp_probe_k") or 0.0)
    cur_dep = float(current_record.get("depression_pa") or 0.0)
    cur_rpm = int(current_record.get("exhaust_rpm") or 0)

    # 1. Extraction des relevés réels depuis le dernier RAZ
    rows = conn.execute("""
        SELECT id, epoch, status_code, alarm_code, popup_flags,
               temp_fumes, temp_probe_k, exhaust_rpm, depression_pa,
               power_actual, vent_front_actual, duct_left_actual, duct_right_actual,
               raw_buffer_json
        FROM telemetry
        WHERE epoch >= ?
          AND (id > 27 OR comm_mode = 'simulation')
          AND status_code != -1
          AND (comm_mode = 'simulation' OR (raw_buffer_json IS NOT NULL AND raw_buffer_json != '{}'))
        ORDER BY epoch ASC
    """, (eval_start_epoch,)).fetchall()

    # 2. Segmentation en cycles de TRAVAIL et filtrage des 20 premières minutes (1200s)
    stabilized_samples: List[Dict[str, Any]] = []
    cycle_start_epoch: Optional[int] = None
    prev_epoch: Optional[int] = None
    valid_cycles_count = 0
    cycle_counted = False
    accumulated_stable_s = 0.0

    for r in rows:
        st = int(r["status_code"] or 0)
        ep = int(r["epoch"] or 0)
        if st == 5:
            if cycle_start_epoch is None:
                cycle_start_epoch = ep
                prev_epoch = ep
                cycle_counted = False

            dt = max(0, min(300, ep - (prev_epoch or ep)))
            prev_epoch = ep

            p_act = int(r["power_actual"] or 0)
            al_code = int(r["alarm_code"] or 0)
            pop_flags = int(r["popup_flags"] or 0)

            if (ep - cycle_start_epoch) >= STABILIZATION_WARMUP_S and p_act >= 1 and al_code == 0 and pop_flags == 0:
                if not cycle_counted:
                    valid_cycles_count += 1
                    cycle_counted = True
                accumulated_stable_s += dt

                t_fumes = float(r["temp_fumes"] or 0.0)
                t_flame = float(r["temp_probe_k"] or 0.0)
                dep_pa = float(r["depression_pa"] or 0.0)
                rpm = float(r["exhaust_rpm"] or 0.0)
                vent_sum = int(r["vent_front_actual"] or 0) + int(r["duct_left_actual"] or 0) + int(r["duct_right_actual"] or 0)

                if t_fumes > 0.0 and dep_pa > 0.0:
                    stabilized_samples.append({
                        "power_actual": p_act,
                        "temp_fumes": t_fumes,
                        "temp_probe_k": t_flame,
                        "depression_pa": dep_pa,
                        "exhaust_rpm": rpm,
                        "vent_active": vent_sum > 0,
                        "ratio_tf": t_flame / t_fumes,
                        "rpm_per_pa": rpm / dep_pa
                    })
        else:
            cycle_start_epoch = None
            prev_epoch = None
            cycle_counted = False

    stable_minutes = round(accumulated_stable_s / 60.0, 1)

    # 3. Enregistrement automatique de la ligne de base aéraulique après RAZ (sur les 30 premiers points propres)
    if baseline_rpm_pa is None and len(stabilized_samples) >= 30:
        baseline_rpm_pa = round(float(statistics.median([s["rpm_per_pa"] for s in stabilized_samples[:30]])), 1)
        batch_state["baseline_rpm_per_pa"] = baseline_rpm_pa
        save_pellet_batch_state(batch_state)

    # 4. Tant que le quota d'observation (180 min et 3 cycles) n'est pas atteint -> Mode Collecte Silencieuse
    if stable_minutes < BATCH_MIN_EVAL_MINUTES or valid_cycles_count < BATCH_MIN_CYCLES or len(stabilized_samples) < 20:
        msg_wait = (
            f"Lot « {brand_name} » (RAZ : {batch_state.get('raz_timestamp')}) — "
            f"Collecte différée en cours : {stable_minutes}/{int(BATCH_MIN_EVAL_MINUTES)} min stabilisées "
            f"(après 20 min de chauffe) et {valid_cycles_count}/{BATCH_MIN_CYCLES} cycles. "
            f"Aucune modification de tarage avant la fin de l'évaluation."
        )
        pellet_q = {
            "status": "stabilizing",
            "quality_score": None,
            "quality_label": f"Évaluation Lot ({stable_minutes:.0f}/{int(BATCH_MIN_EVAL_MINUTES)}m • {valid_cycles_count}/{BATCH_MIN_CYCLES} cyc)",
            "quality_color": "sky",
            "target_flame_temp": 500.0,
            "flame_delta": 0.0,
            "flame_delta_avg": None,
            "stable_minutes": int(stable_minutes),
            "current_trim": cur_p_trim,
            "suggested_trim": cur_p_trim,
            "explanation": msg_wait,
            "action_needed": False,
            "pellet_brand": brand_name
        }
        exhaust_q = {
            "status": "stabilizing",
            "quality_score": None,
            "quality_label": f"Observation Tirage ({stable_minutes:.0f}/{int(BATCH_MIN_EVAL_MINUTES)}m)",
            "quality_color": "sky",
            "target_depression_pa": 3.0,
            "depression_pa": round(cur_dep, 1),
            "dep_delta": 0.0,
            "dep_delta_avg": None,
            "stable_minutes": int(stable_minutes),
            "exhaust_rpm": cur_rpm,
            "current_trim": cur_e_trim,
            "suggested_trim": cur_e_trim,
            "explanation": msg_wait,
            "action_needed": False
        }
        return pellet_q, exhaust_q

    # 5. Calcul des indicateurs statistiques robustes (Médiane & Centile 90) sur la fenêtre complète
    fumes_sorted = sorted(s["temp_fumes"] for s in stabilized_samples)
    flame_sorted = sorted(s["temp_probe_k"] for s in stabilized_samples)
    dep_sorted = sorted(s["depression_pa"] for s in stabilized_samples)
    rpm_pa_sorted = sorted(s["rpm_per_pa"] for s in stabilized_samples)
    ratio_tf_sorted = sorted(s["ratio_tf"] for s in stabilized_samples)

    med_fumes = round(float(statistics.median(fumes_sorted)), 1)
    p90_fumes = round(_percentile(fumes_sorted, 0.90), 1)
    med_flame = round(float(statistics.median(flame_sorted)), 1)
    p90_flame = round(_percentile(flame_sorted, 0.90), 1)
    med_dep = round(float(statistics.median(dep_sorted)), 2)
    med_rpm_pa = round(float(statistics.median(rpm_pa_sorted)), 1)
    med_ratio_tf = round(float(statistics.median(ratio_tf_sorted)), 2)

    unvent_high_pwr = [s for s in stabilized_samples if (not s["vent_active"]) and s["power_actual"] >= 3]
    p7_samples = [s for s in stabilized_samples if s["power_actual"] >= 6]
    low_p_samples = [s for s in stabilized_samples if s["power_actual"] <= 2]

    sug_p = cur_p_trim
    sug_e = cur_e_trim
    p_label = "Combustion Optimale (ENplus A1)"
    e_label = "Aéraulique Coaxiale Optimale"
    p_color = "emerald"
    e_color = "emerald"
    p_action = False
    e_action = False

    # PRIORITÉ 0 : VETO SÉCURITÉ VENTILATION (Puissance >= P3 sans soufflerie active -> Surchauffe fumées > 170°C)
    if len(unvent_high_pwr) >= 10 and statistics.median([s["temp_fumes"] for s in unvent_high_pwr]) > 170.0:
        med_unvent = round(float(statistics.median([s["temp_fumes"] for s in unvent_high_pwr])), 1)
        p_label = "VETO : Ventilation Coupée (Surchauffe)"
        e_label = "VETO : Défaut Échange Thermique"
        p_color = "rose"
        e_color = "rose"
        p_exp = (
            f"VETO SÉCURITÉ ({len(unvent_high_pwr)} pts à P3..P8 sans ventilation) : les fumées montent à {med_unvent}°C "
            f"(max certifié : 168°C). Ne modifiez pas le tarage granulés : réglez la ventilation frontale et les canalisations sur PS (Auto)."
        )
        e_exp = (
            f"La chaleur n'est pas évacuée de l'échangeur car les ventilateurs sont à 0 (OFF). "
            f"Activez le mode PS (offset 33132/33134/33136 = 8) et ramenez tout tarage excessif entre -5% et +5%."
        )

    # PRIORITÉ 1 : VETO MAINTENANCE AÉRAULIQUE (Encrassement brasero / coupe-flamme / conduit coaxial)
    elif med_dep < 2.0 or (baseline_rpm_pa and med_rpm_pa > baseline_rpm_pa * 1.15):
        p_label = "VETO : Entretien Foyer Requis"
        e_label = "Alerte Encrassement / Coaxial (< 2 Pa)"
        p_color = "rose"
        e_color = "rose"
        p_exp = (
            f"VETO MAINTENANCE : La dépression médiane ({med_dep} Pa) ou l'effort de l'extracteur ({med_rpm_pa} RPM/Pa "
            f"vs réf. {baseline_rpm_pa} RPM/Pa) signale un encrassement. Tarage granulés verrouillé."
        )
        e_exp = (
            f"Interdiction d'augmenter PRISE D'AIR pour masquer un colmatage (risque Alarme 11/13). "
            f"À froid et débranché : aspirez le brasero, le compartiment derrière le coupe-flamme et vérifiez le conduit coaxial Ø 80/130."
        )

    # PRIORITÉ 2 : SURCHARGE THERMIQUE / GRANULÉS DE PETIT CALIBRE (< 2 cm)
    elif (p7_samples and statistics.median([s["temp_fumes"] for s in p7_samples]) > 168.0) or p90_flame > 630.0:
        sug_p = clamp_nobis_trim(cur_p_trim - 2)
        sug_e = clamp_nobis_trim(cur_e_trim + 2)
        p_action = (sug_p != cur_p_trim)
        e_action = (sug_e != cur_e_trim)
        p_label = "Surcharge / Petit Calibre (< 2 cm)"
        e_label = "Manque d'O₂ Comburant"
        p_color = "amber"
        e_color = "amber"
        p_exp = (
            f"Sur {stable_minutes:.0f} min ({len(stabilized_samples)} pts), P90 Flamme = {p90_flame}°C (> 630°C) et P90 Fumées = {p90_fumes}°C. "
            f"Granulés denses ou courts (< 2 cm) : selon la notice Nobis (Mélange P/A), réduisez PELLET à {sug_p:+d}%."
        )
        e_exp = (
            f"Pour oxygéner ce chargement dense et stabiliser les fumées sous 168°C, "
            f"augmentez PRISE D'AIR à {sug_e:+d}% dans le menu MÉLANGE P/A (plage -5% à +5%)."
        )

    # PRIORITÉ 3 : EXCÈS D'ASPIRATION / SUR-TIRAGE (Calories perdues dans les fumées)
    elif med_fumes > 160.0 and med_ratio_tf < 2.7 and med_dep > 3.8:
        sug_p = cur_p_trim
        sug_e = clamp_nobis_trim(cur_e_trim - 2)
        e_action = (sug_e != cur_e_trim)
        p_label = "Charge Granulés Correcte"
        e_label = "Excès d'Aspiration (Pertes Fumées)"
        p_color = "emerald"
        e_color = "amber"
        p_exp = f"Ratio Flamme/Fumées bas ({med_ratio_tf}) avec fumées à {med_fumes}°C : le dosage granulés ({cur_p_trim:+d}%) est correct."
        e_exp = (
            f"Dépression soutenue ({med_dep} Pa) évacuant trop vite les gaz chauds dans le conduit coaxial. "
            f"Proposition : abaisser PRISE D'AIR à {sug_e:+d}%."
        )

    # PRIORITÉ 4 : GRANULÉS LONGS OU HUMIDES / RISQUE DE CONDENSATION (< 105°C en P1-P2)
    elif low_p_samples and statistics.median([s["temp_fumes"] for s in low_p_samples]) < 105.0 and med_flame < 330.0:
        sug_p = clamp_nobis_trim(cur_p_trim + 2)
        sug_e = cur_e_trim
        p_action = (sug_p != cur_p_trim)
        p_label = "Faible PCI / Risque Condensation"
        e_label = "Aspiration Nominale"
        p_color = "amber"
        e_color = "emerald"
        p_exp = (
            f"Température des fumées proche du minimum certifié (101°C) et flamme basse ({med_flame}°C). "
            f"Proposition : augmenter PELLET à {sug_p:+d}% et activer la fonction ANTICONDENSATION."
        )
        e_exp = f"Tirage coaxial stable (médiane {med_dep} Pa). Conservez PRISE D'AIR à {cur_e_trim:+d}%."

    # PRIORITÉ 5 : COMBUSTION NOMINALE OPTIMALE
    else:
        p_exp = (
            f"Lot « {brand_name} » évalué sur {stable_minutes:.0f} min ({valid_cycles_count} cycles) : "
            f"Fumées médiane = {med_fumes}°C (plage certifiée 101–168°C), Flamme médiane = {med_flame}°C, Ratio = {med_ratio_tf}. "
            f"Réglage PELLET ({cur_p_trim:+d}%) optimal."
        )
        e_exp = (
            f"Équilibre aéraulique coaxial conforme : Dépression médiane = {med_dep} Pa ({med_rpm_pa} RPM/Pa). "
            f"Réglage PRISE D'AIR ({cur_e_trim:+d}%) optimal."
        )

    pellet_quality = {
        "status": "active",
        "quality_score": 95 if p_color == "emerald" else (75 if p_color == "amber" else 55),
        "quality_label": p_label,
        "quality_color": p_color,
        "target_flame_temp": round(med_fumes * 3.2, 1),
        "flame_delta": round(cur_flame - med_flame, 1),
        "flame_delta_avg": round(med_fumes - 150.0, 1),
        "stable_minutes": int(stable_minutes),
        "current_trim": cur_p_trim,
        "suggested_trim": sug_p,
        "explanation": p_exp,
        "action_needed": p_action,
        "pellet_brand": brand_name
    }

    exhaust_quality = {
        "status": "active",
        "quality_score": 95 if e_color == "emerald" else (75 if e_color == "amber" else 55),
        "quality_label": e_label,
        "quality_color": e_color,
        "target_depression_pa": 3.0,
        "depression_pa": round(cur_dep, 1),
        "dep_delta": round(cur_dep - med_dep, 1),
        "dep_delta_avg": round(med_dep - 3.0, 1),
        "stable_minutes": int(stable_minutes),
        "exhaust_rpm": cur_rpm,
        "current_trim": cur_e_trim,
        "suggested_trim": sug_e,
        "explanation": e_exp,
        "action_needed": e_action
    }

    return pellet_quality, exhaust_quality


def enrich_telemetry_record(conn: Any, record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrichit un enregistrement de télémétrie avec les statistiques de consommation glissantes (7j / 30j),
    le diagnostic de stabilité de consigne et les évaluations thermiques/aérauliques IA par lot (Mélange P/A).
    Centralise cette logique métier pour éliminer toute duplication (DRY).
    """
    ref_epoch = int(record.get("epoch") or int(datetime.now().timestamp()))
    stats = get_rolling_consumption_stats(conn, ref_epoch)
    record["conso_week_kg"] = stats["conso_week_kg"]
    record["conso_month_kg"] = stats["conso_month_kg"]

    pellet_q, exhaust_q = evaluate_batch_combustion_and_trim(conn, record)
    record["pellet_quality"] = pellet_q
    record["exhaust_quality"] = exhaust_q
    record["weather_details"] = weather_service.last_weather

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

        temp_amb = float(raw_buf.get(103, prev.get("temp_ambient") or 19.0))
        setpoint_amb = float(raw_buf.get(33122, r_val.get("temp_amb_setpoint", 19.0)))
        temp_fumes = float(raw_buf.get(2, prev.get("temp_fumes") or 25.0))
        temp_probe_k = float(raw_buf.get(0, prev.get("temp_probe_k") or 25.0))
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

        # Débit théorique de granulés estimé selon la consigne de puissance Nobis A10 (OFFSET_11: P1..P8)
        # Formule fixe constructeur : P1 = 0,82 kg/h, P2 = 1,04 kg/h, P3 = 1,26 kg/h, P6 = 1,92 kg/h, P7 = 2,14 kg/h, P8 = 2,36 kg/h
        if state in (2, 3, 4, 5, 6) and p_actual > 0:
            rate_kgh = round(0.60 + p_actual * 0.22, 2)
        else:
            rate_kgh = 0.0

        # Le motoréducteur de vis sans fin Nobis A10 est un moteur Brushless 24V (VIS S. FIN / SET V.S.F. / AMP. V.S.F.)
        # et non un moteur alternatif cadencé ON/OFF. Ces registres d'encodeur/courant n'étant pas dans les 76 offsets
        # du buffer Agua IOT, auger_on_time_s, auger_cycle_s et auger_duty_pct sont à None (NULL) pour éviter toute valeur fictive.
        auger_s = None
        duty_pct = None
        auger_cycle = None

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
        standby_en = int(r_val.get("standby_set", 0))
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
            "31": duct_left_actual,
            "32": duct_right_actual,
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
        "active_db_file": os.path.basename(MODE_DB_PATHS[comm_mode]),
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

            is_trim = (offset in (70, 71) or rk in ("trim_pellet_pct", "trim_exhaust_pct"))
            normalized_cmds.append({
                "reg_key": rk,
                "offset": offset,
                "label": label,
                "old_val": old_val,
                "new_val": val,
                "in_catalog": rk in regs,
                "is_trim": is_trim
            })
            # En mode Agua Cloud, les offsets 70 et 71 (tarages MÉLANGE G/A) ne figurent pas dans la table Cloud Agua IOT.
            # On ne les injecte pas dans cloud_items_spec pour éviter le rejet/erreur 502 de l'API Agua Cloud.
            if not (comm_mode == "agua_cloud" and is_trim):
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
                details_str = "Mémorisé localement (à valider sur radiocommande: MENU ▸ PROG. UTILISATEUR ▸ MÉLANGE G/A)"
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
                if comm_mode == "agua_cloud" and c.get("is_trim"):
                    c_details = "Mémorisé localement (à valider sur radiocommande: MENU ▸ PROG. UTILISATEUR ▸ MÉLANGE G/A)"
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


async def execute_register_write(reg_key: str, new_value: float, custom_offset: Optional[int] = None, skip_poll: bool = False) -> Dict[str, Any]:
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

