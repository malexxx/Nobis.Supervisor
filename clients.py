import os
import sys
import time
import uuid
import socket
import asyncio
import ipaddress
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional, Tuple, AsyncIterator

import httpx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import AGUA_REG_KEY_ALIASES, HOPPER_CAPACITY_KG
from database import db_log, get_sys_db

# ==============================================================================
# SÉCURITÉ : VALIDATION ANTI-SSRF POUR LES WEBHOOKS, PASSERELLES & NOTIFICATIONS
# ==============================================================================
_FORBIDDEN_HOSTS = {
    "127.0.0.1", "localhost", "localhost.localdomain", "::1", "0.0.0.0", "0",
    "169.254.169.254", "metadata.google.internal", "broadcasthost"
}

_DEFAULT_HARDCODED_DEVICE = "597775E6-C29D-4CB0-ABF8-36F6C8E704ED"
_DEFAULT_HARDCODED_PRODUCT = "CCC78CA4-E701-4976-A981-1F8F61C9D4F7"


def _is_forbidden_webhook_ip(ip_obj: ipaddress._BaseAddress) -> bool:
    """Retourne True si l'adresse IP appartient à un réseau local, privé, loopback ou réservé."""
    mapped = getattr(ip_obj, "ipv4_mapped", None)
    if mapped is not None and _is_forbidden_webhook_ip(mapped):
        return True
    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def _is_loopback_or_metadata_ip(ip_obj: ipaddress._BaseAddress) -> bool:
    """Retourne True si l'IP cible loopback, link-local (metadata), multicast ou unspecified."""
    mapped = getattr(ip_obj, "ipv4_mapped", None)
    if mapped is not None and _is_loopback_or_metadata_ip(mapped):
        return True
    return bool(
        ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    )


def is_safe_webhook_url(url: str) -> bool:
    """Vérifie qu'une URL de webhook est sûre et ne tente pas un SSRF (loopback, IPv6 mappé, RFC1918, métadonnées)."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower().strip().rstrip(".")
        if (
            not hostname
            or hostname in _FORBIDDEN_HOSTS
            or hostname.startswith("127.")
            or hostname.endswith(".local")
            or hostname.endswith(".internal")
            or hostname.endswith(".localhost")
            or hostname.endswith(".localdomain")
            or "127.0.0.1" in hostname
        ):
            return False

        try:
            ip_obj = ipaddress.ip_address(hostname)
            return not _is_forbidden_webhook_ip(ip_obj)
        except ValueError:
            pass

        # Résolution DNS pour bloquer les domaines pointant vers des IP internes (ex: 127.0.0.1.nip.io)
        try:
            infos = socket.getaddrinfo(hostname, None)
            for info in infos:
                raw_addr = str(info[4][0]).split("%")[0]
                if _is_forbidden_webhook_ip(ipaddress.ip_address(raw_addr)):
                    return False
            return bool(infos)
        except socket.gaierror:
            # Environnement hors-ligne ou test unitaire : refuser les hôtes mono-label / numériques ambigus
            if "." not in hostname:
                return False
            stripped = hostname.replace(".", "")
            if stripped.isdigit() or stripped.startswith("0x"):
                return False
            return True
    except Exception:
        return False


def is_safe_gateway_url(url: str) -> bool:
    """Vérifie qu'une URL de passerelle UART LAN est sûre (autorise LAN privé, bloque loopback/metadata/ports internes)."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower().strip().rstrip(".")
        if (
            not hostname
            or hostname in _FORBIDDEN_HOSTS
            or hostname.startswith("127.")
            or hostname.endswith(".localhost")
            or hostname.endswith(".internal")
            or hostname.endswith(".localdomain")
            or "127.0.0.1" in hostname
        ):
            return False
        try:
            port = parsed.port
        except ValueError:
            return False
        if port in (5549, 8080, 1220, 1221):
            return False
        try:
            ip_obj = ipaddress.ip_address(hostname)
            return not _is_loopback_or_metadata_ip(ip_obj)
        except ValueError:
            pass
        try:
            infos = socket.getaddrinfo(hostname, None)
            for info in infos:
                raw_addr = str(info[4][0]).split("%")[0]
                if _is_loopback_or_metadata_ip(ipaddress.ip_address(raw_addr)):
                    return False
        except socket.gaierror:
            pass
        return True
    except Exception:
        return False


