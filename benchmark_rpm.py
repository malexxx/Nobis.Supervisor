"""
Script d'évaluation du débit maximal de requêtes par minute (RPM) sans erreur.
- Démarrage à 1 requête par minute.
- Paliers de 5 minutes avec augmentation progressive.
- Gestion de l'authentification (Agua IOT Cloud / API générique) avec prompt interactif sécurisé.
"""

import argparse
import asyncio
import getpass
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import httpx

# Tentative d'import de la base de données locale si disponible
try:
    from database import get_settings_dict
except ImportError:
    get_settings_dict = None


# Constantes Agua IOT Cloud
AGUA_BASE_URL = "https://nobis.agua-iot.com"
AGUA_CUSTOMER_CODE = "700700"
AGUA_BRAND_ID = "1"
AGUA_LOGIN_PATH = "/userLogin"
AGUA_SIGNUP_PATH = "/appSignup"
AGUA_BUFFER_PATH = "/deviceGetBufferReading"
AGUA_DEVICE_LIST_PATH = "/deviceList"


@dataclass
class StepResult:
    target_rpm: float
    duration_s: float
    total_sent: int
    success_count: int
    error_count: int
    latencies: List[float] = field(default_factory=list)
    errors_by_type: Dict[str, int] = field(default_factory=dict)

    @property
    def avg_latency_ms(self) -> float:
        return (sum(self.latencies) / len(self.latencies) * 1000.0) if self.latencies else 0.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)] * 1000.0


async def authenticate_agua_cloud(
    client: httpx.AsyncClient,
    email: str,
    password: str,
    app_uuid: Optional[str] = None
) -> Tuple[str, str, str]:
    """Authentifie l'utilisateur sur le cloud Agua IOT et retourne (token_jwt, id_device, id_product)."""
    app_uuid = app_uuid or str(uuid.uuid4())
    headers_base = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json",
        "Origin": "file://",
        "User-Agent": "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/88.0.4324.181 Mobile Safari/537.36",
        "id_brand": AGUA_BRAND_ID,
        "customer_code": AGUA_CUSTOMER_CODE,
        "local": "true",
    }

    print("🔐 Enregistrement de l'application auprès d'Agua IOT (appSignup)...")
    try:
        await client.post(
            f"{AGUA_BASE_URL}{AGUA_SIGNUP_PATH}",
            headers=headers_base,
            json={
                "phone_type": "Android",
                "phone_id": app_uuid,
                "phone_version": "1.0",
                "language": "fr",
                "id_app": app_uuid,
                "push_notification_token": app_uuid,
                "push_notification_active": False,
            },
            timeout=20.0,
        )
    except Exception:
        pass  # Si déjà enregistré, on continue vers le login

    print("🔑 Authentification utilisateur (userLogin)...")
    login_headers = dict(headers_base)
    login_headers["Authorization"] = app_uuid
    res = await client.post(
        f"{AGUA_BASE_URL}{AGUA_LOGIN_PATH}",
        headers=login_headers,
        json={"email": email, "password": password},
        timeout=25.0,
    )

    if res.status_code >= 400:
        raise RuntimeError(f"Échec de connexion Agua IOT (HTTP {res.status_code}) : {res.text}")

    data = res.json()
    token_jwt = data.get("token")
    if not token_jwt:
        raise RuntimeError(f"Token absent de la réponse : {data}")

    print("✅ Authentification réussie ! Récupération des identifiants poêle (deviceList)...")
    api_headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json",
        "id_brand": AGUA_BRAND_ID,
        "customer_code": AGUA_CUSTOMER_CODE,
        "local": "false",
        "Authorization": str(token_jwt),
    }

    r_dev = await client.post(
        f"{AGUA_BASE_URL}{AGUA_DEVICE_LIST_PATH}",
        headers=api_headers,
        timeout=20.0,
    )

    id_device = ""
    id_product = ""
    if r_dev.status_code == 200:
        try:
            dev_json = r_dev.json()
            devices = dev_json.get("device", []) if isinstance(dev_json, dict) else []
            if devices and isinstance(devices[0], dict):
                id_device = str(devices[0].get("id_device") or "")
                id_product = str(devices[0].get("id_product") or "")
        except Exception:
            pass

    return token_jwt, id_device, id_product


