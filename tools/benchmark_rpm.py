"""
Script d'évaluation du débit maximal de requêtes par minute (RPM) sans erreur.
- Démarrage à 1 requête par minute.
- Paliers de 5 minutes avec augmentation progressive.
- Réutilise directement AguaCloudClient pour l'authentification Cloud Agua IOT.
"""

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import getpass
import json
import logging
import os
import sys
import time
import uuid

import httpx

logger = logging.getLogger("nobis.benchmark")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from clients import AguaCloudClient  # noqa: E402

try:
    from database import get_settings_dict
except ImportError:
    get_settings_dict = None


@dataclass
class StepResult:
    target_rpm: float
    duration_s: float
    total_sent: int
    success_count: int
    error_count: int
    latencies: list[float] = field(default_factory=list)
    errors_by_type: dict[str, int] = field(default_factory=dict)

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
    email: str,
    password: str,
    app_uuid: str | None = None,
) -> tuple[AguaCloudClient, str, str]:
    """Authentifie l'utilisateur via AguaCloudClient et retourne (client, id_device, id_product)."""
    agua = AguaCloudClient()
    info = await agua.login_and_discover(
        email=email,
        password=password,
        app_uuid=app_uuid or str(uuid.uuid4()),
        persist_discovery=False,
    )
    return agua, str(info.get("id_device") or ""), str(info.get("id_product") or "")


def obtain_credentials() -> tuple[str, str]:
    """Récupère l'email et le mot de passe depuis la DB locale ou via prompt interactif sécurisé."""
    email = ""
    pwd = ""

    if get_settings_dict:
        try:
            settings = get_settings_dict()
            db_email = settings.get("agua_email", "").strip()
            db_pwd = settings.get("agua_password", "").strip()
            if db_email and db_pwd:
                choice = input(f"👉 Identifiants trouvés en base locale ({db_email}). Les utiliser ? [O/n] : ").strip().lower()
                if choice in ("", "o", "oui", "y", "yes"):
                    return db_email, db_pwd
        except Exception as exc:
            logger.debug("Lecture identifiants DB locale ignorée : %s", exc)

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
    payload: str | None,
) -> tuple[bool, float, str | None]:
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
    payload: str | None,
    stage_num: int,
) -> StepResult:
    """Exécute un palier à un rythme régulier (target_rpm) pendant duration_s."""
    interval_s = 60.0 / target_rpm if target_rpm > 0 else 60.0
    total_expected = max(1, int(round((duration_s / 60.0) * target_rpm)))

    print(f"\n▶ PALIER {stage_num} : {target_rpm:.1f} req/min (1 requête toutes les {interval_s:.1f}s pendant {duration_s/60:.1f} min)")
    print(f"  Total prévu pour ce palier : {total_expected} requête(s)")
    print("-" * 75)

    success_count = 0
    error_count = 0
    latencies: list[float] = []
    errors_by_type: dict[str, int] = {}

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

        if req_idx < total_expected:
            elapsed_since_start = time.perf_counter() - start_stage
            target_time = req_idx * interval_s
            sleep_time = target_time - elapsed_since_start
            if sleep_time > 0:
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


def _parse_benchmark_args():
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
    return parser.parse_args()


async def _prepare_benchmark_target(args) -> tuple[str, str, dict[str, str], str | None]:
    is_cloud = not args.local and (args.url is None or "agua-iot" in args.url)
    if not is_cloud:
        target_url = args.url or "http://localhost:8000/api/latest"
        print(f"🎯 Cible locale/personnalisée configurée : [GET] {target_url}")
        return target_url, "GET", {}, None

    target_url = args.url or f"{AguaCloudClient.BASE_URL}{AguaCloudClient.API_PATH_DEVICE_BUFFER_READING}"
    email, password = obtain_credentials()
    try:
        agua, id_device, id_product = await authenticate_agua_cloud(email, password)
    except Exception as e:
        print(f"\n❌ Erreur d'authentification : {e}")
        sys.exit(1)

    if not id_device and get_settings_dict:
        s = get_settings_dict()
        id_device = s.get("agua_id_device", "")
        id_product = s.get("agua_id_product", "")

    headers = agua._headers_for_api("false")
    payload = json.dumps({"id_device": id_device, "id_product": id_product, "BufferId": 1})
    print(f"🎯 Cible Cloud configurée : {target_url} (Device: {id_device})")
    return target_url, "POST", headers, payload


def _print_benchmark_summary(
    last_success: StepResult | None,
    failure: StepResult | None,
    max_rpm: float,
) -> None:
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
        print(f"\n🎉 Plafond testé atteint ({max_rpm:.1f} req/min) sans aucune erreur.")
    print("=" * 75 + "\n")


async def main_benchmark():
    args = _parse_benchmark_args()
    step_duration_s = args.step_minutes * 60.0

    print("\n" + "=" * 75)
    print("🚀 BENCHMARK DU TAUX DE REQUÊTES MAXIMAL SANS ERREUR")
    print(f"🕒 Démarrage : {args.start_rpm} req/min | Paliers : toutes les {args.step_minutes} minutes")
    print("=" * 75)

    async with httpx.AsyncClient(timeout=args.timeout, follow_redirects=True) as client:
        target_url, method, headers, payload = await _prepare_benchmark_target(args)

        print("\nTest de connectivité initial...")
        ok, lat, err = await send_single_request(client, method, target_url, headers, payload)
        if not ok:
            print(f"❌ La première requête de test a échoué ({err}). Vérifiez la cible ou vos identifiants.")
            return
        print(f"✅ Connectivité confirmée (latence : {lat * 1000:.0f} ms).\n")

        current_rpm = args.start_rpm
        stage_num = 1
        last_success: StepResult | None = None
        failure: StepResult | None = None

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
            current_rpm = round(current_rpm * args.multiplier, 1) if args.multiplier else round(current_rpm + args.increment_rpm, 1)
            stage_num += 1

        _print_benchmark_summary(last_success, failure, args.max_rpm)


if __name__ == "__main__":
    try:
        asyncio.run(main_benchmark())
    except KeyboardInterrupt:
        print("\n\n⏹️ Benchmark interrompu par l'utilisateur.")
