"""
Sous-routeur pour l'assistant d'étalonnage couplé Nobis A10 (Nobis.Supervisor).
Gère la page HTML du wizard et les étapes d'acquisition multi-phases et tarage EEPROM.
"""
import asyncio
import os
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import HTMLResponse

from config import BASE_DIR
from database import get_active_mode, get_db
from telemetry import execute_register_write

router = APIRouter(tags=["Calibration"])


@router.get("/calibration", response_class=HTMLResponse)
@router.head("/calibration", response_class=HTMLResponse, include_in_schema=False)
async def serve_calibration_wizard():
    """Sert l'interface HTML de l'assistant d'étalonnage Nobis A10."""
    calib_file = os.path.join(BASE_DIR, "src", "ui", "wizard_calibration.html")
    no_cache = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    if os.path.exists(calib_file):
        with open(calib_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers=no_cache)
    raise HTTPException(status_code=404, detail="Page de calibration introuvable")


@router.get("/api/calibration/status")
async def api_calibration_status():
    """Retourne l'état complet de la session de calibration active (persiste en arrière-plan)."""
    from src.calibration.calibration_service import get_calibration_service
    calib_srv = get_calibration_service()

    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        last_row = conn.execute("SELECT * FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
        latest = dict(last_row) if last_row else {}
    finally:
        conn.close()

    return calib_srv.get_status(latest)


@router.post("/api/calibration/step1-start")
async def api_calibration_step1_start(payload: Dict[str, Any] = Body(default={})):
    """Étape 1 : Choix du mode et désactivation automatique de POWERFUL (P8)."""
    from src.calibration.calibration_service import build_sample_from_record, get_calibration_service
    calib_srv = get_calibration_service()
    mode_str = str(payload.get("mode") or "COMBINED_BOTH")

    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        last_row = conn.execute("SELECT * FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
        latest = dict(last_row) if last_row else {}
    finally:
        conn.close()

    sample = build_sample_from_record(latest)

    def _disable_pwf_cb():
        try:
            asyncio.create_task(execute_register_write("fun_pwf_set", 0.0, 33154, skip_poll=True))
            return True
        except Exception:
            return False

    return calib_srv.start_preflight(mode_str, sample, disable_p8_fn=_disable_pwf_cb)


@router.post("/api/calibration/step2-confirm-al03")
async def api_calibration_step2_confirm():
    """Étape 2 : Confirmation de l'état initial AL-03 et réarmement automatique."""
    from src.calibration.calibration_service import build_sample_from_record, get_calibration_service
    calib_srv = get_calibration_service()

    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        last_row = conn.execute("SELECT * FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
        latest = dict(last_row) if last_row else {}
    finally:
        conn.close()

    sample = build_sample_from_record(latest)

    def _reset_alarm_cb():
        try:
            asyncio.create_task(execute_register_write("status_code", 85.0, 232, skip_poll=True))
            return True
        except Exception:
            return False

    return calib_srv.confirm_initial_al03(sample, reset_alarm_fn=_reset_alarm_cb)


@router.post("/api/calibration/step3-load-charge")
async def api_calibration_step3_load(payload: Dict[str, Any] = Body(default={})):
    """Étape 3 : Verrouille le poids du sac étalon et lance l'acquisition en arrière-plan."""
    from src.calibration.calibration_service import build_sample_from_record, get_calibration_service
    calib_srv = get_calibration_service()
    weighed_kg = float(payload.get("weighed_bag_kg", 15.0))
    aspirated_g = float(payload.get("aspirated_priming_grams", 0.0))

    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        last_row = conn.execute("SELECT * FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
        latest = dict(last_row) if last_row else {}
    finally:
        conn.close()

    sample = build_sample_from_record(latest)
    return calib_srv.load_weighed_charge(sample, weighed_bag_kg=weighed_kg, aspirated_priming_g=aspirated_g)


@router.post("/api/calibration/step5-apply")
async def api_calibration_step5_apply():
    """Étape 5 : Application en 1 clic des Offsets 70 et 71 dans l'EEPROM Micronova."""
    from src.calibration.calibration_service import get_calibration_service
    calib_srv = get_calibration_service()

    written_vals = {}

    def _write_reg(reg: int, val: int) -> bool:
        written_vals[reg] = val
        rk = "trim_pellet_pct" if reg == 70 else "trim_exhaust_pct"
        asyncio.create_task(execute_register_write(rk, float(val), reg))
        return True

    def _read_reg(reg: int) -> int:
        return written_vals.get(reg, 0)

    return calib_srv.apply_recommendations(write_fn=_write_reg, read_fn=_read_reg)


@router.post("/api/calibration/reset")
async def api_calibration_reset():
    """Réinitialise la calibration pour repartir de zéro si souhaité."""
    from src.calibration.calibration_service import get_calibration_service
    calib_srv = get_calibration_service()
    return calib_srv.reset_session()
