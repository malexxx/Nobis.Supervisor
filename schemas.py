from typing import List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator


class CommandPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reg_key: str = Field(..., min_length=1, pattern=r"^[a-zA-Z0-9_.-]+$", description="Clé du registre Micronova ou RAW_<offset>")
    value: float = Field(..., ge=-1e12, le=1e12, description="Valeur numérique à écrire")
    offset: Optional[int] = Field(default=None, ge=0, le=65535, description="Offset mémoire personnalisé optionnel")

    @field_validator("reg_key")
    @classmethod
    def validate_reg_key(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Paramètre reg_key ne peut pas être vide")
        return s

    @field_validator("value", mode="before")
    @classmethod
    def disallow_bool_value(cls, v):
        if isinstance(v, bool):
            raise ValueError("Boolean values are not allowed for numeric fields")
        return v

    @field_validator("offset", mode="before")
    @classmethod
    def validate_offset(cls, v):
        if isinstance(v, bool):
            raise ValueError("Boolean values are not allowed for integer fields")
        if isinstance(v, float):
            if not v.is_integer():
                raise ValueError("Float value must be an integer")
            return int(v)
        return v


class BatchCommandPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    commands: List[CommandPayload] = Field(..., min_length=1, max_length=64, description="Liste des écritures à exécuter en batch")


class RefillHopperPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    bags_added: float = Field(default=2.0, ge=0.0, le=10.0, description="Nombre de sacs de 15 kg ajoutés (capacité trémie 35 kg)")
    target_pct: float = Field(default=100.0, ge=0.0, le=100.0, description="Niveau cible de la trémie en %")
    level_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0, description="Alias optionnel du niveau cible en %")

    @field_validator("bags_added", "target_pct", "level_pct", mode="before")
    @classmethod
    def disallow_bool_hopper(cls, v):
        if isinstance(v, bool):
            raise ValueError("Boolean values are not allowed for numeric fields")
        return v


class SnapshotCreatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[StrictStr] = Field(default="", description="Nom descriptif du snapshot")
    refresh_live: StrictBool = Field(default=False, description="Forcer une lecture temps réel avant capture")


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    comm_mode: Optional[Literal["simulation", "agua_cloud", "local_gateway"]] = None
    poll_interval_s: Optional[Union[StrictInt, float, StrictStr]] = None
    agua_email: Optional[StrictStr] = None
    agua_password: Optional[StrictStr] = None
    agua_id_device: Optional[StrictStr] = None
    agua_id_product: Optional[StrictStr] = None
    local_gateway_url: Optional[StrictStr] = None
    weather_city: Optional[StrictStr] = None
    weather_lat: Optional[StrictStr] = None
    weather_lon: Optional[StrictStr] = None
    pellet_bag_price_eur: Optional[Union[float, StrictInt, StrictStr]] = None
    pellet_bags_stock: Optional[Union[float, StrictInt, StrictStr]] = None
    pellet_bags_used_season: Optional[Union[float, StrictInt, StrictStr]] = None
    webhook_url: Optional[StrictStr] = None
    matter_enabled: Optional[Union[bool, StrictInt, StrictStr]] = None
    matter_device_name: Optional[StrictStr] = None
    matter_port: Optional[Union[StrictInt, StrictStr]] = None

    @field_validator("poll_interval_s", "pellet_bag_price_eur", "pellet_bags_stock", "pellet_bags_used_season", "matter_port", mode="before")
    @classmethod
    def disallow_bool_numerics(cls, v):
        if isinstance(v, bool):
            raise ValueError("Boolean values are not allowed for numeric fields")
        return v


class TestConnectionPayload(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="ignore")

    comm_mode: Literal["simulation", "agua_cloud", "local_gateway"] = "agua_cloud"
    agua_email: Optional[StrictStr] = None
    agua_password: Optional[StrictStr] = None
    agua_id_device: Optional[StrictStr] = None
    agua_id_product: Optional[StrictStr] = None
    local_gateway_url: Optional[StrictStr] = None


class SilenceActivatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    duration_hours: float = Field(default=2.0, ge=0.1, le=24.0, description="Durée limitée du mode Silence ! en heures (défaut 2H)")

    @field_validator("duration_hours", mode="before")
    @classmethod
    def disallow_bool_silence(cls, v):
        if isinstance(v, bool):
            raise ValueError("Boolean values are not allowed for numeric fields")
        return v


class SilenceDeactivatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    override_commands: Optional[List[CommandPayload]] = Field(default=None, description="Commandes optionnelles à appliquer lors de la désactivation (ex: nouvelle puissance ou ventilation)")


class MatterWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: Literal["set_power", "set_temperature"] = Field(..., description="Action commandée: set_power ou set_temperature")
    state: Optional[Literal["ON", "OFF", "on", "off"]] = Field(default=None, description="État cible: ON ou OFF")
    temperature: Optional[float] = Field(default=None, ge=7.0, le=40.0, description="Consigne de température ambiante en °C")

    @field_validator("temperature", mode="before")
    @classmethod
    def disallow_bool_temp(cls, v):
        if isinstance(v, bool):
            raise ValueError("Boolean values are not allowed for numeric fields")
        return v


class TestWebhookPayload(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="ignore")
    webhook_url: str = Field(..., pattern=r"^https?://[a-zA-Z0-9_.-]+\.[a-zA-Z0-9_.-]+", description="URL de destination webhook / ntfy")


class ClientErrorPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    severity: StrictStr = Field(default="ERROR", description="Niveau de sévérité du log")
    source: StrictStr = Field(default="FRONTEND", description="Composant émetteur de l'erreur")
    message: StrictStr = Field(default="Erreur interface", description="Détail du message d'erreur")


class InstallerVerifyPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    password: str = Field(..., min_length=1, description="Mot de passe Agua Cloud pour déverrouillage installateur")


class StoveSetPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["power", "temp", "fan"] = Field(..., description="Type de consigne: power, temp ou fan")
    zone: Optional[Literal["salon", "front", "sx", "dx"]] = Field(default=None, description="Zone cible pour temp/fan: salon, front, sx ou dx")
    value: float = Field(..., ge=-1e12, le=1e12, description="Valeur de consigne à appliquer")
    label: Optional[StrictStr] = Field(default=None, description="Libellé optionnel (ex: OFF, PS)")

    @field_validator("value", mode="before")
    @classmethod
    def disallow_bool_val(cls, v):
        if isinstance(v, bool):
            raise ValueError("Boolean values are not allowed for numeric fields")
        return v
