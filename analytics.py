"""
Module d'analyses, d'évaluations thermiques et d'algorithmes de combustion.
Regroupe les calculs d'autonomie (forecast runway), stabilité de puissance,
qualité du granulé, tirage aéraulique (Mélange P/A) et enrichissement de télémétrie.
"""
from datetime import datetime
import json
import statistics
from typing import Any, Dict, List, Optional, Tuple

from clients import weather_service
from database import get_rolling_consumption_stats, get_sys_db

# ==============================================================================
# ALGORITHME D'ÉVALUATION PAR LOT NOBIS A10 C LIGHT STYLE COAX (MÉLANGE P/A)
# ==============================================================================
BATCH_MIN_EVAL_MINUTES: float = 180.0     # 3 heures cumulées en TRAVAIL stabilisé avant proposition
BATCH_MIN_CYCLES: int = 4                 # Minimum 3 cycles d'allumage distincts
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
        if vf == 0 and vdr == 0 and vdl == 0:
            fans_active = False

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

    target_flame = 430.0 + (power_actual * 40.0)
    instant_delta = round(temp_probe_k - target_flame, 1)

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

    target_dep = round(2.0 + (power_actual * 0.25), 1)
    instant_delta = round(depression_pa - target_dep, 1)

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

    stabilized_samples: List[Dict[str, Any]] = []
    cycle_start_epoch: Optional[int] = None
    prev_epoch: Optional[int] = None
    valid_cycles_count = 0
    cycle_counted = False
    accumulated_stable_s = 0.0

    for r in rows:
        ep = int(r["epoch"])
        st = int(r["status_code"] or 0)
        al = int(r["alarm_code"] or 0)
        pop = int(r["popup_flags"] or 0)

        is_break = (prev_epoch is not None and (ep - prev_epoch) > 300)
        if st != 5 or al != 0 or pop != 0 or is_break:
            cycle_start_epoch = None
            cycle_counted = False
            prev_epoch = ep
            continue

        if cycle_start_epoch is None:
            cycle_start_epoch = ep

        tf = float(r["temp_fumes"] or 0.0)
        tk = float(r["temp_probe_k"] or 0.0)
        rpm = int(r["exhaust_rpm"] or 0)
        dep = float(r["depression_pa"] or 0.0)
        pwr = int(r["power_actual"] or 0)
        vf = int(r["vent_front_actual"] or 0)
        vdl = int(r["duct_left_actual"] or 0)
        vdr = int(r["duct_right_actual"] or 0)
        is_vented = (vf > 0 or vdl > 0 or vdr > 0)

        dt = min(60.0, max(1.0, float(ep - prev_epoch))) if prev_epoch else 30.0
        prev_epoch = ep

        time_in_cycle = ep - cycle_start_epoch
        if time_in_cycle < STABILIZATION_WARMUP_S:
            continue

        if not cycle_counted:
            valid_cycles_count += 1
            cycle_counted = True

        accumulated_stable_s += dt

        if dep > 0.05 and rpm > 100:
            ratio_tf = tk / tf if tf > 10.0 else 3.0
            rpm_pa = rpm / dep
            stabilized_samples.append({
                "epoch": ep,
                "temp_fumes": tf,
                "temp_probe_k": tk,
                "depression_pa": dep,
                "exhaust_rpm": rpm,
                "rpm_per_pa": rpm_pa,
                "ratio_tf": ratio_tf,
                "power_actual": pwr,
                "vent_active": is_vented
            })

    stable_minutes = accumulated_stable_s / 60.0
    is_eval_ready = (stable_minutes >= BATCH_MIN_EVAL_MINUTES and valid_cycles_count >= BATCH_MIN_CYCLES)

    if not is_eval_ready or len(stabilized_samples) < 30:
        rem_mins = max(0, int(BATCH_MIN_EVAL_MINUTES - stable_minutes))
        rem_cyc = max(0, BATCH_MIN_CYCLES - valid_cycles_count)
        pellet_q = {
            "status": "accumulating",
            "quality_score": None,
            "quality_label": f"Acquisition Lot ({int(stable_minutes)}/{int(BATCH_MIN_EVAL_MINUTES)} min)",
            "quality_color": "sky",
            "target_flame_temp": None,
            "flame_delta": None,
            "flame_delta_avg": None,
            "stable_minutes": int(stable_minutes),
            "current_trim": cur_p_trim,
            "suggested_trim": cur_p_trim,
            "explanation": (
                f"Lot « {brand_name} » en observation : {int(stable_minutes)} min stabilisées sur {int(BATCH_MIN_EVAL_MINUTES)} min requises, "
                f"{valid_cycles_count}/{BATCH_MIN_CYCLES} cycles. Reste environ {rem_mins} min et {rem_cyc} cycle(s) pour calibrer le dosage PELLET."
            ),
            "action_needed": False,
            "pellet_brand": brand_name
        }
        exhaust_q = {
            "status": "accumulating",
            "quality_score": None,
            "quality_label": f"Acquisition Coaxial ({int(stable_minutes)}/{int(BATCH_MIN_EVAL_MINUTES)} min)",
            "quality_color": "sky",
            "target_depression_pa": None,
            "depression_pa": round(cur_dep, 1),
            "dep_delta": None,
            "dep_delta_avg": None,
            "stable_minutes": int(stable_minutes),
            "exhaust_rpm": cur_rpm,
            "current_trim": cur_e_trim,
            "suggested_trim": cur_e_trim,
            "explanation": (
                f"L'analyse du tirage dynamique requiert {int(BATCH_MIN_EVAL_MINUTES)} min de régime établi en TRAVAIL (actuellement {int(stable_minutes)} min). "
                f"Tarage extracteur actuel : {cur_e_trim:+d}%."
            ),
            "action_needed": False
        }
        return pellet_q, exhaust_q

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
            "La chaleur n'est pas évacuée de l'échangeur car les ventilateurs sont à 0 (OFF). "
            "Activez le mode PS (offset 33132/33134/33136 = 8) et ramenez tout tarage excessif entre -5% et +5%."
        )

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
            "Interdiction d'augmenter PRISE D'AIR pour masquer un colmatage (risque Alarme 11/13). "
            "À froid et débranché : aspirez le brasero, le compartiment derrière le coupe-flamme et vérifiez le conduit coaxial Ø 80/130."
        )

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
