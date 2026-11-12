import os
import sys
from typing import Dict, Any, List

# ==============================================================================
# GESTION DES CHEMINS & CONSTANTES MÉTIER (NOBIS A10 C LIGHT STYLE COAX)
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DATA_DIR = (
    "/app/data"
    if os.path.isdir("/app/data") and not sys.platform.startswith("win")
    else (
        os.path.join(BASE_DIR, "data")
        if os.path.isdir(os.path.join(BASE_DIR, "data"))
        else os.path.join(os.path.dirname(BASE_DIR), "data")
    )
)
DATA_DIR = os.path.dirname(os.environ["DB_PATH"]) if "DB_PATH" in os.environ else _DEFAULT_DATA_DIR
os.makedirs(DATA_DIR, exist_ok=True)

STATIC_DIR = next(
    (p for p in [
        os.path.join(BASE_DIR, "static"),
        os.path.join(BASE_DIR, "app", "static"),
        "/app/app/static",
        "/app/static"
    ] if os.path.isdir(p)),
    os.path.join(BASE_DIR, "static")
)
os.makedirs(STATIC_DIR, exist_ok=True)

SYSTEM_DB_PATH = os.path.join(DATA_DIR, "nobis_a10_system.db")
MODE_DB_PATHS: Dict[str, str] = {
    "simulation": os.path.join(DATA_DIR, "nobis_a10_simulation.db"),
    "agua_cloud": os.path.join(DATA_DIR, "nobis_a10_agua_cloud.db"),
    "local_gateway": os.path.join(DATA_DIR, "nobis_a10_local_gateway.db"),
}

# Capacité du réservoir de granulés du poêle Nobis A10 (en kg) et poids d'un sac standard
HOPPER_CAPACITY_KG: float = 35.0
PELLET_BAG_WEIGHT_KG: float = 15.0

STOVE_STATES: Dict[int, str] = {
    0: "OFF (Éteint)",
    1: "ATTENTE (Check-up)",
    2: "DÉBUT (Préchauffage)",
    3: "DÉBUT (Chargement pellet)",
    4: "DÉBUT (Allumage flamme)",
    5: "TRAVAIL (Normal)",
    6: "NETTOYAGE BRASIER",
    7: "ARRÊT (Refroidissement)",
    8: "ECO STOP (Stand-by actif)",
    9: "STAND BY",
    10: "ALARME SÉCURITÉ",
    11: "MÉMOIRE ALARME"
}

NOBIS_ALARMS: Dict[int, str] = {
    0: "Aucune (____)",
    1: "AL01 - BLACK OUT (Coupure secteur)",
    2: "AL02 - IGNITION MANQUÉE",
    3: "AL03 - PELLET FINI (Réservoir vide)",
    4: "AL04 - SURCHAUFFE TEMPÉRATURE FUMÉES",
    5: "AL05 - RPM EXTRACTEUR FUMÉES INCORRECT",
    6: "AL06 - DÉFAUT EXTRACTEUR FUMÉES",
    7: "AL07 - RPM MOTEUR PELLET INCORRECT",
    8: "AL08 - DÉFAUT MOTEUR PELLET",
    9: "AL09 - MOTEUR PELLET VERROUILLÉ / BLOQUÉ",
    10: "AL10 - TENSION MOTEUR PELLET",
    11: "AL11 - PRESSION MINIMUM Pa (Tirage/Coaxial)",
    12: "AL12 - DÉFAUT NETTOYEUR BRASIER",
    13: "AL13 - DÉPRESSION CHAMBRE",
    14: "AL14 - SÉCURITÉ THERMIQUE",
    15: "AL15 - SÉCURITÉ PORTE / TIROIR CENDRES",
    16: "AL16 - SÉCURITÉ COUVERCLE RÉSERVOIR PELLET",
    17: "AL17 - SÉCURITÉ PELLET",
    18: "AL18 - SONDE FLAMME K DÉFAUT",
    22: "AL22 - TEMPÉRATURE FLAMME",
    23: "AL23 - TRIAC MOTEUR PELLET",
    24: "AL24 - PHASE MOTEUR PELLET",
    28: "AL28 - ÉCHEC ENCODEUR FUMÉES",
    29: "AL29 - LIMITE CYCLES NETTOYAGE ATTEINTE"
}

