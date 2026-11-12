# -*- coding: utf-8 -*-
"""
Module d'étalonnage gravimétrique et de tarage de combustion (Mélange P/A)
pour poêle à granulés Nobis A10 (A10 C Light / 2020 / Style / Coax).
Intègre le filtrage des phases transitoires (Allumage par encodeur RPM,
Inertie thermique, Extinction normale et Famine pré-AL03, P0/P8).
Dépôt cible : https://github.com/malexxx/Nobis.Supervisor
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class CalibrationMode(str, Enum):
    CONSUMPTION_ONLY = "CONSUMPTION_ONLY"   # Étalonnage débit (kg/h) seul
    COMBUSTION_ONLY = "COMBUSTION_ONLY"     # Tarage combustion (Offsets 70/71) seul
    COMBINED_BOTH = "COMBINED_BOTH"         # « Lancer les deux » (Protocole complet AL-03 -> AL-03)


class CalibrationState(str, Enum):
    IDLE = "IDLE"
    PREFLIGHT_CHECK = "PREFLIGHT_CHECK"             # Vérification et désactivation P8 (Powerful)
    WAITING_INITIAL_AL03 = "WAITING_INITIAL_AL03"   # Attente cuve 100% vide confirmée par AL-03
    WAITING_COOLING_OFF = "WAITING_COOLING_OFF"     # Réarmement AL-03 et attente état ÉTEINT (OFF)
    WAITING_CHARGE_WEIGHT = "WAITING_CHARGE_WEIGHT" # Pesée du sac étalon et amorçage vis sans fin
    RUNNING_ACQUISITION = "RUNNING_ACQUISITION"     # Enregistrement télémétrique multi-phases P0..P8
    COMPLETED_LOCKED = "COMPLETED_LOCKED"           # Clôture figée sur AL-03 final
    ABORTED_ERROR = "ABORTED_ERROR"


class StoveOperationalPhase(str, Enum):
    OFF = "ETEINT"
    STARTING = "MISE_EN_MARCHE"                     # Préchauffage bougie + pré-charge granulés
    WAITING_FLAME = "ATTENTE_FLAMME"                # Vis à 0 RPM, développement flamme sur air chaud
    FLAME_PHASE = "PHASE_FLAMME"                    # Reprise vis sans fin et stabilisation brasero
    WORK = "TRAVAIL"                                # Régime modulé P1 à P7 (Offsets 70/71 actifs)
    ECONOMY_RIS = "ECONOMIE"                        # P0 : Modulation basse / RIS (consigne atteinte)
    POWERFUL_BOOST = "POWERFUL"                     # P8 : Boost ventilation max + surcharge
    BRAZIER_CLEANING = "NETTOYAGE_BRASERO"          # Nettoyage périodique en marche (extracteur poussé)
    ACTIVE_CLEANER_CYCLE = "DISPOSITIF_NETTOYAGE_ACTIF"  # Extinction auto + raclage mécanique + rallumage
    REIGNITION = "RALLUMAGE"                        # Attente refroidissement avant redémarrage auto
    COOLING_FINAL = "NETTOYAGE_FINAL"               # Extinction : vis à 0 RPM, refroidissement échangeur
    CLIMATE_COMFORT_STANDBY = "CONFORT_CLIMAT_ECO"  # Veille thermique ECO (poêle arrêté)
    ALARM = "ALARME"                                # Arrêt sécurité (AL-01 à AL-29)


@dataclass
class TelemetrySample:
    timestamp: float
    phase: StoveOperationalPhase
    power_level: int                  # 0 (P0/RIS), 1..7 (P1..P7), 8 (P8/Powerful)
    alarm_code: Optional[str]         # ex: "AL-03", "AL-02", "AL-16", None
    auger_rpm_actual: float           # VIS S. FIN (RPM réel lu sur encodeur BLDC 24V)
    auger_rpm_set: float              # SET V.S.F. (RPM consigne)
    auger_current_ma: float           # AMP. V.S.F. (mA - chute < 65 mA si vis tourne à vide)
    flue_pressure_pa: float           # PRS (Pa)
    flue_pressure_set_pa: float       # SET PRS (Pa)
    smoke_extractor_rpm: float        # EXT. FUMEES (RPM)
    flame_temp_c: float               # T. FLAMME (°C - Sonde K foyer)
    smoke_temp_c: float               # T. HOT FUM (°C - Sonde fumées)
    room_temp_c: float                # T. PALMAR (°C)
    offset_70_pellet: int             # MELANGE P/A -> PELLET (-5 à +5 %)
    offset_71_air: int                # MELANGE P/A -> PRISE D AIR (-5 à +5 %)
    powerful_enabled_mode: str        # "OFF", "ON", "CHRONO"
    optima_flame_active: bool = False
    anticondensation_active: bool = False
    excessive_load_flag: bool = False
    hot_fumes_flag: bool = False
    hopper_lid_open: bool = False
    door_or_ashtray_open: bool = False


@dataclass
class PhaseBreakdownStats:
    ignition_cycles_count: int = 0
    startup_phases_duration_min: float = 0.0
    startup_estimated_mass_kg: float = 0.0
    brazier_cleaning_duration_min: float = 0.0
    cooling_extinction_ignored_min: float = 0.0
    starvation_pre_al03_ignored_min: float = 0.0
    purged_pre_al03_combustion_samples: int = 0
    warmup_filtered_combustion_samples: int = 0


@dataclass
class CalibrationResult:
    mode: CalibrationMode
    weighed_bag_kg: float
    intrinsic_k_factor: float
    before_flows_kgh: Dict[int, float]
    measured_raw_flows_kgh: Dict[int, float]
    coupled_effective_flows_kgh: Dict[int, float]
    recommended_offset_70: int
    recommended_offset_71: int
    current_offset_70: int
    current_offset_71: int
    time_per_power_hours: Dict[int, float]
    phase_stats: PhaseBreakdownStats
    mean_smoke_temp_delta_c: float
    mean_pressure_error_pct: float
    diagnostic_summary: str
    closed_by_al03: bool


class NobisA10CalibrationEngine:
    """
    Moteur d'étalonnage précis pour Nobis A10 (Firmware T033.NBS.AIR.MB01).
    Sépare rigoureusement les phases transitoires (Allumage, Nettoyage, Extinction,
    Famine pré-AL03, Ralenti P0, Boost P8) des phases de travail stabilisées P1..P7.
    """

    # Débits nominaux certifiés (IMQ CS25-0117813-01) de P1 (0.86 kg/h) à P7 (2.16 kg/h)
    NOMINAL_FLOWS_KGH: Dict[int, float] = {
        0: 0.860,  # P0 : ÉCONOMIE / RIS (ajusté dynamiquement par ratio RPM si SET V.S.F. < P1)
        1: 0.860,  # P1 : Puissance réduite certifiée (4.1 kW in / 3.8 kW out)
        2: 1.077,
        3: 1.293,
        4: 1.510,
        5: 1.727,
        6: 1.943,
        7: 2.160,  # P7 : Puissance nominale certifiée (10.4 kW in / 9.5 kW out)
        8: 2.320,  # P8 : POWERFUL (extrapolé dynamiquement via ratio SET V.S.F. P8 / P7)
    }

    # Températures théoriques des fumées (°C) par palier stabilisé P1..P7
    REF_SMOKE_TEMPS_C: Dict[int, float] = {
        1: 101.0,
        2: 112.2,
        3: 123.3,
        4: 134.5,
        5: 145.7,
        6: 156.8,
        7: 168.0,
    }

    # Constantes électromécaniques Nobis A10 (REAL STATUS 02/02)
    REF_P7_AUGER_RPM: float = 850.0           # Consigne nominale SET V.S.F. au palier P7
    REF_P1_AUGER_RPM: float = 338.0           # Consigne nominale estimée au palier P1
    FALLBACK_IGNITION_MASS_KG: float = 0.200  # Utilisé uniquement si auger_rpm_actual == 0 tout l'allumage
    EMPTY_AUGER_CURRENT_MA: float = 65.0      # Seuil courant BLDC à vide (famine de granulés pré-AL03)
    HOPPER_CAPACITY_KG: float = 35.0

    # Constantes de filtrage temporel des phases transitoires
    POST_IGNITION_WARMUP_SEC: float = 1200.0      # 20 min d'inertie corps de chauffe après allumage
    MIN_STABLE_TIER_DURATION_SEC: float = 900.0   # 15 min de stabilité requises après changement P1..P7
    POST_P8_COOLDOWN_SEC: float = 300.0           # 5 min d'exclusion thermique après sortie de P8
    PRE_AL03_ROLLING_PURGE_SEC: float = 600.0     # 10 min purgées rétroactivement avant AL-03

    def __init__(self, current_k_factor: float = 1.0) -> None:
        self.state: CalibrationState = CalibrationState.IDLE
        self.mode: CalibrationMode = CalibrationMode.COMBINED_BOTH
        self.k_intrinsic: float = current_k_factor
        self.weighed_mass_kg: float = 0.0
        self.priming_extracted_mass_kg: float = 0.0

        # Accumulateurs gravimétriques par palier (P0 à P8)
        self.raw_time_seconds: Dict[int, float] = {k: 0.0 for k in range(0, 9)}
        self.weighted_time_hours: Dict[int, float] = {k: 0.0 for k in range(0, 9)}

        # Accumulateurs spécifiques aux phases transitoires d'allumage (indépendants de l'Offset 70)
        self.startup_equivalent_p7_hours: float = 0.0
        self.startup_rpm_telemetry_available: bool = False
        self.phase_stats: PhaseBreakdownStats = PhaseBreakdownStats()

        # Tampons et repères temporels pour le tarage de combustion P/A
        self.telemetry_window: List[TelemetrySample] = []
        self._last_sample: Optional[TelemetrySample] = None
        self._last_p8_timestamp: float = -1e9
        self._last_ignition_end_ts: float = -1e9
        self._current_tier_entry_ts: float = 0.0
        self._in_ignition_sequence: bool = False
        self._overload_event_detected: bool = False
        self.result: Optional[CalibrationResult] = None

    def get_effective_flow_rates(self, offset_70: int) -> Dict[int, float]:
        """
        Couplage mathématique bidirectionnel :
        Tout ajustement du tarage de la vis sans fin (Offset 70 : -5% à +5%)
        est automatiquement répercuté comme facteur correcteur sur les débits horaires.
        """
        clamped_offset = max(-5, min(5, int(offset_70)))
        coupling_multiplier = 1.0 + (clamped_offset / 100.0)
        return {
            k: round(flow * self.k_intrinsic * coupling_multiplier, 3)
            for k, flow in self.NOMINAL_FLOWS_KGH.items()
        }

    def start_process(self, mode: CalibrationMode) -> CalibrationState:
        """Réinitialise tous les accumulateurs et lance le pré-contrôle (désactivation P8)."""
        self.mode = mode
        self.raw_time_seconds = {k: 0.0 for k in range(0, 9)}
        self.weighted_time_hours = {k: 0.0 for k in range(0, 9)}
        self.startup_equivalent_p7_hours = 0.0
        self.startup_rpm_telemetry_available = False
        self.phase_stats = PhaseBreakdownStats()
        self.telemetry_window.clear()
        self._last_sample = None
        self._last_p8_timestamp = -1e9
        self._last_ignition_end_ts = -1e9
        self._in_ignition_sequence = False
        self._overload_event_detected = False
        self.result = None
        self.state = CalibrationState.PREFLIGHT_CHECK
        return self.state

    def verify_preflight_and_disable_p8(
        self,
        sample: TelemetrySample,
        disable_p8_callback: Optional[Callable[[], bool]] = None
    ) -> Tuple[bool, str]:
        """
        Vérifie que le mode Boost POWERFUL (P8) est désactivé (OFF).
        Si actif (ON ou CHRONO), déclenche sa désactivation automatique.
        """
        if sample.powerful_enabled_mode != "OFF" or sample.power_level == 8:
            if disable_p8_callback is not None and disable_p8_callback():
                pass
            else:
                return (
                    False,
                    "Impossible de désactiver POWERFUL (P8) automatiquement. "
                    "Sur la radiocommande : MENU >> HABILITER PWF >> OFF."
                )

        if self.mode == CalibrationMode.COMBUSTION_ONLY:
            self.state = CalibrationState.RUNNING_ACQUISITION
            return True, "Pré-contrôle validé (Boost P8 désactivé). Acquisition combustion démarrée."

        self.state = CalibrationState.WAITING_INITIAL_AL03
        return True, "Pré-contrôle validé (Boost P8 désactivé). En attente de l'état initial AL-03."

    def confirm_initial_al03(self, sample: TelemetrySample) -> Tuple[bool, str]:
        """Valide le point de départ : cuve 100% vide et vis purgée confirmées par AL-03."""
        if sample.alarm_code != "AL-03":
            return False, f"État actuel ({sample.alarm_code or sample.phase.value}) différent de AL-03."
        self.state = CalibrationState.WAITING_COOLING_OFF
        return True, "État AL-03 initial confirmé. Réarmez l'alarme et attendez l'état ÉTEINT (OFF)."

    def confirm_stove_off_and_load_charge(
        self,
        sample: TelemetrySample,
        weighed_bag_kg: float,
        priming_aspirated_kg: float = 0.0
    ) -> Tuple[bool, str]:
        """
        Enregistre la charge étalon pesée une fois le poêle en état ÉTEINT (ou NETTOYAGE FINAL)
        et déduit éventuellement la masse aspirée dans le brasero après CRG. VIS S. FIN.
        """
        if sample.phase not in (StoveOperationalPhase.OFF, StoveOperationalPhase.COOLING_FINAL):
            return False, "Le poêle doit être en état ÉTEINT ou NETTOYAGE FINAL pour charger la cuve."
        if weighed_bag_kg < 5.0 or weighed_bag_kg > self.HOPPER_CAPACITY_KG:
            return False, "La masse étalon pesée doit être comprise entre 5,0 kg et 35,0 kg."
        if sample.hopper_lid_open or sample.door_or_ashtray_open:
            return False, "Veuillez refermer le couvercle granulés et la porte/cendrier (anti AL-15/AL-16)."

        self.weighed_mass_kg = float(weighed_bag_kg)
        self.priming_extracted_mass_kg = float(priming_aspirated_kg)
        self.state = CalibrationState.RUNNING_ACQUISITION
        return True, f"Charge étalon nette de {self.weighed_mass_kg - self.priming_extracted_mass_kg:.3f} kg verrouillée."

    def get_current_estimated_consumed_mass_kg(self) -> float:
        """Retourne l'estimation temps réel de la masse consommée depuis le départ."""
        if self.startup_rpm_telemetry_available:
            startup_mass = self.NOMINAL_FLOWS_KGH[7] * self.startup_equivalent_p7_hours
        else:
            startup_mass = self.phase_stats.ignition_cycles_count * self.FALLBACK_IGNITION_MASS_KG

        work_mass = sum(
            self.NOMINAL_FLOWS_KGH[k] * self.weighted_time_hours[k]
            for k in range(0, 9)
        )
        return (startup_mass + work_mass) * self.k_intrinsic

    def ingest_telemetry(self, sample: TelemetrySample) -> CalibrationState:
        """
        Machine d'ingestion temps réel prenant en compte chaque phase opérationnelle :
        - Allumage (MISE_EN_MARCHE, ATTENTE_FLAMME, PHASE_FLAMME) : intégration RPM sans Offset 70.
        - Extinction / Veille (NETTOYAGE_FINAL, ETEINT, CONFORT_CLIMAT_ECO) : exclusion totale.
        - Famine de fin de cuve (pré-AL03 en phase TRAVAIL) : détection courant vis à vide + chute T°.
        - Travail (P1..P7) : intégration massique + filtrage inertie 20 min post-allumage / 15 min palier.
        """
        if self.state != CalibrationState.RUNNING_ACQUISITION:
            return self.state

        if self._last_sample is None:
            self._last_sample = sample
            self._current_tier_entry_ts = sample.timestamp
            return self.state

        dt_sec = max(0.0, sample.timestamp - self._last_sample.timestamp)

        # Mise à jour du timer de stabilisation lors d'un changement de palier ou de phase
        if (sample.power_level != self._last_sample.power_level or
                sample.phase != self._last_sample.phase):
            self._current_tier_entry_ts = sample.timestamp

        # Enregistrement des drapeaux d'anomalie de surcharge thermique
        if sample.excessive_load_flag or sample.hot_fumes_flag:
            self._overload_event_detected = True

        # --- DÉTECTION DE FIN DE CYCLE : AL-03 (ou AL-02 en fin de trémie > 92% à l'allumage) ---
        if sample.alarm_code == "AL-03":
            self._finalize_calibration(sample, closed_by_al03=True)
            return self.state

        if (sample.alarm_code == "AL-02" and
                self.weighed_mass_kg > 0 and
                self.get_current_estimated_consumed_mass_kg() >= 0.92 * self.weighed_mass_kg):
            # Cas limite : panne sèche survenant exactement pendant un rallumage automatique
            self._finalize_calibration(sample, closed_by_al03=True)
            return self.state

        # Suspension sécurité si trémie ou porte ouverte (vis coupée par la carte mère)
        if sample.hopper_lid_open or sample.door_or_ashtray_open:
            self._last_sample = sample
            return self.state

        # --- CAS 1 : PHASES D'ALLUMAGE (MISE_EN_MARCHE, ATTENTE_FLAMME, PHASE_FLAMME) ---
        if sample.phase in (
            StoveOperationalPhase.STARTING,
            StoveOperationalPhase.WAITING_FLAME,
            StoveOperationalPhase.FLAME_PHASE,
        ):
            if not self._in_ignition_sequence:
                self._in_ignition_sequence = True
                self.phase_stats.ignition_cycles_count += 1

            self.phase_stats.startup_phases_duration_min += dt_sec / 60.0

            # Intégration exacte par encodeur RPM du motoréducteur BLDC (indépendant de l'Offset 70)
            # En ATTENTE_FLAMME, auger_rpm_actual == 0 donc l'intégrale ajoute 0.0 kg
            if sample.auger_rpm_actual > 0:
                self.startup_rpm_telemetry_available = True
                rpm_ratio = sample.auger_rpm_actual / self.REF_P7_AUGER_RPM
                self.startup_equivalent_p7_hours += rpm_ratio * (dt_sec / 3600.0)

            self._last_ignition_end_ts = sample.timestamp
            self._last_sample = sample
            return self.state

        # Sortie de la séquence d'allumage
        if self._in_ignition_sequence:
            self._in_ignition_sequence = False
            self._last_ignition_end_ts = sample.timestamp

        # --- CAS 2 : PHASES D'EXTINCTION NORMALE, REFROIDISSEMENT ET VEILLE ---
        if sample.phase in (
            StoveOperationalPhase.COOLING_FINAL,
            StoveOperationalPhase.OFF,
            StoveOperationalPhase.CLIMATE_COMFORT_STANDBY,
            StoveOperationalPhase.ACTIVE_CLEANER_CYCLE,
            StoveOperationalPhase.REIGNITION,
            StoveOperationalPhase.ALARM,
        ):
            # Vis sans fin à l'arrêt (0 RPM) et extracteur en refroidissement : on ignore totalement
            self.phase_stats.cooling_extinction_ignored_min += dt_sec / 60.0
            self._last_sample = sample
            return self.state

        # --- CAS 3 : PHASES DE PRODUCTION THERMIQUE (TRAVAIL P1..P7, P0 RIS, P8 POWERFUL, NETTOYAGE BRASERO) ---
        if sample.phase in (
            StoveOperationalPhase.WORK,
            StoveOperationalPhase.ECONOMY_RIS,
            StoveOperationalPhase.POWERFUL_BOOST,
            StoveOperationalPhase.BRAZIER_CLEANING,
        ):
            if sample.phase == StoveOperationalPhase.BRAZIER_CLEANING:
                self.phase_stats.brazier_cleaning_duration_min += dt_sec / 60.0

            # Identification du palier effectif k in {0..8}
            if sample.phase == StoveOperationalPhase.ECONOMY_RIS:
                k = 0
                if sample.auger_rpm_set > 0:
                    self.NOMINAL_FLOWS_KGH[0] = round(
                        self.NOMINAL_FLOWS_KGH[1] * (sample.auger_rpm_set / self.REF_P1_AUGER_RPM), 3
                    )
            elif sample.phase == StoveOperationalPhase.POWERFUL_BOOST:
                k = 8
                self._last_p8_timestamp = sample.timestamp
                if sample.auger_rpm_set > 0:
                    self.NOMINAL_FLOWS_KGH[8] = round(
                        self.NOMINAL_FLOWS_KGH[7] * (sample.auger_rpm_set / self.REF_P7_AUGER_RPM), 3
                    )
            else:
                k = max(0, min(8, sample.power_level))

            # Détection de l'extinction par famine de fin de cuve (vis tournant à vide juste avant AL-03)
            flame_drop_rate_c_per_min = (
                (sample.flame_temp_c - self._last_sample.flame_temp_c) / (dt_sec / 60.0)
                if dt_sec > 0 else 0.0
            )
            smoke_drop_rate_c_per_min = (
                (sample.smoke_temp_c - self._last_sample.smoke_temp_c) / (dt_sec / 60.0)
                if dt_sec > 0 else 0.0
            )
            is_starving_empty = (
                sample.auger_current_ma < self.EMPTY_AUGER_CURRENT_MA
                and (flame_drop_rate_c_per_min < -15.0 or smoke_drop_rate_c_per_min < -4.0)
            )

            if is_starving_empty:
                # La vis tourne dans le vide en attendant que la chute de T° déclenche AL-03
                self.phase_stats.starvation_pre_al03_ignored_min += dt_sec / 60.0
            else:
                # Facteurs modificateurs internes du firmware Micronova
                alpha = 1.0
                if sample.optima_flame_active:
                    alpha *= 0.92
                if sample.anticondensation_active:
                    alpha *= 1.05

                offset_factor = 1.0 + (sample.offset_70_pellet / 100.0)
                self.raw_time_seconds[k] += dt_sec
                self.weighted_time_hours[k] += alpha * offset_factor * (dt_sec / 3600.0)

            # --- FILTRAGE STRICT POUR LE TARAGE DE COMBUSTION (Offsets 70/71) ---
            # Exclusion de P0 (RIS), P8 (Powerful + 5 min), Nettoyage Brasero, Famine pré-AL03,
            # ainsi que des 20 min de chauffe initiale post-allumage et 15 min de transition de palier.
            time_since_p8 = sample.timestamp - self._last_p8_timestamp
            time_since_ignition = sample.timestamp - self._last_ignition_end_ts
            tier_stability_duration = sample.timestamp - self._current_tier_entry_ts

            if sample.phase == StoveOperationalPhase.WORK and 1 <= sample.power_level <= 7 and not is_starving_empty:
                if (time_since_p8 > self.POST_P8_COOLDOWN_SEC and
                        time_since_ignition >= self.POST_IGNITION_WARMUP_SEC and
                        tier_stability_duration >= self.MIN_STABLE_TIER_DURATION_SEC):
                    self.telemetry_window.append(sample)
                else:
                    self.phase_stats.warmup_filtered_combustion_samples += 1

        self._last_sample = sample
        return self.state

    def _finalize_calibration(self, final_sample: TelemetrySample, closed_by_al03: bool) -> None:
        """
        Verrouille les calculs à la réception obligatoire de l'erreur AL-03 :
        1. Purge rétroactivement les 10 dernières minutes du tampon P/A (extinction par famine).
        2. Calcule K_intrinsèque en isolant la masse d'allumage (RPM) de la masse de travail.
        3. Calcule les recommandations Offsets 70 et 71 sur les seuls paliers stabilisés.
        """
        # 1. Purge rétroactive des 10 dernières minutes avant AL-03
        cutoff_ts = final_sample.timestamp - self.PRE_AL03_ROLLING_PURGE_SEC
        initial_count = len(self.telemetry_window)
        valid_combustion_samples = [s for s in self.telemetry_window if s.timestamp <= cutoff_ts]
        self.phase_stats.purged_pre_al03_combustion_samples = initial_count - len(valid_combustion_samples)

        if not valid_combustion_samples and self.telemetry_window:
            valid_combustion_samples = self.telemetry_window[:-1] or self.telemetry_window

        # 2. Bilan gravimétrique exact (Allumages par encodeur RPM + Travail pondéré)
        if self.startup_rpm_telemetry_available:
            theoretical_startup_mass = self.NOMINAL_FLOWS_KGH[7] * self.startup_equivalent_p7_hours
        else:
            theoretical_startup_mass = (
                self.phase_stats.ignition_cycles_count * self.FALLBACK_IGNITION_MASS_KG
            )

        if self.mode in (CalibrationMode.CONSUMPTION_ONLY, CalibrationMode.COMBINED_BOTH):
            net_mass_kg = max(0.1, self.weighed_mass_kg - self.priming_extracted_mass_kg)
            theoretical_work_mass = sum(
                self.NOMINAL_FLOWS_KGH[k] * self.weighted_time_hours[k]
                for k in range(0, 9)
            )
            theoretical_total_mass = theoretical_startup_mass + theoretical_work_mass
            if theoretical_total_mass > 0.5:
                self.k_intrinsic = round(net_mass_kg / theoretical_total_mass, 4)

        self.phase_stats.startup_estimated_mass_kg = round(
            theoretical_startup_mass * self.k_intrinsic, 3
        )
        self.phase_stats.startup_phases_duration_min = round(
            self.phase_stats.startup_phases_duration_min, 1
        )
        self.phase_stats.brazier_cleaning_duration_min = round(
            self.phase_stats.brazier_cleaning_duration_min, 1
        )
        self.phase_stats.cooling_extinction_ignored_min = round(
            self.phase_stats.cooling_extinction_ignored_min, 1
        )
        self.phase_stats.starvation_pre_al03_ignored_min = round(
            self.phase_stats.starvation_pre_al03_ignored_min, 1
        )

        # 3. Calcul des recommandations de tarage combustion (Offsets 70 et 71)
        eps_m_pct = (self.k_intrinsic - 1.0) * 100.0
        cur_off70 = final_sample.offset_70_pellet
        cur_off71 = final_sample.offset_71_air

        if valid_combustion_samples and self.mode in (
            CalibrationMode.COMBUSTION_ONLY,
            CalibrationMode.COMBINED_BOTH,
        ):
            smoke_deltas = [
                s.smoke_temp_c - self.REF_SMOKE_TEMPS_C.get(s.power_level, 134.5)
                for s in valid_combustion_samples
            ]
            mean_smoke_delta_c = sum(smoke_deltas) / len(smoke_deltas)

            pressure_errors = [
                ((s.flue_pressure_pa - s.flue_pressure_set_pa) / max(1.0, s.flue_pressure_set_pa)) * 100.0
                for s in valid_combustion_samples
            ]
            mean_pressure_err_pct = sum(pressure_errors) / len(pressure_errors)

            overload_penalty = 2.0 if self._overload_event_detected else 0.0

            raw_off70 = cur_off70 - (0.65 * eps_m_pct) - (0.08 * mean_smoke_delta_c) - overload_penalty
            rec_off70 = max(-5, min(5, int(round(raw_off70))))

            raw_off71 = (
                cur_off71
                - (0.50 * mean_pressure_err_pct)
                + (0.10 * mean_smoke_delta_c)
                + 0.35 * (eps_m_pct + rec_off70 - cur_off70)
            )
            rec_off71 = max(-5, min(5, int(round(raw_off71))))
        else:
            mean_smoke_delta_c = 0.0
            mean_pressure_err_pct = 0.0
            rec_off70 = cur_off70
            rec_off71 = cur_off71

        before_flows = {k: round(self.NOMINAL_FLOWS_KGH[k], 3) for k in range(0, 9)}
        raw_measured_flows = {
            k: round(self.NOMINAL_FLOWS_KGH[k] * self.k_intrinsic, 3) for k in range(0, 9)
        }
        coupled_flows = self.get_effective_flow_rates(rec_off70)
        hours_by_tier = {k: round(self.raw_time_seconds[k] / 3600.0, 2) for k in range(0, 9)}

        diag = (
            f"Facteur K={self.k_intrinsic:.4f} ({eps_m_pct:+.1f}%). "
            f"Allumages={self.phase_stats.ignition_cycles_count} ({self.phase_stats.startup_estimated_mass_kg:.3f} kg). "
            f"Famine pré-AL03 filtrée={self.phase_stats.starvation_pre_al03_ignored_min:.1f} min. "
            f"Écart T° fumées={mean_smoke_delta_c:+.1f}°C, Écart tirage={mean_pressure_err_pct:+.1f}%. "
            f"Recommandation : Offset 70 = {rec_off70:+d}%, Offset 71 = {rec_off71:+d}%."
        )

        self.result = CalibrationResult(
            mode=self.mode,
            weighed_bag_kg=self.weighed_mass_kg,
            intrinsic_k_factor=self.k_intrinsic,
            before_flows_kgh=before_flows,
            measured_raw_flows_kgh=raw_measured_flows,
            coupled_effective_flows_kgh=coupled_flows,
            recommended_offset_70=rec_off70,
            recommended_offset_71=rec_off71,
            current_offset_70=cur_off70,
            current_offset_71=cur_off71,
            time_per_power_hours=hours_by_tier,
            phase_stats=self.phase_stats,
            mean_smoke_temp_delta_c=round(mean_smoke_delta_c, 2),
            mean_pressure_error_pct=round(mean_pressure_err_pct, 2),
            diagnostic_summary=diag,
            closed_by_al03=closed_by_al03,
        )
        self.state = CalibrationState.COMPLETED_LOCKED

    def apply_offsets_one_click(
        self,
        agua_write_register_fn: Callable[[int, int], bool],
        agua_read_register_fn: Callable[[int], int],
    ) -> Tuple[bool, Dict[int, float], str]:
        """
        Applique en 1 clic les Offsets 70 et 71 dans l'EEPROM de la carte Micronova
        avec vérification Read-After-Write, et met à jour les débits horaires couplés.
        """
        if self.result is None:
            return False, {}, "Aucun résultat d'étalonnage verrouillé disponible."

        target_70 = self.result.recommended_offset_70
        target_71 = self.result.recommended_offset_71

        ok_70 = agua_write_register_fn(70, target_70)
        ok_71 = agua_write_register_fn(71, target_71)
        if not (ok_70 and ok_71):
            return False, {}, "Échec d'écriture Agua IoT. Utilisez le guide radiocommande."

        read_70 = agua_read_register_fn(70)
        read_71 = agua_read_register_fn(71)
        if read_70 != target_70 or read_71 != target_71:
            return (
                False,
                {},
                f"Désynchronisation après relecture (lu 70={read_70}, 71={read_71}).",
            )

        updated_flows = self.get_effective_flow_rates(read_70)
        self.result.coupled_effective_flows_kgh = updated_flows
        self.result.current_offset_70 = read_70
        self.result.current_offset_71 = read_71
        return True, updated_flows, f"Offsets 70 ({read_70:+d}%) et 71 ({read_71:+d}%) appliqués et couplés."

    def to_dict(self) -> Dict[str, Any]:
        """Sérialise l'état complet du moteur pour sauvegarde SQLite persistante."""
        return {
            "state": self.state.value,
            "mode": self.mode.value,
            "k_intrinsic": self.k_intrinsic,
            "weighed_mass_kg": self.weighed_mass_kg,
            "priming_extracted_mass_kg": self.priming_extracted_mass_kg,
            "raw_time_seconds": {str(k): v for k, v in self.raw_time_seconds.items()},
            "weighted_time_hours": {str(k): v for k, v in self.weighted_time_hours.items()},
            "startup_equivalent_p7_hours": self.startup_equivalent_p7_hours,
            "startup_rpm_telemetry_available": self.startup_rpm_telemetry_available,
            "phase_stats": asdict(self.phase_stats),
            "telemetry_window": [
                {**asdict(s), "phase": s.phase.value}
                for s in self.telemetry_window[-300:]
            ],
            "last_sample": (
                {**asdict(self._last_sample), "phase": self._last_sample.phase.value}
                if self._last_sample else None
            ),
            "last_p8_timestamp": self._last_p8_timestamp,
            "last_ignition_end_ts": self._last_ignition_end_ts,
            "current_tier_entry_ts": self._current_tier_entry_ts,
            "in_ignition_sequence": self._in_ignition_sequence,
            "overload_event_detected": self._overload_event_detected,
            "result": (
                {
                    **asdict(self.result),
                    "mode": self.result.mode.value,
                    "phase_stats": asdict(self.result.phase_stats),
                    "before_flows_kgh": {str(k): v for k, v in self.result.before_flows_kgh.items()},
                    "measured_raw_flows_kgh": {str(k): v for k, v in self.result.measured_raw_flows_kgh.items()},
                    "coupled_effective_flows_kgh": {str(k): v for k, v in self.result.coupled_effective_flows_kgh.items()},
                    "time_per_power_hours": {str(k): v for k, v in self.result.time_per_power_hours.items()},
                }
                if self.result else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NobisA10CalibrationEngine:
        """Désérialise l'état complet du moteur depuis un dump SQLite persisté."""
        engine = cls(current_k_factor=float(data.get("k_intrinsic", 1.0)))
        engine.state = CalibrationState(data.get("state", CalibrationState.IDLE.value))
        engine.mode = CalibrationMode(data.get("mode", CalibrationMode.COMBINED_BOTH.value))
        engine.weighed_mass_kg = float(data.get("weighed_mass_kg", 0.0))
        engine.priming_extracted_mass_kg = float(data.get("priming_extracted_mass_kg", 0.0))

        raw_ts = data.get("raw_time_seconds", {})
        engine.raw_time_seconds = {int(k): float(v) for k, v in raw_ts.items()}
        for k in range(0, 9):
            engine.raw_time_seconds.setdefault(k, 0.0)

        w_th = data.get("weighted_time_hours", {})
        engine.weighted_time_hours = {int(k): float(v) for k, v in w_th.items()}
        for k in range(0, 9):
            engine.weighted_time_hours.setdefault(k, 0.0)

        engine.startup_equivalent_p7_hours = float(data.get("startup_equivalent_p7_hours", 0.0))
        engine.startup_rpm_telemetry_available = bool(data.get("startup_rpm_telemetry_available", False))

        if "phase_stats" in data and isinstance(data["phase_stats"], dict):
            engine.phase_stats = PhaseBreakdownStats(**data["phase_stats"])

        def _parse_sample(d: Dict[str, Any]) -> TelemetrySample:
            d_copy = dict(d)
            if isinstance(d_copy.get("phase"), str):
                d_copy["phase"] = StoveOperationalPhase(d_copy["phase"])
            return TelemetrySample(**d_copy)

        if "telemetry_window" in data and isinstance(data["telemetry_window"], list):
            engine.telemetry_window = [_parse_sample(s) for s in data["telemetry_window"]]

        if data.get("last_sample"):
            engine._last_sample = _parse_sample(data["last_sample"])

        engine._last_p8_timestamp = float(data.get("last_p8_timestamp", -1e9))
        engine._last_ignition_end_ts = float(data.get("last_ignition_end_ts", -1e9))
        engine._current_tier_entry_ts = float(data.get("current_tier_entry_ts", 0.0))
        engine._in_ignition_sequence = bool(data.get("in_ignition_sequence", False))
        engine._overload_event_detected = bool(data.get("overload_event_detected", False))

        res_data = data.get("result")
        if res_data and isinstance(res_data, dict):
            res_copy = dict(res_data)
            res_copy["mode"] = CalibrationMode(res_copy["mode"])
            if isinstance(res_copy.get("phase_stats"), dict):
                res_copy["phase_stats"] = PhaseBreakdownStats(**res_copy["phase_stats"])
            res_copy["before_flows_kgh"] = {int(k): float(v) for k, v in res_copy.get("before_flows_kgh", {}).items()}
            res_copy["measured_raw_flows_kgh"] = {int(k): float(v) for k, v in res_copy.get("measured_raw_flows_kgh", {}).items()}
            res_copy["coupled_effective_flows_kgh"] = {int(k): float(v) for k, v in res_copy.get("coupled_effective_flows_kgh", {}).items()}
            res_copy["time_per_power_hours"] = {int(k): float(v) for k, v in res_copy.get("time_per_power_hours", {}).items()}
            engine.result = CalibrationResult(**res_copy)

        return engine
