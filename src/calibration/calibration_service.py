# -*- coding: utf-8 -*-
"""
Service de persistance et d'exécution en arrière-plan de la calibration Nobis A10.
Maintient l'état du moteur dans SQLite (nobis_a10_system.db -> table settings)
afin que la calibration se poursuive sans interruption en arrière-plan
même lorsque l'utilisateur quitte la page ou éteint son navigateur.
"""

from __future__ import annotations
import json
import time
import logging
from typing import Dict, Any, Optional, Callable
from dataclasses import asdict

from database import get_sys_db
from .nobis_calibration import (
    NobisA10CalibrationEngine,
    CalibrationState,
    StoveOperationalPhase,
    TelemetrySample,
)
from .calibration_wizard import NobisCalibrationWizard

logger = logging.getLogger("nobis.calibration")

CALIBRATION_SETTINGS_KEY = "nobis_a10_active_calibration_session"


def build_sample_from_record(
    record: Dict[str, Any], raw_buf: Optional[Dict[int, float]] = None
) -> TelemetrySample:
    """Transforme un enregistrement télémétrique du superviseur en TelemetrySample."""
    status_code = int(record.get("status_code") or 0)
    p_actual = int(record.get("power_actual") or record.get("power_level") or 4)
    fun_pwf = int(record.get("fun_pwf_set") or 0)

    # Détermination de la phase opérationnelle
    if status_code == 0:
        phase = StoveOperationalPhase.OFF
    elif status_code in (1, 2, 3):
        phase = StoveOperationalPhase.STARTING
    elif status_code == 4:
        phase = StoveOperationalPhase.WAITING_FLAME
    elif status_code == 5:
        if fun_pwf == 1 or p_actual == 8:
            phase = StoveOperationalPhase.POWERFUL_BOOST
        elif p_actual == 0:
            phase = StoveOperationalPhase.ECONOMY_RIS
        else:
            phase = StoveOperationalPhase.WORK
    elif status_code == 6:
        phase = StoveOperationalPhase.BRAZIER_CLEANING
    elif status_code == 7:
        phase = StoveOperationalPhase.COOLING_FINAL
    elif status_code in (8, 9):
        phase = StoveOperationalPhase.CLIMATE_COMFORT_STANDBY
    elif status_code in (10, 11):
        phase = StoveOperationalPhase.ALARM
    else:
        phase = StoveOperationalPhase.WORK

    # Alarm code (ne jamais assimiler popup_pellet_empty=1 [réserve ~12%] à l'alarme sèche AL-03)
    alarm_num = int(record.get("alarm_code") or 0)
    if alarm_num == 3:
        alarm_str = "AL-03"
    elif alarm_num == 2:
        alarm_str = "AL-02"
    elif alarm_num == 16:
        alarm_str = "AL-16"
    elif alarm_num == 15:
        alarm_str = "AL-15"
    elif alarm_num > 0:
        alarm_str = f"AL-{alarm_num:02d}"
    else:
        alarm_str = None

    # RPM tachymétrique estimé
    if phase == StoveOperationalPhase.STARTING:
        rpm_act = 425.0
    elif phase in (
        StoveOperationalPhase.WAITING_FLAME,
        StoveOperationalPhase.OFF,
        StoveOperationalPhase.COOLING_FINAL,
    ):
        rpm_act = 0.0
    else:
        rpm_act = 338.0 + (p_actual - 1) * ((850.0 - 338.0) / 6.0) if p_actual >= 1 else 338.0

    # Détection de courant à vide (uniquement sur véritable alarme AL-03)
    if alarm_str == "AL-03":
        cur_ma = 45.0
    else:
        cur_ma = 150.0

    pwf_mode = "ON" if fun_pwf == 1 else "OFF"

    # Normalisation de l'échelle de dépression : coaxiale réelle (2.3-3.8 Pa) vs banc de test (>= 8 Pa / 18 Pa)
    target_coax_pa = round(2.0 + max(1, min(7, p_actual)) * 0.25, 2)
    raw_dep_pa = float(record.get("depression_pa") or 0.0)
    if raw_dep_pa <= 0.0:
        flue_pa = target_coax_pa
        flue_set_pa = target_coax_pa
    elif raw_dep_pa < 8.0:
        flue_pa = raw_dep_pa
        flue_set_pa = target_coax_pa
    else:
        flue_pa = raw_dep_pa
        flue_set_pa = 18.0

    return TelemetrySample(
        timestamp=float(record.get("epoch") or time.time()),
        phase=phase,
        power_level=p_actual,
        alarm_code=alarm_str,
        auger_rpm_actual=round(rpm_act, 1),
        auger_rpm_set=round(rpm_act, 1),
        auger_current_ma=cur_ma,
        flue_pressure_pa=flue_pa,
        flue_pressure_set_pa=flue_set_pa,
        smoke_extractor_rpm=float(record.get("exhaust_rpm") or 1850.0),
        flame_temp_c=float(record.get("temp_probe_k") or 600.0),
        smoke_temp_c=float(record.get("temp_fumes") or 135.0),
        room_temp_c=float(record.get("temp_ambient") or 20.0),
        offset_70_pellet=int(record.get("trim_pellet_pct") or 0),
        offset_71_air=int(record.get("trim_exhaust_pct") or 0),
        powerful_enabled_mode=pwf_mode,
        hopper_lid_open=bool(record.get("popup_pellet_open")),
        door_or_ashtray_open=bool(record.get("popup_door_open")),
    )