# ==============================================================================
# POOL DE CONNEXIONS HTTPX PARTAGÉ (PERSISTANT SUR LE LIFESPAN FASTAPI)
# ==============================================================================
_shared_http_client: Optional[httpx.AsyncClient] = None


async def init_http_client():
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = httpx.AsyncClient(
            timeout=25.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20)
        )


async def close_http_client():
    global _shared_http_client
    if _shared_http_client is not None and not _shared_http_client.is_closed:
        await _shared_http_client.aclose()
    _shared_http_client = None


@asynccontextmanager
async def borrow_http_client(timeout: float = 5.0) -> AsyncIterator[httpx.AsyncClient]:
    """Réutilise le client HTTP partagé (connection pooling) ou en crée un temporaire hors lifespan."""
    if _shared_http_client is not None and not _shared_http_client.is_closed:
        yield _shared_http_client
    else:
        async with httpx.AsyncClient(timeout=timeout) as temp_client:
            yield temp_client


# ==============================================================================
# SERVICE DE NOTIFICATIONS PUSH / WEBHOOK (NTFY.SH, HOME ASSISTANT, DISCORD)
# ==============================================================================
class NotificationService:
    def __init__(self):
        self._last_sent_by_key: Dict[str, float] = {}
        self.cooldown_s: float = 3600.0  # 1h anti-spam par type d'alerte

    async def send_webhook(self, webhook_url: str, title: str, message: str, alert_key: str, mode: str) -> bool:
        """Envoie une notification push vers une URL Webhook (ntfy.sh, Home Assistant, etc.) avec cooldown anti-spam."""
        url = (webhook_url or "").strip()
        if not url or not is_safe_webhook_url(url):
            if url and not is_safe_webhook_url(url):
                db_log("WARNING", "WEBHOOK", f"URL Webhook rejetée par le filtre de sécurité anti-SSRF : {url}", mode)
            return False

        now = time.time()
        last_ts = self._last_sent_by_key.get(alert_key, 0.0)
        if now - last_ts < self.cooldown_s:
            return False

        try:
            async with borrow_http_client(timeout=8.0) as client:
                if "ntfy.sh" in url:
                    resp = await client.post(
                        url,
                        content=message.encode("utf-8"),
                        headers={"Title": title, "Priority": "high", "Tags": "fire,warning"},
                        timeout=8.0
                    )
                else:
                    payload = {
                        "title": title,
                        "message": message,
                        "content": f"**{title}** — {message}",
                        "alert_key": alert_key,
                        "mode": mode
                    }
                    resp = await client.post(url, json=payload, timeout=8.0)
                resp.raise_for_status()
            self._last_sent_by_key[alert_key] = now
            db_log("INFO", "WEBHOOK", f"Notification envoyée [{title}] : {message}", mode)
            return True
        except Exception as e:
            db_log("WARNING", "WEBHOOK", f"Échec envoi notification ({str(e)})", mode)
            return False

    async def evaluate_telemetry_alerts(self, record: Dict[str, Any], settings: Dict[str, str]):
        """Évalue les alarmes et seuils critiques (trémie vide, alarme AL, blackout) et déclenche les webhooks."""
        webhook_url = (settings.get("webhook_url") or "").strip()
        if not webhook_url:
            return
        mode = str(record.get("comm_mode", "agua_cloud"))

        alarm_code = int(record.get("alarm_code") or 0)
        if alarm_code > 0:
            alarm_lbl = record.get("alarm_label", f"AL{alarm_code}")
            await self.send_webhook(
                webhook_url,
                "🚨 Alarme Poêle Nobis A10",
                f"Alarme active détectée : {alarm_lbl} (Code {alarm_code})",
                f"alarm_{alarm_code}",
                mode
            )
        else:
            # Réinitialise les clés d'alarme quand tout revient à la normale
            for k in list(self._last_sent_by_key.keys()):
                if k.startswith("alarm_"):
                    self._last_sent_by_key.pop(k, None)

        hopper_pct = float(record.get("pellet_hopper_pct") if record.get("pellet_hopper_pct") is not None else 100.0)
        if hopper_pct <= 20.0:
            kg_left = round((hopper_pct / 100.0) * HOPPER_CAPACITY_KG, 1)
            await self.send_webhook(
                webhook_url,
                "🌾 Réserve Granulés Basse (< 20%)",
                f"Trémie à {hopper_pct}% (~{kg_left} kg restants sur {int(HOPPER_CAPACITY_KG)} kg). Pensez à recharger.",
                "low_hopper",
                mode
            )
        elif hopper_pct >= 50.0:
            self._last_sent_by_key.pop("low_hopper", None)

        cat_hours = float(record.get("cat_service_hours") if record.get("cat_service_hours") is not None else 1420.0)
        if cat_hours <= 50.0:
            await self.send_webhook(
                webhook_url,
                "🔧 Entretien CAT Nobis A10 Imminent",
                f"Il reste {int(cat_hours)} h avant l'échéance d'entretien programmé CAT.",
                "cat_service",
                mode
            )


