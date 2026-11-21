"""Client Cloud Agua IOT (protocole Micronova / py-agua-iot, RWMSmaster)."""
import asyncio
import logging
from typing import Any
import uuid

import httpx

from config import AGUA_REG_KEY_ALIASES
from database import get_sys_db

logger = logging.getLogger("nobis.agua_cloud")


class AguaCloudClient:
    """Client asynchrone Cloud Agua IOT (authentification JWT, découverte produit et protocole RWMSmaster)."""

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
        self.token_jwt: str | None = None
        self.refresh_token: str | None = None
        self.id_device: str | None = ""
        self.id_product: str | None = ""
        self._registered_uuids: set = set()
        self.registers_map_by_offset: dict[int, dict[str, Any]] = {}
        self.registers_map_by_key: dict[str, dict[str, Any]] = {}
        self.working_write_endpoint: str | None = None
        self._auth_lock: asyncio.Lock | None = None

    def _get_auth_lock(self) -> asyncio.Lock:
        if self._auth_lock is None:
            self._auth_lock = asyncio.Lock()
        return self._auth_lock

    def _headers_for_login(self, app_uuid: str) -> dict[str, str]:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json",
            "Origin": "file://",
            "User-Agent": "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/88.0.4324.181 Mobile Safari/537.36",
            "id_brand": self.BRAND_ID,
            "customer_code": self.CUSTOMER_CODE,
            "local": "true",
            "Authorization": app_uuid,
        }

    def _headers_for_api(self, local_flag: str = "false") -> dict[str, str]:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json",
            "Origin": "file://",
            "User-Agent": "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/88.0.4324.181 Mobile Safari/537.36",
            "id_brand": self.BRAND_ID,
            "customer_code": self.CUSTOMER_CODE,
            "local": local_flag,
            "Authorization": str(self.token_jwt),
        }

    @staticmethod
    def _extract_flat_registers(raw_map_json: dict[str, Any]) -> list[dict[str, Any]]:
        maps_list = (
            raw_map_json.get("device_registers_map", {}).get("registers_map", [])
            or raw_map_json.get("registers_map", [])
            or raw_map_json.get("registersMap", [])
        )
        if not isinstance(maps_list, list):
            return []
        flat_regs: list[dict[str, Any]] = []
        for entry in maps_list:
            if not isinstance(entry, dict):
                continue
            if isinstance(entry.get("registers"), list):
                flat_regs.extend(entry["registers"])
            else:
                flat_regs.append(entry)
        return flat_regs

    def _cache_register_entry(self, item: dict[str, Any]) -> None:
        rk = str(item.get("reg_key") or item.get("key") or "")
        off = item.get("offset")
        if off is not None:
            try:
                off_int = int(off)
                if off_int not in self.registers_map_by_offset or item.get("reg_type") == "SET":
                    item_for_offset = dict(item)
                    raw_mask = item_for_offset.get("mask") or item_for_offset.get("Mask")
                    if raw_mask is not None and int(raw_mask) not in (255, 65535):
                        item_for_offset["mask"] = 65535 if item_for_offset.get("is_word", True) else 255
                    self.registers_map_by_offset[off_int] = item_for_offset
            except (ValueError, TypeError):
                pass
        if rk:
            self.registers_map_by_key[rk] = item

    def _parse_and_cache_registers_map(self, raw_map_json: dict[str, Any]) -> None:
        """Extrait offset, bit_data, endianess, mask depuis /deviceGetRegistersMap (principe py-agua-iot)."""
        try:
            for item in self._extract_flat_registers(raw_map_json):
                if isinstance(item, dict):
                    self._cache_register_entry(item)
        except Exception as exc:
            logger.debug("Erreur parsing cartographie registres Agua : %s", exc)

    def _resolve_device_ids(self, settings: dict[str, str]) -> tuple[str, str]:
        """Résout id_device et id_product en priorisant l'équipement découvert en mémoire puis les réglages."""
        cfg_dev = (settings.get("agua_id_device") or "").strip()
        cfg_prod = (settings.get("agua_id_product") or "").strip()
        mem_dev = (self.id_device or "").strip()
        mem_prod = (self.id_product or "").strip()
        return mem_dev or cfg_dev, mem_prod or cfg_prod

    async def login_and_discover(
        self,
        email: str,
        password: str,
        app_uuid: str,
        persist_discovery: bool = True,
    ) -> dict[str, Any]:
        """Authentification auprès de l'API Agua Cloud, obtention du jeton JWT et découverte du poêle."""
        async with self._get_auth_lock():
            return await self._login_and_discover_unlocked(email, password, app_uuid, persist_discovery=persist_discovery)

    async def _ensure_app_signup(self, client: httpx.AsyncClient, app_uuid: str) -> None:
        if app_uuid in self._registered_uuids:
            return
        signup_headers = self._headers_for_login(app_uuid)
        signup_headers.pop("Authorization", None)
        signup_payload = {
            "phone_type": "Android",
            "phone_id": app_uuid,
            "phone_version": "1.0",
            "language": "fr",
            "id_app": app_uuid,
            "push_notification_token": app_uuid,
            "push_notification_active": False,
        }
        r_sig = await client.post(
            f"{self.BASE_URL}{self.API_PATH_APP_SIGNUP}",
            headers=signup_headers,
            json=signup_payload,
            timeout=5.0,
        )
        if r_sig.status_code in (200, 201):
            self._registered_uuids.add(app_uuid)

    def _persist_discovered_device_ids(self) -> None:
        if not (self.id_device and self.id_product):
            return
        try:
            sconn = get_sys_db()
            try:
                sconn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES ('agua_id_device', ?)",
                    (self.id_device,),
                )
                sconn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES ('agua_id_product', ?)",
                    (self.id_product,),
                )
                sconn.commit()
            finally:
                sconn.close()
        except Exception as exc:
            logger.debug("Impossible de persister id_device/id_product Agua : %s", exc)

    async def _discover_devices(
        self,
        client: httpx.AsyncClient,
        persist_discovery: bool,
    ) -> tuple[list[Any], dict[str, Any] | None]:
        r_dev = await client.post(
            f"{self.BASE_URL}{self.API_PATH_DEVICE_LIST}",
            headers=self._headers_for_api(),
            json={},
            timeout=20.0,
        )
        if r_dev.status_code != 200:
            return [], None
        devices = r_dev.json().get("device", [])
        if not (devices and isinstance(devices, list)):
            return [], None
        first_dev = devices[0]
        self.id_device = str(first_dev.get("id_device") or self.id_device)
        self.id_product = str(first_dev.get("id_product") or self.id_product)
        if persist_discovery:
            self._persist_discovered_device_ids()
        return devices, first_dev

    async def _fetch_registers_map_best_effort(self, client: httpx.AsyncClient) -> None:
        try:
            r_map = await client.post(
                f"{self.BASE_URL}{self.API_PATH_DEVICE_REGISTERS_MAP}",
                headers=self._headers_for_api(),
                json={
                    "id_device": self.id_device,
                    "id_product": self.id_product,
                    "last_update": "2018-06-03T08:59:54.043",
                },
                timeout=20.0,
            )
            if r_map.status_code == 200:
                self._parse_and_cache_registers_map(r_map.json())
        except Exception as exc:
            logger.debug("Échec récupération best-effort registers_map Agua : %s", exc)

    async def _login_and_discover_unlocked(
        self,
        email: str,
        password: str,
        app_uuid: str,
        persist_discovery: bool = True,
    ) -> dict[str, Any]:
        import clients as _clients_pkg

        email = (email or "").strip()
        password = (password or "").strip()
        app_uuid = (app_uuid or str(uuid.uuid4())).strip()

        if not email or not password or password == "********" or "@" not in email:
            raise ValueError("Veuillez saisir votre email et votre vrai mot de passe Agua IOT.")

        async with _clients_pkg.borrow_http_client(timeout=5.0) as client:
            await self._ensure_app_signup(client, app_uuid)

            r_login = await client.post(
                f"{self.BASE_URL}{self.API_PATH_LOGIN}",
                headers=self._headers_for_login(app_uuid),
                json={"email": email, "password": password},
                timeout=5.0,
            )
            if r_login.status_code >= 400:
                raise RuntimeError(f"HTTP {r_login.status_code} sur /userLogin -> {r_login.text}")

            j_login = r_login.json()
            self.token_jwt = j_login.get("token")
            self.refresh_token = j_login.get("refresh_token")
            if not self.token_jwt:
                raise RuntimeError(f"Token JWT absent de /userLogin : {j_login}")

            devices, first_dev = await self._discover_devices(client, persist_discovery)
            await self._fetch_registers_map_best_effort(client)

            return {
                "token_ok": True,
                "id_device": self.id_device,
                "id_product": self.id_product,
                "devices_found": len(devices),
                "registers_map_cached": len(self.registers_map_by_offset),
                "write_endpoint": self.API_PATH_DEVICE_WRITING,
                "device_info": first_dev,
            }

    async def _wait_job_answer(self, client: httpx.AsyncClient, id_request: str) -> dict[str, Any]:
        """Attend la complétion asynchrone d'une tâche d'écriture ou lecture sur l'API Agua IOT (polling status)."""
        last_status = "pending"
        for _ in range(15):
            await asyncio.sleep(1.0)
            r_job = await client.get(
                f"{self.BASE_URL}{self.API_PATH_DEVICE_JOB_STATUS}/{id_request}",
                headers=self._headers_for_api(),
                timeout=15.0,
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

    async def read_buffer(self, settings: dict[str, str]) -> dict[int, float]:
        """Interroge le buffer brut des registres de la carte Micronova via POST /deviceGetBufferReading."""
        import clients as _clients_pkg

        app_uuid = settings.get("agua_uuid", "")
        if not self.token_jwt:
            await self.login_and_discover(settings.get("agua_email", ""), settings.get("agua_password", ""), app_uuid)
        id_dev, id_prod = self._resolve_device_ids(settings)
        payload = {"id_device": id_dev, "id_product": id_prod, "BufferId": 1}

        async with _clients_pkg.borrow_http_client(timeout=5.0) as client:
            resp = await client.post(
                f"{self.BASE_URL}{self.API_PATH_DEVICE_BUFFER_READING}",
                headers=self._headers_for_api(),
                json=payload,
                timeout=5.0,
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
                    timeout=5.0,
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

            clean_buf: dict[int, float] = {}
            for k, v in zip(items, values, strict=False):
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

    async def scan_registers_map(self, settings: dict[str, str]) -> dict[str, Any]:
        """Scanne la cartographie complète des registres exposée par l'API Agua Cloud (/deviceGetRegistersMap)."""
        import clients as _clients_pkg

        app_uuid = settings.get("agua_uuid", "")
        if not self.token_jwt:
            await self.login_and_discover(settings.get("agua_email", ""), settings.get("agua_password", ""), app_uuid)
        id_dev, id_prod = self._resolve_device_ids(settings)
        payload = {"id_device": id_dev, "id_product": id_prod, "last_update": "2018-06-03T08:59:54.043"}
        async with _clients_pkg.borrow_http_client(timeout=20.0) as client:
            resp = await client.post(
                f"{self.BASE_URL}{self.API_PATH_DEVICE_REGISTERS_MAP}",
                headers=self._headers_for_api(),
                json=payload,
                timeout=20.0,
            )
            resp.raise_for_status()
            data = resp.json()
            self._parse_and_cache_registers_map(data)
            return data

    _BYTE_OFFSETS = frozenset({
        8, 11, 16, 22, 28, 29, 31, 32, 34, 40, 70, 71, 115, 232,
        33162, 34048, 34049, 34050, 34051, 34052, 34053,
    })

    def _find_register_meta(self, offset: int, reg_key: str | None) -> tuple[dict[str, Any] | None, bool]:
        if reg_key:
            for alias in [reg_key] + AGUA_REG_KEY_ALIASES.get(reg_key, []):
                if alias in self.registers_map_by_key:
                    return self.registers_map_by_key[alias], True
        return self.registers_map_by_offset.get(int(offset)), False

    @staticmethod
    def _extract_bit_len_and_endian(meta: dict[str, Any], default_bit: int) -> tuple[int, str]:
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
        return bit_len, endian

    def _resolve_register_write_params(self, offset: int, reg_key: str | None = None) -> tuple[int, int, str, int]:
        """Résout les métadonnées de registre (offset réel, longueur binaire, endianness, masque) pour l'écriture."""
        meta, matched_by_key = self._find_register_meta(offset, reg_key)
        default_bit = 8 if int(offset) in self._BYTE_OFFSETS else 16
        default_mask = 255 if default_bit == 8 else 65535
        if not meta:
            return int(offset), default_bit, "L", default_mask

        try:
            real_offset = int(meta.get("offset", offset))
            bit_len, endian = self._extract_bit_len_and_endian(meta, default_bit)
            full_mask = 255 if bit_len == 8 else 65535
            raw_meta_mask = int(meta.get("mask") or meta.get("Mask") or full_mask)
            mask_val = raw_meta_mask if matched_by_key else (65535 if raw_meta_mask == 65535 else full_mask)
            return real_offset, bit_len, endian, mask_val
        except Exception:
            return int(offset), default_bit, "L", default_mask

    def _build_rwms_payload(
        self,
        id_dev: str,
        id_prod: str,
        items_spec: list[tuple[int, float, str | None]],
    ) -> dict[str, Any]:
        bit_data_list: list[int] = []
        endian_list: list[str] = []
        items_list: list[int] = []
        masks_list: list[int] = []
        values_list: list[int] = []

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

        return {
            "id_device": id_dev,
            "id_product": id_prod,
            "Protocol": "RWMSmaster",
            "BitData": bit_data_list,
            "Endianess": endian_list,
            "Items": items_list,
            "Masks": masks_list,
            "Values": values_list,
        }

    async def _post_write_endpoint(
        self,
        client: httpx.AsyncClient,
        ep: str,
        payload_rwms: dict[str, Any],
        settings: dict[str, str],
        app_uuid: str,
    ) -> tuple[httpx.Response | None, str | None]:
        try:
            resp = await client.post(
                f"{self.BASE_URL}{ep}",
                headers=self._headers_for_api("false"),
                json=payload_rwms,
                timeout=5.0,
            )
        except Exception as ex:
            return None, f"{ep}->err:{str(ex)}"

        if resp.status_code != 401:
            return resp, None

        try:
            await self.login_and_discover(settings.get("agua_email", ""), settings.get("agua_password", ""), app_uuid)
            resp = await client.post(
                f"{self.BASE_URL}{ep}",
                headers=self._headers_for_api("false"),
                json=payload_rwms,
                timeout=5.0,
            )
            return resp, None
        except Exception as ex:
            return None, f"{ep}->auth_err:{str(ex)}"

    async def write_registers_batch(
        self,
        settings: dict[str, str],
        items_spec: list[tuple[int, float, str | None]],
    ) -> dict[str, Any]:
        """Écriture groupée (ou unitaire) Cloud Agua IOT via POST /deviceRequestWriting (protocole RWMSmaster)."""
        import clients as _clients_pkg

        if not items_spec:
            return {"endpoint": self.working_write_endpoint, "response": {}}

        app_uuid = settings.get("agua_uuid", "")
        if not self.token_jwt:
            await self.login_and_discover(settings.get("agua_email", ""), settings.get("agua_password", ""), app_uuid)
        id_dev, id_prod = self._resolve_device_ids(settings)
        payload_rwms = self._build_rwms_payload(id_dev, id_prod, items_spec)

        candidate_endpoints = (
            [self.working_write_endpoint]
            if self.working_write_endpoint
            else ["/deviceRequestWriting", "/deviceWriting", "/deviceGetBufferWriting", "/deviceDataWriting"]
        )

        tried_errors: list[str] = []
        async with _clients_pkg.borrow_http_client(timeout=5.0) as client:
            for ep in candidate_endpoints:
                resp, err = await self._post_write_endpoint(client, ep, payload_rwms, settings, app_uuid)
                if err is not None:
                    tried_errors.append(err)
                    break

                assert resp is not None
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
