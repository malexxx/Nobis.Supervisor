from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class CommandPayload(BaseModel):
    reg_key: str = Field(..., min_length=1, description="Clé du registre Micronova ou RAW_<offset>")
    value: float = Field(..., description="Valeur numérique à écrire")
    offset: Optional[int] = Field(default=None, description="Offset mémoire personnalisé optionnel")


class BatchCommandPayload(BaseModel):
    commands: List[CommandPayload] = Field(..., min_length=1, description="Liste des écritures à exécuter en batch")


class RefillHopperPayload(BaseModel):
    bags_added: float = Field(default=2.0, ge=0.0, le=10.0, description="Nombre de sacs de 15 kg ajoutés (capacité trémie 35 kg)")
    target_pct: float = Field(default=100.0, ge=0.0, le=100.0, description="Niveau cible de la trémie en %")


class SnapshotCreatePayload(BaseModel):
    name: Optional[str] = Field(default="", description="Nom descriptif du snapshot")
    refresh_live: bool = Field(default=False, description="Forcer une lecture temps réel avant capture")


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    comm_mode: Optional[str] = None
    poll_interval_s: Optional[Union[int, float, str]] = None
    agua_email: Optional[str] = None
    agua_password: Optional[str] = None
    local_gateway_url: Optional[str] = None
    weather_city: Optional[str] = None
    weather_lat: Optional[str] = None
    weather_lon: Optional[str] = None
    pellet_bag_price_eur: Optional[Union[float, int, str]] = None
    pellet_bags_stock: Optional[Union[float, int, str]] = None
    pellet_bags_used_season: Optional[Union[float, int, str]] = None
    webhook_url: Optional[str] = None
    matter_enabled: Optional[Union[bool, int, str]] = None
    matter_device_name: Optional[str] = None
    matter_port: Optional[Union[int, str]] = None


class TestConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    comm_mode: str = "simulation"
    agua_email: Optional[str] = None
    agua_password: Optional[str] = None
    local_gateway_url: Optional[str] = None


class SilenceActivatePayload(BaseModel):
    duration_hours: float = Field(default=2.0, ge=0.1, le=24.0, description="Durée limitée du mode Silence ! en heures (défaut 2H)")


class SilenceDeactivatePayload(BaseModel):
    override_commands: Optional[List[CommandPayload]] = Field(default=None, description="Commandes optionnelles à appliquer lors de la désactivation (ex: nouvelle puissance ou ventilation)")


class MatterWebhookPayload(BaseModel):
    action: str = Field(..., description="Action commandée: set_power ou set_temperature")
    state: Optional[str] = Field(default=None, description="État cible: ON ou OFF")
    temperature: Optional[float] = Field(default=None, description="Consigne de température ambiante en °C")


class TestWebhookPayload(BaseModel):
    webhook_url: Optional[str] = Field(default="", description="URL de destination webhook / ntfy")


class ClientErrorPayload(BaseModel):
    severity: str = Field(default="ERROR", description="Niveau de sévérité du log")
    source: str = Field(default="FRONTEND", description="Composant émetteur de l'erreur")
    message: str = Field(default="Erreur interface", description="Détail du message d'erreur")


class InstallerVerifyPayload(BaseModel):
    password: str = Field(..., min_length=1, description="Mot de passe Agua Cloud pour déverrouillage installateur")