def obtain_credentials() -> Tuple[str, str]:
    """Récupère l'email et le mot de passe depuis la DB locale ou via prompt interactif sécurisé."""
    email = ""
    pwd = ""

    # 1. Vérifier si des identifiants existent dans la base locale
    if get_settings_dict:
        try:
            settings = get_settings_dict()
            db_email = settings.get("agua_email", "").strip()
            db_pwd = settings.get("agua_password", "").strip()
            if db_email and db_pwd:
                choice = input(f"👉 Identifiants trouvés en base locale ({db_email}). Les utiliser ? [O/n] : ").strip().lower()
                if choice in ("", "o", "oui", "y", "yes"):
                    return db_email, db_pwd
        except Exception:
            pass

    # 2. Prompt interactif si nécessaire
    print("\n--- Saisie des identifiants Agua IOT Cloud ---")
    while not email:
        email = input("Email Agua IOT : ").strip()
    while not pwd:
        pwd = getpass.getpass("Mot de passe Agua IOT (saisie masquée) : ").strip()

    return email, pwd


async def send_single_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict,
    payload: Optional[str],
) -> Tuple[bool, float, Optional[str]]:
    """Envoie une requête et mesure le résultat et la latence."""
    t0 = time.perf_counter()
    try:
        res = await client.request(method=method, url=url, headers=headers, content=payload)
        elapsed = time.perf_counter() - t0
        if 200 <= res.status_code < 400:
            return True, elapsed, None
        return False, elapsed, f"HTTP {res.status_code}"
    except httpx.TimeoutException:
        return False, time.perf_counter() - t0, "Timeout"
    except httpx.ConnectError:
        return False, time.perf_counter() - t0, "ConnectError"
    except Exception as e:
        return False, time.perf_counter() - t0, type(e).__name__


async def run_stage(
    client: httpx.AsyncClient,
    target_rpm: float,
    duration_s: float,
    method: str,
    url: str,
    headers: dict,
    payload: Optional[str],
    stage_num: int,
) -> StepResult:
    """Exécute un palier à un rythme régulier (target_rpm) pendant duration_s."""
    _target_rps = target_rpm / 60.0
    interval_s = 60.0 / target_rpm if target_rpm > 0 else 60.0
    total_expected = max(1, int(round((duration_s / 60.0) * target_rpm)))

    print(f"\n▶ PALIER {stage_num} : {target_rpm:.1f} req/min (1 requête toutes les {interval_s:.1f}s pendant {duration_s/60:.1f} min)")
    print(f"  Total prévu pour ce palier : {total_expected} requête(s)")
    print("-" * 75)

    success_count = 0
    error_count = 0
    latencies: List[float] = []
    errors_by_type: Dict[str, int] = {}

    start_stage = time.perf_counter()

    for req_idx in range(1, total_expected + 1):
        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"  [{now_str}] Requête {req_idx}/{total_expected} en cours...", end="", flush=True)

        ok, lat, err_reason = await send_single_request(client, method, url, headers, payload)
        latencies.append(lat)

        if ok:
            success_count += 1
            print(f" -> ✅ OK ({lat * 1000:.0f} ms)")
        else:
            error_count += 1
            errors_by_type[err_reason] = errors_by_type.get(err_reason, 0) + 1
            print(f" -> ❌ ÉCHEC : {err_reason} ({lat * 1000:.0f} ms)")
            print(f"\n⚠️  ERREUR DÉTECTÉE au palier {target_rpm:.1f} req/min ! Arrêt immédiat pour protéger le service.")
            break

        # Attendre le prochain intervalle si ce n'est pas la dernière requête
        if req_idx < total_expected:
            elapsed_since_start = time.perf_counter() - start_stage
            target_time = req_idx * interval_s
            sleep_time = target_time - elapsed_since_start
            if sleep_time > 0:
                # Affichage discret d'attente
                await asyncio.sleep(sleep_time)

    return StepResult(
        target_rpm=target_rpm,
        duration_s=time.perf_counter() - start_stage,
        total_sent=len(latencies),
        success_count=success_count,
        error_count=error_count,
        latencies=latencies,
        errors_by_type=errors_by_type,
    )


