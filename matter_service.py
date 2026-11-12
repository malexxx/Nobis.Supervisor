import os
import sys
import shutil
import asyncio
import subprocess
from typing import Dict, Any, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import DATA_DIR
from database import get_settings_dict, get_active_mode, db_log
from clients import borrow_http_client

MATTER_BRIDGE_DIR = os.path.join(BASE_DIR, "matter_bridge")
MATTER_SCRIPT_PATH = os.path.join(MATTER_BRIDGE_DIR, "bridge.mjs")
MATTER_STORAGE_DIR = os.path.join(DATA_DIR, "matter_storage")
MATTER_CONTROL_URL = "http://127.0.0.1:5549"


def _find_node_binary() -> Optional[str]:
    """Recherche l'exécutable Node.js (portable local Windows ou global Linux/Docker)."""
    project_root = os.path.dirname(BASE_DIR)
    candidates = [
        os.path.join(project_root, ".node", "node.exe"),
        os.path.join(BASE_DIR, ".node", "node.exe"),
        shutil.which("node"),
        "/usr/bin/node",
        "/usr/local/bin/node",
        r"C:\Program Files\nodejs\node.exe",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


class MatterBridgeManager:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = asyncio.Lock()
        self._stopping = False

    def is_enabled(self) -> bool:
        s = get_settings_dict()
        return s.get("matter_enabled", "1") not in ("0", "false", "False")

    async def is_control_alive(self) -> bool:
        try:
            async with borrow_http_client(timeout=1.5) as client:
                r = await client.get(f"{MATTER_CONTROL_URL}/status", timeout=1.5)
                return r.status_code == 200
        except Exception:
            return False

    async def ensure_started(self) -> Dict[str, Any]:
        async with self._lock:
            if not self.is_enabled():
                return {"running": False, "enabled": False, "reason": "Pont Matter désactivé dans la configuration"}

            if await self.is_control_alive():
                return await self._fetch_status_unlocked()

            node_bin = _find_node_binary()
            if not node_bin:
                return {
                    "running": False,
                    "enabled": True,
                    "error": "Exécutable Node.js introuvable pour le pont Matter.",
                }

            if not os.path.isfile(MATTER_SCRIPT_PATH):
                return {
                    "running": False,
                    "enabled": True,
                    "error": f"Script Matter introuvable : {MATTER_SCRIPT_PATH}",
                }

            self._stopping = False
            os.makedirs(MATTER_STORAGE_DIR, exist_ok=True)
            settings = get_settings_dict()
            device_name = (settings.get("matter_device_name") or "Poêle Nobis A10").strip()
            matter_port = str(settings.get("matter_port") or "5540").strip()

            env = os.environ.copy()
            env["NOBIS_API_URL"] = os.environ.get("NOBIS_API_URL", "http://127.0.0.1:8080")
            env["MATTER_PORT"] = matter_port
            env["MATTER_CONTROL_PORT"] = "5549"
            env["MATTER_STORAGE_DIR"] = MATTER_STORAGE_DIR
            env["MATTER_DEVICE_NAME"] = device_name

            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

            try:
                self._proc = subprocess.Popen(
                    [node_bin, MATTER_SCRIPT_PATH],
                    cwd=MATTER_BRIDGE_DIR,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            except Exception as e:
                db_log("ERROR", "MATTER", f"Impossible de lancer le pont Matter : {e}", get_active_mode())
                return {"running": False, "enabled": True, "error": str(e)}

            # Attendre que le serveur HTTP local du pont Matter (127.0.0.1:5549) réponde
            for _ in range(25):
                await asyncio.sleep(0.25)
                if await self.is_control_alive():
                    status = await self._fetch_status_unlocked()
                    code = status.get("manualPairingCodeFormatted") or status.get("manualPairingCode") or "---"
                    db_log(
                        "INFO",
                        "MATTER",
                        f"Pont Thermostat Matter '{device_name}' démarré (UDP:{matter_port} | Code d'appairage: {code}).",
                        get_active_mode(),
                    )
                    return status

            return {
                "running": False,
                "enabled": True,
                "error": "Délai dépassé lors de l'initialisation du pont Matter.",
            }

    async def stop(self) -> None:
        self._stopping = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception:
                pass
        self._proc = None

    async def _fetch_status_unlocked(self) -> Dict[str, Any]:
        try:
            async with borrow_http_client(timeout=2.5) as client:
                r = await client.get(f"{MATTER_CONTROL_URL}/status", timeout=2.5)
                if r.status_code == 200:
                    data = r.json()
                    data["enabled"] = self.is_enabled()
                    return data
        except Exception as e:
            return {"running": False, "enabled": self.is_enabled(), "error": str(e)}
        return {"running": False, "enabled": self.is_enabled()}

    async def get_status(self) -> Dict[str, Any]:
        if not self.is_enabled():
            return {"running": False, "enabled": False}
        if not await self.is_control_alive() and not self._stopping:
            return await self.ensure_started()
        return await self._fetch_status_unlocked()

    async def sync_telemetry(self, record: Dict[str, Any]) -> None:
        if not self.is_enabled() or self._stopping:
            return
        try:
            if not await self.is_control_alive():
                await self.ensure_started()
            payload = {
                "temp_ambient": record.get("temp_ambient"),
                "temp_amb_setpoint": record.get("temp_amb_setpoint"),
                "status_code": record.get("status_code"),
                "status_managed": record.get("status_managed"),
            }
            async with borrow_http_client(timeout=2.0) as client:
                await client.post(f"{MATTER_CONTROL_URL}/sync", json=payload, timeout=2.0)
        except Exception:
            pass

    async def factory_reset(self) -> Dict[str, Any]:
        try:
            async with borrow_http_client(timeout=3.0) as client:
                await client.post(f"{MATTER_CONTROL_URL}/factory-reset", timeout=3.0)
        except Exception:
            pass
        await asyncio.sleep(0.5)
        await self.stop()
        if os.path.isdir(MATTER_STORAGE_DIR):
            shutil.rmtree(MATTER_STORAGE_DIR, ignore_errors=True)
        db_log(
            "WARNING",
            "MATTER",
            "Réinitialisation d'usine de l'appairage Matter effectuée (nouveau QR Code généré).",
            get_active_mode(),
        )
        return await self.ensure_started()


matter_manager = MatterBridgeManager()