# Mapping officiel entre nos clés internes et les reg_key Micronova (vincentwolsink/home_assistant_micronova_agua_iot)
AGUA_REG_KEY_ALIASES: Dict[str, List[str]] = {
    "status_code": ["status_managed_get", "status_managed", "power_on_off"],
    "power_level": ["power_set"],
    "temp_amb_setpoint": ["temp_air_set"],
    "fan_front_speed": ["vent_front_set"],
    "duct_left_setpoint": ["can_sx_temp_set", "temp_can_sx_set"],
    "duct_left_speed": ["can_sx_vent_set", "vent_can_sx_set"],
    "duct_right_setpoint": ["can_dx_temp_set", "temp_can_dx_set"],
    "duct_right_speed": ["can_dx_vent_set", "vent_can_dx_set"],
    "fun_auto_set": ["fun_auto_set", "auto_mode_set"],
    "fun_pwf_set": ["fun_pwf_set", "powerful_set"],
    "set_silece_set": ["set_silece_set", "silence_set"],
    "chrono_week_enable_set": ["chrono_week_enable_set", "chrono_enable_set"]
}

DEFAULT_REGISTERS_CATALOG: List[Dict[str, Any]] = [
    {"reg_key": "status_code", "offset": 232, "mem_type": "RAM", "label": "Commande Marche/Arrêt (85=ON, 170=OFF)", "unit": "cmd", "min_val": 0, "max_val": 255, "step": 1, "default_val": 0, "category": "STATE"},
    {"reg_key": "power_level", "offset": 33120, "mem_type": "EEPROM", "label": "Puissance de combustion (P1 à P7)", "unit": "P", "min_val": 1, "max_val": 7, "step": 1, "default_val": 4, "category": "CONTROL"},
    {"reg_key": "fun_auto_set", "offset": 33128, "mem_type": "EEPROM", "label": "Modulation Automatique - Auto puissance (0=OFF, 1=ON)", "unit": "bool", "min_val": 0, "max_val": 1, "step": 1, "default_val": 1, "category": "CONTROL"},
    {"reg_key": "standby_set", "offset": 33130, "mem_type": "EEPROM", "label": "Mode ECO - Stand-by auto sur consigne (Réglage radiocommande Nobis, 0=OFF, 1=ON)", "unit": "bool", "min_val": 0, "max_val": 1, "step": 1, "default_val": 0, "category": "CONTROL"},
    {"reg_key": "fun_pwf_set", "offset": 33154, "mem_type": "EEPROM", "label": "Mode BOOST - Surpuissance (0=OFF, 1=ON, 2=CRONO)", "unit": "mode", "min_val": 0, "max_val": 2, "step": 1, "default_val": 0, "category": "CONTROL"},
    {"reg_key": "set_silece_set", "offset": 33250, "mem_type": "EEPROM", "label": "Silencieux - Confort acoustique (0=OFF, 1=ON)", "unit": "bool", "min_val": 0, "max_val": 1, "step": 1, "default_val": 0, "category": "CONTROL"},
    {"reg_key": "temp_amb_setpoint", "offset": 33122, "mem_type": "EEPROM", "label": "Consigne T° Ambiante Salon (7..40°C, 41=MAN)", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 19, "category": "CONTROL"},
    {"reg_key": "fan_front_speed", "offset": 33132, "mem_type": "EEPROM", "label": "Ventilation Frontale (0=OFF, 1..7, 8=PS Auto)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 3, "category": "CONTROL"},
    {"reg_key": "duct_left_setpoint", "offset": 33126, "mem_type": "EEPROM", "label": "Consigne T° Canalisation Gauche SX (7..40°C, 41=MAN)", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "DUCT"},
    {"reg_key": "duct_left_speed", "offset": 33136, "mem_type": "EEPROM", "label": "Vitesse Canalisation Gauche SX (0=OFF, 1..7, 8=PS)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "DUCT"},
    {"reg_key": "duct_right_setpoint", "offset": 33124, "mem_type": "EEPROM", "label": "Consigne T° Canalisation Droite DX (7..40°C, 41=MAN)", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "DUCT"},
    {"reg_key": "duct_right_speed", "offset": 33134, "mem_type": "EEPROM", "label": "Vitesse Canalisation Droite DX (0=OFF, 1..7, 8=PS)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "DUCT"},
    {"reg_key": "chrono_week_enable_set", "offset": 33162, "mem_type": "EEPROM", "label": "Activation Générale Chrono Hebdomadaire (0=OFF, 1=ON)", "unit": "bool", "min_val": 0, "max_val": 1, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p1_start_set", "offset": 33164, "mem_type": "EEPROM", "label": "Chrono P1 - Heure Allumage (0..143 x10min, 144=OFF)", "unit": "slot", "min_val": 0, "max_val": 144, "step": 1, "default_val": 48, "category": "CHRONO"},
    {"reg_key": "chrono_p1_stop_set", "offset": 33166, "mem_type": "EEPROM", "label": "Chrono P1 - Heure Arrêt (0..143 x10min, 144=OFF)", "unit": "slot", "min_val": 0, "max_val": 144, "step": 1, "default_val": 63, "category": "CHRONO"},
    {"reg_key": "chrono_p1_days_mask", "offset": 33168, "mem_type": "EEPROM", "label": "Chrono P1 - Masque Jours (Lun=1..Dim=64, 127=Tous)", "unit": "mask", "min_val": 0, "max_val": 127, "step": 1, "default_val": 127, "category": "CHRONO"},
    {"reg_key": "chrono_p1_power_set", "offset": 33170, "mem_type": "EEPROM", "label": "Chrono P1 - Puissance (1..7)", "unit": "P", "min_val": 1, "max_val": 7, "step": 1, "default_val": 7, "category": "CHRONO"},
    {"reg_key": "chrono_p1_temperature_set", "offset": 33172, "mem_type": "EEPROM", "label": "Chrono P1 - Consigne T° Ambiante (7..41°C)", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 19, "category": "CHRONO"},
    {"reg_key": "chrono_p1_tcanr_set", "offset": 33174, "mem_type": "EEPROM", "label": "Chrono P1 - Consigne T° Canalisation Droite DX", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "CHRONO"},
    {"reg_key": "chrono_p1_tcanl_set", "offset": 33176, "mem_type": "EEPROM", "label": "Chrono P1 - Consigne T° Canalisation Gauche SX", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "CHRONO"},
    {"reg_key": "chrono_p1_fan_front_set", "offset": 33178, "mem_type": "EEPROM", "label": "Chrono P1 - Vitesse Ventilation Frontale (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p1_canr_set", "offset": 33180, "mem_type": "EEPROM", "label": "Chrono P1 - Vitesse Canalisation Droite DX (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p1_canl_set", "offset": 33182, "mem_type": "EEPROM", "label": "Chrono P1 - Vitesse Canalisation Gauche SX (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p2_start_set", "offset": 33184, "mem_type": "EEPROM", "label": "Chrono P2 - Heure Allumage (0..143 x10min, 144=OFF)", "unit": "slot", "min_val": 0, "max_val": 144, "step": 1, "default_val": 126, "category": "CHRONO"},
    {"reg_key": "chrono_p2_stop_set", "offset": 33186, "mem_type": "EEPROM", "label": "Chrono P2 - Heure Arrêt (0..143 x10min, 144=OFF)", "unit": "slot", "min_val": 0, "max_val": 144, "step": 1, "default_val": 138, "category": "CHRONO"},
    {"reg_key": "chrono_p2_days_mask", "offset": 33188, "mem_type": "EEPROM", "label": "Chrono P2 - Masque Jours (Lun=1..Dim=64, 127=Tous)", "unit": "mask", "min_val": 0, "max_val": 127, "step": 1, "default_val": 127, "category": "CHRONO"},
    {"reg_key": "chrono_p2_power_set", "offset": 33190, "mem_type": "EEPROM", "label": "Chrono P2 - Puissance (1..7)", "unit": "P", "min_val": 1, "max_val": 7, "step": 1, "default_val": 7, "category": "CHRONO"},
    {"reg_key": "chrono_p2_temperature_set", "offset": 33192, "mem_type": "EEPROM", "label": "Chrono P2 - Consigne T° Ambiante (7..41°C)", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 19, "category": "CHRONO"},
    {"reg_key": "chrono_p2_tcanr_set", "offset": 33194, "mem_type": "EEPROM", "label": "Chrono P2 - Consigne T° Canalisation Droite DX", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "CHRONO"},
    {"reg_key": "chrono_p2_tcanl_set", "offset": 33196, "mem_type": "EEPROM", "label": "Chrono P2 - Consigne T° Canalisation Gauche SX", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "CHRONO"},
    {"reg_key": "chrono_p2_fan_front_set", "offset": 33198, "mem_type": "EEPROM", "label": "Chrono P2 - Vitesse Ventilation Frontale (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p2_canr_set", "offset": 33200, "mem_type": "EEPROM", "label": "Chrono P2 - Vitesse Canalisation Droite DX (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p2_canl_set", "offset": 33202, "mem_type": "EEPROM", "label": "Chrono P2 - Vitesse Canalisation Gauche SX (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p3_start_set", "offset": 33204, "mem_type": "EEPROM", "label": "Chrono P3 - Heure Allumage (0..143 x10min, 144=OFF)", "unit": "slot", "min_val": 0, "max_val": 144, "step": 1, "default_val": 144, "category": "CHRONO"},
    {"reg_key": "chrono_p3_stop_set", "offset": 33206, "mem_type": "EEPROM", "label": "Chrono P3 - Heure Arrêt (0..143 x10min, 144=OFF)", "unit": "slot", "min_val": 0, "max_val": 144, "step": 1, "default_val": 144, "category": "CHRONO"},
    {"reg_key": "chrono_p3_days_mask", "offset": 33208, "mem_type": "EEPROM", "label": "Chrono P3 - Masque Jours (Lun=1..Dim=64, 127=Tous)", "unit": "mask", "min_val": 0, "max_val": 127, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p3_power_set", "offset": 33210, "mem_type": "EEPROM", "label": "Chrono P3 - Puissance (1..7)", "unit": "P", "min_val": 1, "max_val": 7, "step": 1, "default_val": 7, "category": "CHRONO"},
    {"reg_key": "chrono_p3_temperature_set", "offset": 33212, "mem_type": "EEPROM", "label": "Chrono P3 - Consigne T° Ambiante (7..41°C)", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "CHRONO"},
    {"reg_key": "chrono_p3_tcanr_set", "offset": 33214, "mem_type": "EEPROM", "label": "Chrono P3 - Consigne T° Canalisation Droite DX", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "CHRONO"},
    {"reg_key": "chrono_p3_tcanl_set", "offset": 33216, "mem_type": "EEPROM", "label": "Chrono P3 - Consigne T° Canalisation Gauche SX", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "CHRONO"},
    {"reg_key": "chrono_p3_fan_front_set", "offset": 33218, "mem_type": "EEPROM", "label": "Chrono P3 - Vitesse Ventilation Frontale (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p3_canr_set", "offset": 33220, "mem_type": "EEPROM", "label": "Chrono P3 - Vitesse Canalisation Droite DX (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p3_canl_set", "offset": 33222, "mem_type": "EEPROM", "label": "Chrono P3 - Vitesse Canalisation Gauche SX (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p4_start_set", "offset": 33224, "mem_type": "EEPROM", "label": "Chrono P4 - Heure Allumage (0..143 x10min, 144=OFF)", "unit": "slot", "min_val": 0, "max_val": 144, "step": 1, "default_val": 144, "category": "CHRONO"},
    {"reg_key": "chrono_p4_stop_set", "offset": 33226, "mem_type": "EEPROM", "label": "Chrono P4 - Heure Arrêt (0..143 x10min, 144=OFF)", "unit": "slot", "min_val": 0, "max_val": 144, "step": 1, "default_val": 144, "category": "CHRONO"},
    {"reg_key": "chrono_p4_days_mask", "offset": 33228, "mem_type": "EEPROM", "label": "Chrono P4 - Masque Jours (Lun=1..Dim=64, 127=Tous)", "unit": "mask", "min_val": 0, "max_val": 127, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p4_power_set", "offset": 33230, "mem_type": "EEPROM", "label": "Chrono P4 - Puissance (1..7)", "unit": "P", "min_val": 1, "max_val": 7, "step": 1, "default_val": 7, "category": "CHRONO"},
    {"reg_key": "chrono_p4_temperature_set", "offset": 33232, "mem_type": "EEPROM", "label": "Chrono P4 - Consigne T° Ambiante (7..41°C)", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "CHRONO"},
    {"reg_key": "chrono_p4_tcanr_set", "offset": 33234, "mem_type": "EEPROM", "label": "Chrono P4 - Consigne T° Canalisation Droite DX", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "CHRONO"},
    {"reg_key": "chrono_p4_tcanl_set", "offset": 33236, "mem_type": "EEPROM", "label": "Chrono P4 - Consigne T° Canalisation Gauche SX", "unit": "°C", "min_val": 7, "max_val": 41, "step": 1, "default_val": 20, "category": "CHRONO"},
    {"reg_key": "chrono_p4_fan_front_set", "offset": 33238, "mem_type": "EEPROM", "label": "Chrono P4 - Vitesse Ventilation Frontale (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p4_canr_set", "offset": 33240, "mem_type": "EEPROM", "label": "Chrono P4 - Vitesse Canalisation Droite DX (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "chrono_p4_canl_set", "offset": 33242, "mem_type": "EEPROM", "label": "Chrono P4 - Vitesse Canalisation Gauche SX (0..8)", "unit": "lvl", "min_val": 0, "max_val": 8, "step": 1, "default_val": 0, "category": "CHRONO"},
    {"reg_key": "clock_hour_set", "offset": 34048, "mem_type": "EEPROM", "label": "Horloge Poêle - Heures (0..23)", "unit": "h", "min_val": 0, "max_val": 23, "step": 1, "default_val": 17, "category": "PR_PARAM"},
    {"reg_key": "clock_minute_set", "offset": 34049, "mem_type": "EEPROM", "label": "Horloge Poêle - Minutes (0..59)", "unit": "min", "min_val": 0, "max_val": 59, "step": 1, "default_val": 42, "category": "PR_PARAM"},
    {"reg_key": "calendar_day_set", "offset": 34050, "mem_type": "EEPROM", "label": "Calendrier Poêle - Jour du mois (1..31)", "unit": "j", "min_val": 1, "max_val": 31, "step": 1, "default_val": 1, "category": "PR_PARAM"},
    {"reg_key": "calendar_month_set", "offset": 34051, "mem_type": "EEPROM", "label": "Calendrier Poêle - Mois (1..12)", "unit": "m", "min_val": 1, "max_val": 12, "step": 1, "default_val": 11, "category": "PR_PARAM"},
    {"reg_key": "calendar_year_set", "offset": 34052, "mem_type": "EEPROM", "label": "Calendrier Poêle - Année (0..99 -> +2000)", "unit": "an", "min_val": 0, "max_val": 99, "step": 1, "default_val": 26, "category": "PR_PARAM"},
    {"reg_key": "calendar_day_week_set", "offset": 34053, "mem_type": "EEPROM", "label": "Calendrier Poêle - Jour semaine (1=Lun..7=Dim)", "unit": "j", "min_val": 1, "max_val": 7, "step": 1, "default_val": 7, "category": "PR_PARAM"},
    {"reg_key": "trim_pellet_pct", "offset": 70, "mem_type": "EEPROM", "label": "Mélange P/A - Tarage Granulés (-5% à +5%)", "unit": "%", "min_val": -5, "max_val": 5, "step": 1, "default_val": 0, "category": "TRIM"},
    {"reg_key": "trim_exhaust_pct", "offset": 71, "mem_type": "EEPROM", "label": "Mélange P/A - Prise d'Air Fumées (-5% à +5%)", "unit": "%", "min_val": -5, "max_val": 5, "step": 1, "default_val": 0, "category": "TRIM"},
    {"reg_key": "cat_service_hours", "offset": 260, "mem_type": "EEPROM", "label": "Compteur Heures avant Entretien CAT", "unit": "h", "min_val": 0, "max_val": 2500, "step": 1, "default_val": 1420, "category": "MAINTENANCE"},
]

TELEMETRY_COLUMNS: Dict[str, str] = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "timestamp": "TEXT NOT NULL",
    "epoch": "INTEGER NOT NULL",
    "comm_mode": "TEXT DEFAULT 'agua_cloud'",
    "status_code": "INTEGER DEFAULT 0",
    "status_label": "TEXT DEFAULT 'OFF (Éteint)'",
    "status_managed": "INTEGER DEFAULT 0",
    "alarm_code": "INTEGER DEFAULT 0",
    "alarm_label": "TEXT DEFAULT 'Aucune (____)'",
    "blackout_popup": "INTEGER DEFAULT 0",
    "popup_flags": "INTEGER DEFAULT 0",
    "popup_door_open": "INTEGER DEFAULT 0",
    "popup_pellet_empty": "INTEGER DEFAULT 0",
    "popup_pellet_open": "INTEGER DEFAULT 0",
    "popup_pa_fault": "INTEGER DEFAULT 0",
    "popup_tcj_fault": "INTEGER DEFAULT 0",
    "popup_tck_fault": "INTEGER DEFAULT 0",
    "temp_ambient": "REAL DEFAULT 19.0",
    "temp_amb_setpoint": "REAL DEFAULT 19.0",
    "temp_fumes": "REAL DEFAULT 185.0",
    "temp_probe_k": "REAL DEFAULT 309.0",
    "temp_board_ntc": "REAL DEFAULT NULL",
    "exhaust_rpm": "INTEGER DEFAULT 2200",
    "exhaust_target_rpm": "INTEGER DEFAULT NULL",
    "depression_pa": "REAL DEFAULT 8.3",
    "flame_light_idx": "INTEGER DEFAULT NULL",
    "glow_plug_on": "INTEGER DEFAULT 0",
    "power_level": "INTEGER DEFAULT 4",
    "power_actual": "INTEGER DEFAULT 4",
    "fun_auto_set": "INTEGER DEFAULT 1",
    "standby_enabled": "INTEGER DEFAULT 0",
    "fun_pwf_set": "INTEGER DEFAULT 0",
    "set_silece_set": "INTEGER DEFAULT 0",
    "fan_front_speed": "INTEGER DEFAULT 3",
    "vent_front_actual": "INTEGER DEFAULT 2",
    "duct_left_temp": "REAL DEFAULT 20.0",
    "duct_left_setpoint": "REAL DEFAULT 20.0",
    "duct_left_speed": "INTEGER DEFAULT 0",
    "duct_left_actual": "INTEGER DEFAULT 0",
    "duct_right_temp": "REAL DEFAULT 20.0",
    "duct_right_setpoint": "REAL DEFAULT 20.0",
    "duct_right_speed": "INTEGER DEFAULT 0",
    "duct_right_actual": "INTEGER DEFAULT 0",
    "thermostat_flags": "INTEGER DEFAULT 64",
    "thermostat_main": "INTEGER DEFAULT 0",
    "thermostat_dx": "INTEGER DEFAULT 0",
    "thermostat_sx": "INTEGER DEFAULT 0",
    "enable_mask_105": "INTEGER DEFAULT 18479",
    "enable_mask_107": "INTEGER DEFAULT 2",
    "chrono_week_enable": "INTEGER DEFAULT 0",
    "chrono_p1_start": "INTEGER DEFAULT 48",
    "chrono_p1_stop": "INTEGER DEFAULT 63",
    "chrono_p1_days": "INTEGER DEFAULT 127",
    "chrono_p1_power": "INTEGER DEFAULT 7",
    "chrono_p1_temp": "REAL DEFAULT 19.0",
    "chrono_p2_start": "INTEGER DEFAULT 126",
    "chrono_p2_stop": "INTEGER DEFAULT 138",
    "chrono_p2_days": "INTEGER DEFAULT 127",
    "chrono_p2_power": "INTEGER DEFAULT 7",
    "chrono_p2_temp": "REAL DEFAULT 19.0",
    "chrono_p3_start": "INTEGER DEFAULT 144",
    "chrono_p3_stop": "INTEGER DEFAULT 144",
    "chrono_p3_days": "INTEGER DEFAULT 0",
    "chrono_p3_power": "INTEGER DEFAULT 7",
    "chrono_p3_temp": "REAL DEFAULT 20.0",
    "chrono_p4_start": "INTEGER DEFAULT 144",
    "chrono_p4_stop": "INTEGER DEFAULT 144",
    "chrono_p4_days": "INTEGER DEFAULT 0",
    "chrono_p4_power": "INTEGER DEFAULT 7",
    "chrono_p4_temp": "REAL DEFAULT 20.0",
    "stove_clock_str": "TEXT DEFAULT '17:42 01/11/2026'",
    "auger_on_time_s": "REAL DEFAULT NULL",
    "auger_cycle_s": "REAL DEFAULT NULL",
    "auger_duty_pct": "REAL DEFAULT NULL",
    "pellet_rate_kgh": "REAL DEFAULT 1.15",
    "pellet_hopper_pct": "REAL DEFAULT 78.0",
    "pellet_daily_kg": "REAL DEFAULT 8.4",
    "trim_pellet_pct": "INTEGER DEFAULT 0",
    "trim_exhaust_pct": "INTEGER DEFAULT 0",
    "cat_service_hours": "REAL DEFAULT 1420",
    "total_work_hours": "REAL DEFAULT 2840.5",
    "ignitions_count": "INTEGER DEFAULT 612",
    "weather_temp_ext": "REAL DEFAULT 10.7",
    "weather_humidity": "REAL DEFAULT 66.0",
    "weather_wind_kmh": "REAL DEFAULT 13.7",
    "runway_days_est": "REAL DEFAULT 15.0",
    "flame_delta_avg": "REAL DEFAULT 0.0",
    "dep_delta_avg": "REAL DEFAULT 0.0",
    "raw_buffer_json": "TEXT DEFAULT '{}'"
}