class NobisCalibrationService:
    """
    Gestionnaire singleton de calibration persistée en arrière-plan.
    """

    def __init__(self) -> None:
        self.engine: NobisA10CalibrationEngine = NobisA10CalibrationEngine()
        self.active_step: int = 1
        self.last_updated_epoch: float = time.time()
        self.load_session()

    def load_session(self) -> None:
        """Charge l'état persisté depuis la table settings de la base système."""
        conn = get_sys_db()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (CALIBRATION_SETTINGS_KEY,)
            ).fetchone()
            if row and row["value"]:
                data = json.loads(row["value"])
                self.engine = NobisA10CalibrationEngine.from_dict(data.get("engine", {}))
                self.active_step = int(data.get("active_step", 1))
                self.last_updated_epoch = float(data.get("last_updated_epoch", time.time()))
                logger.info(
                    f"Session de calibration restaurée depuis SQLite (Étape {self.active_step}, État={self.engine.state.value})"
                )
        except Exception as e:
            logger.warning(f"Impossible de restaurer la session de calibration : {e}")
            self.engine = NobisA10CalibrationEngine()
            self.active_step = 1
        finally:
            conn.close()

    def save_session(self) -> None:
        """Sauvegarde atomique de l'état dans la table settings."""
        self.last_updated_epoch = time.time()
        data = {
            "active_step": self.active_step,
            "last_updated_epoch": self.last_updated_epoch,
            "engine": self.engine.to_dict(),
        }
        conn = get_sys_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (CALIBRATION_SETTINGS_KEY, json.dumps(data)),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Échec sauvegarde session de calibration : {e}")
        finally:
            conn.close()

    def start_preflight(
        self,
        mode_str: str,
        current_sample: TelemetrySample,
        disable_p8_fn: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Étape 1 : Démarre le protocole et désactive POWERFUL."""
        wizard = NobisCalibrationWizard(
            engine=self.engine,
            api_disable_powerful_fn=disable_p8_fn or (lambda: True),
            api_reset_alarm_fn=lambda: True,
            api_write_register_fn=lambda r, v: True,
            api_read_register_fn=lambda r: 0,
        )
        res = wizard.step1_configure_and_preflight(mode_str, current_sample)
        if res["success"]:
            self.active_step = 2 if self.engine.state == CalibrationState.WAITING_INITIAL_AL03 else 4
            self.save_session()
        return res

    def confirm_initial_al03(
        self,
        current_sample: TelemetrySample,
        reset_alarm_fn: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Étape 2 : Confirme la cuve vide initiale (AL-03) et réarme."""
        wizard = NobisCalibrationWizard(
            engine=self.engine,
            api_disable_powerful_fn=lambda: True,
            api_reset_alarm_fn=reset_alarm_fn or (lambda: True),
            api_write_register_fn=lambda r, v: True,
            api_read_register_fn=lambda r: 0,
        )
        res = wizard.step2_verify_empty_hopper_al03(current_sample, auto_reset_alarm=True)
        if res["success"]:
            self.active_step = 3
            self.save_session()
        return res

    def load_weighed_charge(
        self,
        current_sample: TelemetrySample,
        weighed_bag_kg: float,
        aspirated_priming_g: float = 0.0,
    ) -> Dict[str, Any]:
        """Étape 3 : Verrouille la charge étalon pesée et bascule en acquisition arrière-plan."""
        wizard = NobisCalibrationWizard(
            engine=self.engine,
            api_disable_powerful_fn=lambda: True,
            api_reset_alarm_fn=lambda: True,
            api_write_register_fn=lambda r, v: True,
            api_read_register_fn=lambda r: 0,
        )
        res = wizard.step3_register_weighed_charge(
            current_sample,
            weighed_bag_kg=weighed_bag_kg,
            aspirated_priming_grams=aspirated_priming_g,
        )
        if res["success"]:
            self.active_step = 4
            self.save_session()
        return res

    def process_telemetry_record(
        self, record: Dict[str, Any], raw_buf: Optional[Dict[int, float]] = None
    ) -> None:
        """
        Appelé à chaque cycle de télémétrie (ex: 60s) en arrière-plan.
        Ingère les échantillons automatiquement sans aucune interaction utilisateur requise.
        """
        if self.engine.state != CalibrationState.RUNNING_ACQUISITION:
            return

        sample = build_sample_from_record(record, raw_buf)
        new_state = self.engine.ingest_telemetry(sample)

        if new_state == CalibrationState.COMPLETED_LOCKED:
            self.active_step = 5
            logger.info(
                f"Étalonnage Nobis A10 clôturé automatiquement par AL-03 ! K={self.engine.k_intrinsic:.4f}"
            )

        # Sauvegarde persistante de la progression
        self.save_session()

    def get_status(
        self, latest_record: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Retourne l'état courant temps réel pour l'interface web (wizard_calibration.html)."""
        sample = (
            build_sample_from_record(latest_record)
            if latest_record
            else (self.engine._last_sample or build_sample_from_record({}))
        )

        consumed_est_kg = self.engine.get_current_estimated_consumed_mass_kg()
        remaining_est_kg = max(0.0, self.engine.weighed_mass_kg - consumed_est_kg)
        progress_pct = (
            min(99.5, (consumed_est_kg / max(1.0, self.engine.weighed_mass_kg)) * 100.0)
            if self.engine.state != CalibrationState.COMPLETED_LOCKED
            else 100.0
        )

        # Si l'étalonnage est verrouillé, nous sommes à l'étape 5
        if self.engine.state == CalibrationState.COMPLETED_LOCKED:
            step_to_report = 5
        elif self.engine.state == CalibrationState.RUNNING_ACQUISITION:
            step_to_report = 4
        else:
            step_to_report = self.active_step

        return {
            "step": step_to_report,
            "state": self.engine.state.value,
            "mode": self.engine.mode.value,
            "weighed_mass_kg": self.engine.weighed_mass_kg,
            "priming_extracted_mass_kg": self.engine.priming_extracted_mass_kg,
            "estimated_consumed_kg": round(consumed_est_kg, 3),
            "estimated_remaining_kg": round(remaining_est_kg, 3),
            "progress_pct": round(progress_pct, 1),
            "current_phase": sample.phase.value,
            "current_power_tier": f"P{sample.power_level}",
            "hours_per_tier": {
                f"P{k}": round(sec / 3600.0, 2)
                for k, sec in self.engine.raw_time_seconds.items()
                if sec > 0 or (1 <= k <= 7)
            },
            "phase_breakdown": asdict(self.engine.phase_stats),
            "valid_combustion_samples_count": len(self.engine.telemetry_window),
            "waiting_for_al03": self.engine.state == CalibrationState.RUNNING_ACQUISITION,
            "result": asdict(self.engine.result) if self.engine.result else None,
            "last_updated_epoch": self.last_updated_epoch,
        }

    def apply_recommendations(
        self,
        write_fn: Callable[[int, int], bool],
        read_fn: Callable[[int], int],
    ) -> Dict[str, Any]:
        """Étape 5 : Applique les offsets optimaux dans l'EEPROM Micronova."""
        ok, flows, msg = self.engine.apply_offsets_one_click(write_fn, read_fn)
        if ok:
            self.save_session()
        return {
            "success": ok,
            "message": msg,
            "coupled_flows_kgh": flows,
            "result": asdict(self.engine.result) if self.engine.result else None,
        }

    def reset_session(self) -> Dict[str, Any]:
        """Réinitialise la calibration et remet l'état à IDLE."""
        self.engine = NobisA10CalibrationEngine()
        self.active_step = 1
        conn = get_sys_db()
        try:
            conn.execute(
                "DELETE FROM settings WHERE key = ?", (CALIBRATION_SETTINGS_KEY,)
            )
            conn.commit()
        finally:
            conn.close()
        logger.info("Session de calibration réinitialisée.")
        return {"status": "ok", "message": "Calibration réinitialisée."}


# Instance globale du service
_calibration_service_instance: Optional[NobisCalibrationService] = None


def get_calibration_service() -> NobisCalibrationService:
    global _calibration_service_instance
    if _calibration_service_instance is None:
        _calibration_service_instance = NobisCalibrationService()
    return _calibration_service_instance
