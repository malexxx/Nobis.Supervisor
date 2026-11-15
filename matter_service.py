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

    def _is_proc_running(self) -> bool:
        """Indique si le processus enfant Node.js Matter est actuellement actif."""
        return self._proc is not None and self._proc.poll() is None

    async def _probe_control_once(self, timeout: float = 4.0) -> bool:
        """Sonde l'endpoint HTTP local du pont Matter (127.0.0.1:5549/status)."""
        try:
            async with borrow_http_client(timeout=timeout) as client:
                r = await client.get(f"{MATTER_CONTROL_URL}/status", timeout=timeout)
                return r.status_code == 200
        except Exception:
            return False

    async def is_control_alive(self) -> bool:
        """Vérifie si l'API HTTP interne du pont Matter répond (avec tolérance anti-faux-positif)."""
        if await self._probe_control_once(timeout=4.0):
            return True
        if self._is_proc_running():
            await asyncio.sleep(0.3)
            return await self._probe_control_once(timeout=4.0)
        return False

    async def _terminate_proc_unlocked(self) -> None:
        """Arrête proprement le processus enfant Node.js existant (appelé sous _lock)."""
        proc = self._proc
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.terminate()
                    try:
                        await asyncio.to_thread(proc.wait, timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            await asyncio.to_thread(proc.wait, timeout=2)
                        except Exception:
                            pass
                except Exception:
                    pass
            self._proc = None

    async def ensure_started(self) -> Dict[str, Any]:
        """Garantit qu'un unique pont Matter actif est démarré sans jamais dupliquer le processus."""
        async with self._lock:
            if not self.is_enabled():
                return {"running": False, "enabled": False, "reason": "Pont Matter désactivé dans la configuration"}

            if await self.is_control_alive():
                return await self._fetch_status_unlocked()

            if self._is_proc_running():
                # Le processus existe toujours mais n'a pas encore répondu : ultime vérification avant redémarrage
                if await self._probe_control_once(timeout=4.0):
                    return await self._fetch_status_unlocked()
                await self._terminate_proc_unlocked()
            elif self._proc is not None:
                self._proc = None

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
                if not self._is_proc_running():
                    exit_code = self._proc.poll() if self._proc is not None else -1
                    self._proc = None
                    if await self._probe_control_once(timeout=2.0):
                        # Un pont déjà actif répond sur 127.0.0.1:5549 (sans logguer de faux redémarrage)
                        return await self._fetch_status_unlocked()
                    return {
                        "running": False,
                        "enabled": True,
                        "error": f"Le processus du pont Matter s'est arrêté prématurément (code {exit_code}).",
                    }
                if await self._probe_control_once(timeout=1.5):
                    status = await self._fetch_status_unlocked()
                    if self._is_proc_running():
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
        async with self._lock:
            self._stopping = True
            await self._terminate_proc_unlocked()

    async def _fetch_status_unlocked(self) -> Dict[str, Any]:
        try:
            async with borrow_http_client(timeout=4.0) as client:
                r = await client.get(f"{MATTER_CONTROL_URL}/status", timeout=4.0)
                if r.status_code == 200:
                    data = r.json()
                    data["enabled"] = self.is_enabled()
                    return data
        except Exception as e:
            return {"running": False, "enabled": self.is_enabled(), "error": str(e) or type(e).__name__}
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