notification_service = NotificationService()


# ==============================================================================
# SERVICE MÉTÉO OPEN-METEO (MODÈLE AROME MÉTÉO-FRANCE)
# ==============================================================================
class WeatherService:
    CACHE_TTL_S = 600.0  # 10 minutes (Open-Meteo se rafraîchit toutes les 15 min)

    def __init__(self):
        self.last_weather: Dict[str, Any] = {
            "weather_temp_ext": 10.7,
            "weather_humidity": 66.0,
            "weather_wind_kmh": 13.7,
            "forecast_daily_means": [7.0, 6.0, 8.0, 6.5, 6.5, 7.0, 7.5, 7.0, 6.8, 6.5, 7.0, 7.2, 7.0, 6.8],
            "forecast_daily_mins": [3.5, 2.8, 4.2, 3.0, 3.1, 3.8, 4.0, 3.5, 3.2, 3.0, 3.5, 3.8, 3.5, 3.2],
            "forecast_daily_maxs": [10.5, 9.2, 11.8, 10.0, 9.9, 10.2, 11.0, 10.5, 10.4, 10.0, 10.5, 10.6, 10.5, 10.4],
            "forecast_daily_dates": [],
            "last_update_epoch": 0
        }
        self.last_fetch_ts: float = 0.0
        self.last_coords: tuple = ("", "")

    async def fetch_current(self, lat: str, lon: str, force: bool = False) -> Dict[str, Any]:
        """Récupère les observations et prévisions météo Open-Meteo pour les coordonnées spécifiées (avec cache TTL 10 min)."""
        now = time.time()
        coords = (str(lat).strip(), str(lon).strip())
        if not force and coords == self.last_coords and (now - self.last_fetch_ts) < self.CACHE_TTL_S:
            return self.last_weather

        try:
            f_lat = float(coords[0])
            f_lon = float(coords[1])
            if not (-90.0 <= f_lat <= 90.0 and -180.0 <= f_lon <= 180.0):
                return self.last_weather
        except (ValueError, TypeError):
            return self.last_weather

        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={f_lat}&longitude={f_lon}"
            f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m"
            f"&daily=temperature_2m_mean,temperature_2m_min,temperature_2m_max"
            f"&forecast_days=14"
            f"&timezone=Europe%2FParis"
        )
        try:
            async with borrow_http_client(timeout=3.0) as client:
                resp = await client.get(url, timeout=3.0)
                resp.raise_for_status()
                payload = resp.json()
                data = payload.get("current", {})
                daily = payload.get("daily", {})
                means = [float(x) for x in daily.get("temperature_2m_mean", []) if x is not None]
                mins = [float(x) for x in daily.get("temperature_2m_min", []) if x is not None]
                maxs = [float(x) for x in daily.get("temperature_2m_max", []) if x is not None]
                dates = [str(x) for x in daily.get("time", [])]
                self.last_weather = {
                    "weather_temp_ext": float(data.get("temperature_2m", self.last_weather["weather_temp_ext"])),
                    "weather_humidity": float(data.get("relative_humidity_2m", self.last_weather["weather_humidity"])),
                    "weather_wind_kmh": float(data.get("wind_speed_10m", self.last_weather["weather_wind_kmh"])),
                    "forecast_daily_means": means if means else self.last_weather.get("forecast_daily_means", []),
                    "forecast_daily_mins": mins if mins else self.last_weather.get("forecast_daily_mins", []),
                    "forecast_daily_maxs": maxs if maxs else self.last_weather.get("forecast_daily_maxs", []),
                    "forecast_daily_dates": dates if dates else self.last_weather.get("forecast_daily_dates", []),
                    "last_update_epoch": int(now)
                }
                self.last_fetch_ts = now
                self.last_coords = coords
        except Exception as e:
            # En cas d'erreur 503 ou réseau, on retente après 60s
            self.last_fetch_ts = now - (self.CACHE_TTL_S - 60.0)
            db_log("WARNING", "OPEN_METEO", f"Repli sur cache météo ({str(e)})")
        return self.last_weather