async def main_benchmark():
    parser = argparse.ArgumentParser(
        description="Évalue le nombre maximum de requêtes par minute (RPM) sans erreur."
    )
    parser.add_argument("--url", default=None, help="URL cible (par défaut: Cloud Agua IOT /deviceGetBufferReading)")
    parser.add_argument("--start-rpm", type=float, default=1.0, help="RPM de départ (défaut: 1 req/min)")
    parser.add_argument("--increment-rpm", type=float, default=1.0, help="Augmentation du RPM à chaque palier (défaut: +1 req/min)")
    parser.add_argument("--multiplier", type=float, default=None, help="Multiplicateur optionnel (ex: 1.5) au lieu d'un incrément fixe")
    parser.add_argument("--step-minutes", type=float, default=5.0, help="Durée de chaque palier en minutes (défaut: 5 min)")
    parser.add_argument("--max-rpm", type=float, default=60.0, help="Plafond RPM max à tester (défaut: 60)")
    parser.add_argument("--timeout", type=float, default=25.0, help="Timeout par requête en secondes (défaut: 25s)")
    parser.add_argument("--local", action="store_true", help="Tester le serveur Nobis local (http://localhost:8000/api/latest) sans login cloud")

    args = parser.parse_args()

    step_duration_s = args.step_minutes * 60.0
    headers = {}
    payload = None
    method = "POST"

    print("\n" + "=" * 75)
    print("🚀 BENCHMARK DU TAUX DE REQUÊTES MAXIMAL SANS ERREUR")
    print(f"🕒 Démarrage : {args.start_rpm} req/min | Paliers : toutes les {args.step_minutes} minutes")
    print("=" * 75)

    is_cloud = not args.local and (args.url is None or "agua-iot" in args.url)

    async with httpx.AsyncClient(timeout=args.timeout, follow_redirects=True) as client:
        # Configuration pour Agua Cloud si nécessaire
        if is_cloud:
            target_url = args.url or f"{AGUA_BASE_URL}{AGUA_BUFFER_PATH}"
            email, password = obtain_credentials()
            try:
                jwt_token, id_device, id_product = await authenticate_agua_cloud(client, email, password)
            except Exception as e:
                print(f"\n❌ Erreur d'authentification : {e}")
                sys.exit(1)

            # Si id_device/id_product non récupérés, utiliser ceux par défaut en base
            if not id_device and get_settings_dict:
                s = get_settings_dict()
                id_device = s.get("agua_id_device", "597775E6-C29D-4CB0-ABF8-36F6C8E704ED")
                id_product = s.get("agua_id_product", "CCC78CA4-E701-4976-A981-1F8F61C9D4F7")

            headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/json",
                "id_brand": AGUA_BRAND_ID,
                "customer_code": AGUA_CUSTOMER_CODE,
                "local": "false",
                "Authorization": str(jwt_token),
            }
            payload = json.dumps({"id_device": id_device, "id_product": id_product, "BufferId": 1})
            print(f"🎯 Cible Cloud configurée : {target_url} (Device: {id_device})")
        else:
            target_url = args.url or "http://localhost:8000/api/latest"
            method = "GET"
            print(f"🎯 Cible locale/personnalisée configurée : [{method}] {target_url}")

        print("\nTest de connectivité initial...")
        ok, lat, err = await send_single_request(client, method, target_url, headers, payload)
        if not ok:
            print(f"❌ La première requête de test a échoué ({err}). Vérifiez la cible ou vos identifiants.")
            return
        print(f"✅ Connectivité confirmée (latence : {lat * 1000:.0f} ms).\n")

        # Boucle d'escalade des paliers
        current_rpm = args.start_rpm
        stage_num = 1
        last_success: Optional[StepResult] = None
        failure: Optional[StepResult] = None

        while current_rpm <= args.max_rpm:
            res = await run_stage(
                client=client,
                target_rpm=current_rpm,
                duration_s=step_duration_s,
                method=method,
                url=target_url,
                headers=headers,
                payload=payload,
                stage_num=stage_num,
            )

            if res.error_count > 0:
                failure = res
                break

            last_success = res
            print(f"  ✨ Palier {stage_num} réussi (100% succès, latence moy: {res.avg_latency_ms:.0f}ms)")

            # Calcul du palier suivant
            if args.multiplier:
                current_rpm = round(current_rpm * args.multiplier, 1)
            else:
                current_rpm = round(current_rpm + args.increment_rpm, 1)

            stage_num += 1

        # Rapport final
        print("\n" + "=" * 75)
        print("📊 BILAN DU TEST DE CHARGE")
        print("=" * 75)
        if last_success:
            print("🏆 Débit MAXIMUM validé sans AUCUNE erreur :")
            print(f"   ➤ {last_success.target_rpm:.1f} requêtes / minute")
            interval = 60.0 / last_success.target_rpm
            print(f"   ➤ Soit 1 requête toutes les {interval:.1f} secondes")
            print(f"   ➤ Latence moyenne observée : {last_success.avg_latency_ms:.0f} ms")
            print(f"   ➤ Latence P95 : {last_success.p95_latency_ms:.0f} ms")
        else:
            print("❌ Le tout premier palier a rencontré des erreurs.")

        if failure:
            print(f"\n⚠️  Plafond atteint : Erreurs apparues à {failure.target_rpm:.1f} req/min :")
            for err, cnt in failure.errors_by_type.items():
                print(f"   - {err} ({cnt} fois)")
        else:
            print(f"\n🎉 Plafond testé atteint ({args.max_rpm:.1f} req/min) sans aucune erreur.")
        print("=" * 75 + "\n")


if __name__ == "__main__":
    try:
        asyncio.run(main_benchmark())
    except KeyboardInterrupt:
        print("\n\n⏹️ Benchmark interrompu par l'utilisateur.")