weather_service = WeatherService()


# ==============================================================================
# CLIENT CLOUD AGUA IOT BASÉ SUR py-agua-iot (API_PATH_DEVICE_WRITING = /deviceRequestWriting)
# ==============================================================================
class AguaCloudClient:
    BASE_URL = "https://nobis.agua-iot.com"
    CUSTOMER_CODE = "700700"
    BRAND_ID = "1"

    # Constantes officielles de py_agua_iot/__init__.py
    API_PATH_APP_SIGNUP = "/appSignup"
    API_PATH_LOGIN = "/userLogin"
    API_PATH_DEVICE_LIST = "/deviceList"
    API_PATH_DEVICE_REGISTERS_MAP = "/deviceGetRegistersMap"
    API_PATH_DEVICE_BUFFER_READING = "/deviceGetBufferReading"
    API_PATH_DEVICE_WRITING = "/deviceRequestWriting"
    API_PATH_DEVICE_JOB_STATUS = "/deviceJobStatus"

    def __init__(self):
        self.token_jwt: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.id_device: Optional[str] = "597775E6-C29D-4CB0-ABF8-36F6C8E704ED"
        self.id_product: Optional[str] = "CCC78CA4-E701-4976-A981-1F8F61C9D4F7"
        self._registered_uuids: set = set()
        self.registers_map_by_offset: Dict[int, Dict[str, Any]] = {}
        self.registers_map_by_key: Dict[str, Dict[str, Any]] = {}
        self.working_write_endpoint: Optional[str] = "/deviceRequestWriting"

    def _headers_for_login(self, app_uuid: str) -> Dict[str, str]:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json",
            "Origin": "file://",
            "User-Agent": "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/88.0.4324.181 Mobile Safari/537.36",
            "id_brand": self.BRAND_ID,
            "customer_code": self.CUSTOMER_CODE,
            "local": "true",
            "Authorization": app_uuid
        }

    def _headers_for_api(self, local_flag: str = "false") -> Dict[str, str]:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json",
            "Origin": "file://",
            "User-Agent": "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/88.0.4324.181 Mobile Safari/537.36",
            "id_brand": self.BRAND_ID,
            "customer_code": self.CUSTOMER_CODE,
            "local": local_flag,
            "Authorization": str(self.token_jwt)
        }

    def _parse_and_cache_registers_map(self, raw_map_json: Dict[str, Any]):
        """Extrait offset, bit_data, endianess, mask depuis /deviceGetRegistersMap (principe py-agua-iot)."""
        try:
            maps_list = (
                raw_map_json.get("device_registers_map", {}).get("registers_map", [])
                or raw_map_json.get("registers_map", [])
                or raw_map_json.get("registersMap", [])
            )
            flat_regs: List[Dict[str, Any]] = []
            if isinstance(maps_list, list):
                for entry in maps_list:
                    if isinstance(entry, dict) and isinstance(entry.get("registers"), list):
                        flat_regs.extend(entry["registers"])
                    elif isinstance(entry, dict):
                        flat_regs.append(entry)
            for item in flat_regs:
                if not isinstance(item, dict):
                    continue
                rk = str(item.get("reg_key") or item.get("key") or "")
                off = item.get("offset")
                if off is not None:
                    try:
                        off_int = int(off)
                        if off_int not in self.registers_map_by_offset or item.get("reg_type") == "SET":
                            # Si plusieurs registres SET partagent le même offset avec des masques unitaires par bit
                            # (ex: chrono_pX_day_*_set sur 33168/33188/33208/33228 avec mask=1,2,4..64),
                            # l'entrée indexée par offset doit conserver un masque complet (65535) afin de ne pas
                            # bloquer l'écriture globale du registre (ex: chrono_pX_days_mask).
                            item_for_offset = dict(item)
                            raw_mask = item_for_offset.get("mask") or item_for_offset.get("Mask")
                            if raw_mask is not None and int(raw_mask) not in (255, 65535):
                                item_for_offset["mask"] = 65535 if item_for_offset.get("is_word", True) else 255
                            self.registers_map_by_offset[off_int] = item_for_offset
                    except (ValueError, TypeError):
                        pass
                if rk:
                    self.registers_map_by_key[rk] = item
        except Exception:
            pass

    def _resolve_device_ids(self, settings: Dict[str, str]) -> Tuple[str, str]:
        """Résout id_device et id_product en priorisant l'équipement découvert/configuré sur les valeurs par défaut."""
        cfg_dev = (settings.get("agua_id_device") or "").strip()
        cfg_prod = (settings.get("agua_id_product") or "").strip()
        mem_dev = (self.id_device or "").strip()
        mem_prod = (self.id_product or "").strip()

        if mem_dev and mem_dev != _DEFAULT_HARDCODED_DEVICE:
            id_dev = mem_dev
        elif cfg_dev and cfg_dev != _DEFAULT_HARDCODED_DEVICE:
            id_dev = cfg_dev
        else:
            id_dev = cfg_dev or mem_dev

        if mem_prod and mem_prod != _DEFAULT_HARDCODED_PRODUCT:
            id_prod = mem_prod
        elif cfg_prod and cfg_prod != _DEFAULT_HARDCODED_PRODUCT:
            id_prod = cfg_prod
        else:
            id_prod = cfg_prod or mem_prod
        return id_dev, id_prod

    async def login_and_discover(self, email: str, password: str, app_uuid: str, persist_discovery: bool = True) -> Dict[str, Any]:
        """Authentification auprès de l'API Agua Cloud, obtention du jeton JWT et découverte du poêle."""
        email = (email or "").strip()
        password = (password or "").strip()
        app_uuid = (app_uuid or str(uuid.uuid4())).strip()

        if not email or not password or password == "********" or "@" not in email:
            raise ValueError("Veuillez saisir votre email et votre vrai mot de passe Agua IOT.")

        async with borrow_http_client(timeout=5.0) as client:
            if app_uuid not in self._registered_uuids:
                signup_headers = self._headers_for_login(app_uuid)
                signup_headers.pop("Authorization", None)
                signup_payload = {
                    "phone_type": "Android",
                    "phone_id": app_uuid,
                    "phone_version": "1.0",
                    "language": "fr",
                    "id_app": app_uuid,
                    "push_notification_token": app_uuid,
                    "push_notification_active": False
                }
                r_sig = await client.post(
                    f"{self.BASE_URL}{self.API_PATH_APP_SIGNUP}",
                    headers=signup_headers,
                    json=signup_payload,
                    timeout=5.0
                )
                if r_sig.status_code in (200, 201):
                    self._registered_uuids.add(app_uuid)

            r_login = await client.post(
                f"{self.BASE_URL}{self.API_PATH_LOGIN}",
                headers=self._headers_for_login(app_uuid),
                json={"email": email, "password": password},
                timeout=5.0
            )
            if r_login.status_code >= 400:
                raise RuntimeError(f"HTTP {r_login.status_code} sur /userLogin -> {r_login.text}")

            j_login = r_login.json()
            self.token_jwt = j_login.get("token")
            self.refresh_token = j_login.get("refresh_token")
            if not self.token_jwt:
                raise RuntimeError(f"Token JWT absent de /userLogin : {j_login}")

            r_dev = await client.post(
                f"{self.BASE_URL}{self.API_PATH_DEVICE_LIST}",
                headers=self._headers_for_api(),
                json={},
                timeout=20.0
            )
            if r_dev.status_code == 200:
                dev_json = r_dev.json()
                devices = dev_json.get("device", [])
                if devices and isinstance(devices, list):
                    first_dev = devices[0]
                    self.id_device = str(first_dev.get("id_device") or self.id_device)
                    self.id_product = str(first_dev.get("id_product") or self.id_product)
                    if persist_discovery and self.id_device and self.id_product:
                        try:
                            sconn = get_sys_db()
                            try:
                                sconn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('agua_id_device', ?)", (self.id_device,))
                                sconn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('agua_id_product', ?)", (self.id_product,))
                                sconn.commit()
                            finally:
                                sconn.close()
                        except Exception:
                            pass
            else:
                devices = []
                first_dev = None

            try:
                r_map = await client.post(
                    f"{self.BASE_URL}{self.API_PATH_DEVICE_REGISTERS_MAP}",
                    headers=self._headers_for_api(),
                    json={
                        "id_device": self.id_device,
                        "id_product": self.id_product,
                        "last_update": "2018-06-03T08:59:54.043"
                    },
                    timeout=20.0
                )
                if r_map.status_code == 200:
                    self._parse_and_cache_registers_map(r_map.json())
            except Exception:
                pass

            return {
                "token_ok": True,
                "id_device": self.id_device,
                "id_product": self.id_product,
                "devices_found": len(devices),
                "registers_map_cached": len(self.registers_map_by_offset),
                "write_endpoint": self.API_PATH_DEVICE_WRITING,
                "device_info": first_dev
            }

    async def _wait_job_answer(self, client: httpx.AsyncClient, id_request: str) -> Dict[str, Any]:
        """Attend la complétion asynchrone d'une tâche d'écriture ou lecture sur l'API Agua IOT (polling status)."""
        last_status = "pending"
        for _ in range(15):
            await asyncio.sleep(1.0)
            r_job = await client.get(
                f"{self.BASE_URL}{self.API_PATH_DEVICE_JOB_STATUS}/{id_request}",
                headers=self._headers_for_api(),
                timeout=15.0
            )
            if r_job.status_code == 200:
                j_job = r_job.json()
                last_status = j_job.get("jobAnswerStatus")
                if last_status == "completed":
                    return j_job.get("jobAnswerData", {})
                if last_status in ("error", "failed", "timeout"):
                    err_msg = j_job.get("jobAnswerData", {}).get("message") or f"Statut échec: {last_status}"
                    raise RuntimeError(f"Tâche Agua IOT #{id_request} en erreur ({last_status}): {err_msg}")
        raise TimeoutError(f"Délai d'attente dépassé pour la réponse Agua IOT #{id_request} (statut final: {last_status})")

    async def read_buffer(self, settings: Dict[str, str]) -> Dict[int, float]:
        """Interroge le buffer brut des registres de la carte Micronova via POST /deviceGetBufferReading."""
        app_uuid = settings.get("agua_uuid", "")
        if not self.token_jwt:
            await self.login_and_discover(settings.get("agua_email", ""), settings.get("agua_password", ""), app_uuid)
        id_dev, id_prod = self._resolve_device_ids(settings)
        payload = {"id_device": id_dev, "id_product": id_prod, "BufferId": 1}

        async with borrow_http_client(timeout=5.0) as client:
            resp = await client.post(
                f"{self.BASE_URL}{self.API_PATH_DEVICE_BUFFER_READING}",
                headers=self._headers_for_api(),
                json=payload,
                timeout=5.0
            )
            if resp.status_code == 401:
                await self.login_and_discover(settings.get("agua_email", ""), settings.get("agua_password", ""), app_uuid)
                id_dev, id_prod = self._resolve_device_ids(settings)
                payload["id_device"] = id_dev
                payload["id_product"] = id_prod
                resp = await client.post(
                    f"{self.BASE_URL}{self.API_PATH_DEVICE_BUFFER_READING}",
                    headers=self._headers_for_api(),
                    json=payload,
                    timeout=5.0
                )
            resp.raise_for_status()
            body = resp.json()

            id_req = body.get("idRequest")
            if id_req:
                ans = await self._wait_job_answer(client, str(id_req))
                items = ans.get("Items", [])
                values = ans.get("Values", [])
            else:
                items = body.get("Items", [])
                values = body.get("Values", [])

            clean_buf: Dict[int, float] = {}
            for k, v in zip(items, values):
                if k is not None and v is not None:
                    try:
                        ik = int(k)
                        fv = float(v)
                        # Offsets 70 & 71 : entiers signés INT8 (complément à deux 8 bits : 253 -> -3)
                        if ik in (70, 71) and 128.0 <= fv <= 255.0:
                            fv -= 256.0
                        clean_buf[ik] = fv
                    except (ValueError, TypeError):
                        pass
            return clean_buf

    async def scan_registers_map(self, settings: Dict[str, str]) -> Dict[str, Any]:
        """Scanne la cartographie complète des registres exposée par l'API Agua Cloud (/deviceGetRegistersMap)."""
        app_uuid = settings.get("agua_uuid", "")
        if not self.token_jwt:
            await self.login_and_discover(settings.get("agua_email", ""), settings.get("agua_password", ""), app_uuid)
        id_dev, id_prod = self._resolve_device_ids(settings)
        payload = {"id_device": id_dev, "id_product": id_prod, "last_update": "2018-06-03T08:59:54.043"}
        async with borrow_http_client(timeout=20.0) as client:
            resp = await client.post(
                f"{self.BASE_URL}{self.API_PATH_DEVICE_REGISTERS_MAP}",
                headers=self._headers_for_api(),
                json=payload,
                timeout=20.0
            )
            resp.raise_for_status()
            data = resp.json()
            self._parse_and_cache_registers_map(data)
            return data

    def _resolve_register_write_params(self, offset: int, reg_key: Optional[str] = None) -> Tuple[int, int, str, int]:
        """Résout les métadonnées de registre (offset réel, longueur binaire, endianness, masque) pour l'écriture."""
        meta = None
        matched_by_key = False
        if reg_key:
            for alias in [reg_key] + AGUA_REG_KEY_ALIASES.get(reg_key, []):
                if alias in self.registers_map_by_key:
                    meta = self.registers_map_by_key[alias]
                    matched_by_key = True
                    break
        if not meta:
            meta = self.registers_map_by_offset.get(int(offset))

        byte_offsets = {
            8, 11, 16, 22, 28, 29, 31, 32, 34, 40, 70, 71, 115, 232,
            33162, 33250, 34048, 34049, 34050, 34051, 34052, 34053
        }
        default_bit = 8 if int(offset) in byte_offsets else 16
        default_mask = 255 if default_bit == 8 else 65535

        if not meta:
            return int(offset), default_bit, "L", default_mask

        try:
            real_offset = int(meta.get("offset", offset))
            bit_from_meta = meta.get("bit_data") or meta.get("BitData")
            if bit_from_meta is not None:
                bit_len = int(bit_from_meta)
            elif meta.get("is_word") is not None:
                bit_len = 16 if bool(meta.get("is_word")) else 8
            else:
                bit_len = default_bit

            if meta.get("endianess") or meta.get("Endianess"):
                endian = str(meta.get("endianess") or meta.get("Endianess"))
            elif meta.get("big_endian") is not None:
                endian = "B" if bool(meta.get("big_endian")) else "L"
            else:
                endian = "L"

            full_mask = 255 if bit_len == 8 else 65535
            raw_meta_mask = int(meta.get("mask") or meta.get("Mask") or full_mask)
            if matched_by_key:
                mask_val = raw_meta_mask
            else:
                mask_val = 65535 if raw_meta_mask == 65535 else full_mask
            return real_offset, bit_len, endian, mask_val
        except Exception:
            return int(offset), default_bit, "L", default_mask

    async def write_registers_batch(
        self,
        settings: Dict[str, str],
        items_spec: List[Tuple[int, float, Optional[str]]]
    ) -> Dict[str, Any]:
        """
        Écriture groupée (ou unitaire) Cloud Agua IOT via POST /deviceRequestWriting (protocole RWMSmaster).
        items_spec: liste de tuples (offset, value, reg_key)
        """
        if not items_spec:
            return {"endpoint": self.working_write_endpoint, "response": {}}

        app_uuid = settings.get("agua_uuid", "")
        if not self.token_jwt:
            await self.login_and_discover(settings.get("agua_email", ""), settings.get("agua_password", ""), app_uuid)
        id_dev, id_prod = self._resolve_device_ids(settings)

        bit_data_list: List[int] = []
        endian_list: List[str] = []
        items_list: List[int] = []
        masks_list: List[int] = []
        values_list: List[int] = []

        for offset, value, reg_key in items_spec:
            real_offset, bit_len, endian, mask_val = self._resolve_register_write_params(offset, reg_key)
            raw_int = int(round(value))
            if raw_int < 0:
                raw_int = (1 << bit_len) + raw_int
            bit_data_list.append(int(bit_len))
            endian_list.append(str(endian))
            items_list.append(int(real_offset))
            masks_list.append(int(mask_val))
            values_list.append(int(raw_int))

        payload_rwms = {
            "id_device": id_dev,
            "id_product": id_prod,
            "Protocol": "RWMSmaster",
            "BitData": bit_data_list,
            "Endianess": endian_list,
            "Items": items_list,
            "Masks": masks_list,
            "Values": values_list
        }

        candidate_endpoints = [self.working_write_endpoint] if self.working_write_endpoint else [
            "/deviceRequestWriting",
            "/deviceWriting",
            "/deviceGetBufferWriting",
            "/deviceDataWriting"
        ]

        tried_errors: List[str] = []
        async with borrow_http_client(timeout=5.0) as client:
            for ep in candidate_endpoints:
                try:
                    resp = await client.post(
                        f"{self.BASE_URL}{ep}",
                        headers=self._headers_for_api("false"),
                        json=payload_rwms,
                        timeout=5.0
                    )
                except Exception as ex:
                    tried_errors.append(f"{ep}->err:{str(ex)}")
                    break

                if resp.status_code == 401:
                    try:
                        await self.login_and_discover(settings.get("agua_email", ""), settings.get("agua_password", ""), app_uuid)
                        resp = await client.post(
                            f"{self.BASE_URL}{ep}",
                            headers=self._headers_for_api("false"),
                            json=payload_rwms,
                            timeout=5.0
                        )
                    except Exception as ex:
                        tried_errors.append(f"{ep}->auth_err:{str(ex)}")
                        break

                if resp.status_code in (200, 201, 202):
                    self.working_write_endpoint = ep
                    body = resp.json() if resp.text else {}
                    id_req = body.get("idRequest")
                    if id_req:
                        await self._wait_job_answer(client, str(id_req))
                    return {"endpoint": ep, "response": body}

                tried_errors.append(f"{ep}->{resp.status_code}:{resp.text[:60]}")
                if resp.status_code in (401, 403):
                    break

            raise RuntimeError(f"Échec écriture Agua IOT ({', '.join(tried_errors)})")


agua_client = AguaCloudClient()


# ==============================================================================
# CLIENT PASSERELLE LOCALE UART / RS485 (MICRONOVA LOCAL GATEWAY)
# ==============================================================================
class LocalGatewayClient:
    """Client asynchrone pour la passerelle matérielle locale UART/RS485."""

    def __init__(self, default_timeout: float = 6.0):
        self.default_timeout = default_timeout

    async def test_connection(self, gateway_url: str) -> Dict[str, Any]:
        """Teste la joignabilité de la passerelle locale."""
        url = (gateway_url or "http://192.168.1.150").rstrip("/")
        async with borrow_http_client(timeout=5.0) as client:
            resp = await client.get(f"{url}/status", timeout=5.0)
            resp.raise_for_status()
            return resp.json()

    async def read_buffer(self, gateway_url: str) -> Dict[int, float]:
        """Interroge le buffer d'états de la passerelle locale."""
        url = (gateway_url or "http://192.168.1.150").rstrip("/")
        async with borrow_http_client(timeout=self.default_timeout) as client:
            resp = await client.get(f"{url}/status", timeout=self.default_timeout)
            resp.raise_for_status()
            gw_data = resp.json()
            return {int(k): float(v) for k, v in gw_data.items() if str(k).isdigit()}

    async def write_registers(self, gateway_url: str, commands: List[Dict[str, Any]]) -> None:
        """Envoie une liste d'écritures de registres vers la passerelle locale."""
        url = (gateway_url or "http://192.168.1.150").rstrip("/")
        async with borrow_http_client(timeout=8.0) as client:
            for c in commands:
                resp = await client.post(
                    f"{url}/write",
                    json={"offset": c["offset"], "reg_key": c["reg_key"], "value": c["new_val"]},
                    timeout=8.0
                )
                resp.raise_for_status()


local_gateway_client = LocalGatewayClient()
