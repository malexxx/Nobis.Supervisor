"""
Script d'évaluation du débit maximal de requêtes par minute (RPM) sans erreur.
Méthode : Ramping progressif (paliers) avec recherche dichotomique de précision.
"""

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import httpx


@dataclass
class StepResult:
    target_rpm: float
    actual_rpm: float
    total_sent: int
    success_count: int
    error_count: int
    latencies: List[float] = field(default_factory=list)
    errors_by_type: Dict[str, int] = field(default_factory=dict)

    @property
    def error_rate(self) -> float:
        return (self.error_count / self.total_sent * 100.0) if self.total_sent > 0 else 0.0

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


async def send_single_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict,
    payload: Optional[str],
) -> tuple[bool, float, Optional[str]]:
    """Envoie une requête et renvoie (succès, temps_sec, error_reason)."""
    t0 = time.perf_counter()
    try:
        res = await client.request(method=method, url=url, headers=headers, content=payload)
        elapsed = time.perf_counter() - t0
        if 200 <= res.status_code < 400:
            return True, elapsed, None
        else:
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
    duration_sec: float,
    method: str,
    url: str,
    headers: dict,
    payload: Optional[str],
) -> StepResult:
    """Exécute un palier à un rythme régulier (target_rpm) pendant duration_sec."""
    target_rps = target_rpm / 60.0
    interval = 1.0 / target_rps if target_rps > 0 else 0.0
    expected_requests = int(target_rps * duration_sec)

    tasks: List[asyncio.Task] = []
    start_time = time.perf_counter()

    for i in range(expected_requests):
        # Calcul de l'instant théorique pour éviter le drift d'horloge
        scheduled_time = start_time + (i * interval)
        now = time.perf_counter()
        delay = scheduled_time - now
        if delay > 0:
            await asyncio.sleep(delay)

        tasks.append(
            asyncio.create_task(
                send_single_request(client, method, url, headers, payload)
            )
        )

    # Attendre la fin de toutes les requêtes en vol
    results = await asyncio.gather(*tasks, return_exceptions=True)
    total_elapsed = time.perf_counter() - start_time

    success_count = 0
    error_count = 0
    latencies: List[float] = []
    errors_by_type: Dict[str, int] = {}

    for item in results:
        if isinstance(item, Exception):
            err_name = type(item).__name__
            errors_by_type[err_name] = errors_by_type.get(err_name, 0) + 1
            error_count += 1
            continue

        ok, lat, err_reason = item
        latencies.append(lat)
        if ok:
            success_count += 1
        else:
            error_count += 1
            errors_by_type[err_reason] = errors_by_type.get(err_reason, 0) + 1

    total_sent = len(results)
    actual_rpm = (total_sent / total_elapsed) * 60.0 if total_elapsed > 0 else 0.0

    return StepResult(
        target_rpm=target_rpm,
        actual_rpm=actual_rpm,
        total_sent=total_sent,
        success_count=success_count,
        error_count=error_count,
        latencies=latencies,
        errors_by_type=errors_by_type,
    )


async def evaluate_max_rpm(
    url: str,
    method: str = "GET",
    headers: Optional[dict] = None,
    payload: Optional[str] = None,
    start_rpm: float = 300.0,
    multiplier: float = 1.5,
    max_rpm: float = 60000.0,
    stage_duration: float = 10.0,
    timeout: float = 5.0,
    refine: bool = True,
):
    headers = headers or {}
    limits = httpx.Limits(max_connections=2000, max_keepalive_connections=500)
    
    print("\n" + "=" * 70)
    print("🚀 ÉVALUATION DU DÉBIT MAXIMUM PAR MINUTE SANS ERREUR")
    print("=" * 70)
    print(f"Cible         : [{method}] {url}")
    print(f"RPM initial   : {start_rpm:,.0f} req/min ({start_rpm/60:.1f} req/s)")
    print(f"Palier        : {stage_duration:.0f} secondes par test")
    print(f"Multiplicateur: x{multiplier:.2f}")
    print(f"Plafond max   : {max_rpm:,.0f} req/min")
    print("=" * 70 + "\n")

    current_rpm = start_rpm
    last_success_result: Optional[StepResult] = None
    first_failure_result: Optional[StepResult] = None

    async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True) as client:
        # 1. Warmup / Ping initial
        print("🔍 Vérification initiale de la cible...")
        try:
            ok, lat, err = await send_single_request(client, method, url, headers, payload)
            if not ok:
                print(f"❌ Échec de la requête initiale : {err}. Arrêt du test.")
                return
            print(f"✅ Cible accessible (latence: {lat * 1000:.1f} ms).\n")
        except Exception as e:
            print(f"❌ Impossible de joindre l'URL : {e}")
            return

        print(f"{'Palier':<10} | {'Cible RPM':<12} | {'RPS':<8} | {'Envoyées':<9} | {'Erreurs':<9} | {'Lat. Moy':<10} | {'Lat. P95':<10} | {'Statut'}")
        print("-" * 88)

        # 2. Phase d'escalade (Staircase Ramp-up)
        step_idx = 1
        while current_rpm <= max_rpm:
            res = await run_stage(client, current_rpm, stage_duration, method, url, headers, payload)
            status_str = "✅ OK" if res.error_count == 0 else f"❌ {res.error_count} ERR"
            
            print(
                f"{step_idx:<10} | "
                f"{res.target_rpm:<12,.0f} | "
                f"{res.target_rpm/60:<8.1f} | "
                f"{res.total_sent:<9} | "
                f"{res.error_count:<9} | "
                f"{res.avg_latency_ms:<8.1f}ms | "
                f"{res.p95_latency_ms:<8.1f}ms | "
                f"{status_str}"
            )

            if res.error_count > 0:
                first_failure_result = res
                break
            else:
                last_success_result = res
                current_rpm = current_rpm * multiplier
                step_idx += 1
                await asyncio.sleep(1.0)  # Pause de stabilisation

        # 3. Phase de raffinement dichotomique (optionnelle)
        if refine and last_success_result and first_failure_result:
            print("\n🔬 Raffinement du palier limite (recherche dichotomique)...")
            low = last_success_result.target_rpm
            high = first_failure_result.target_rpm

            # Réalisation de 3 itérations pour affiner
            for i in range(3):
                mid = (low + high) / 2.0
                if (high - low) / low < 0.05:  # Tolérance de 5%
                    break
                print(f"   Test dichotomique {i+1}/3 : {mid:,.0f} req/min...", end="", flush=True)
                await asyncio.sleep(1.5)
                res = await run_stage(client, mid, stage_duration, method, url, headers, payload)

                if res.error_count == 0:
                    print(f" -> ✅ Validé (0 erreur, p95={res.p95_latency_ms:.1f}ms)")
                    last_success_result = res
                    low = mid
                else:
                    print(f" -> ❌ {res.error_count} erreurs ({list(res.errors_by_type.keys())[:2]})")
                    first_failure_result = res
                    high = mid

    # Synthèse finale
    print("\n" + "=" * 70)
    print("📊 RÉSULTATS DU BENCHMARK")
    print("=" * 70)
    if last_success_result:
        print(f"🏆 Débit MAXIMAL sans AUCUNE erreur :")
        print(f"   ➤ {last_success_result.target_rpm:,.0f} requêtes / minute")
        print(f"   ➤ {last_success_result.target_rpm / 60:.1f} requêtes / seconde (RPS)")
        print(f"   ➤ Latence moyenne : {last_success_result.avg_latency_ms:.1f} ms")
        print(f"   ➤ Latence 95e centile (p95) : {last_success_result.p95_latency_ms:.1f} ms")
    else:
        print("❌ Aucune charge sans erreur n'a pu être validée (le palier initial a échoué).")

    if first_failure_result:
        print(f"\n⚠️  Point de rupture observé à ~{first_failure_result.target_rpm:,.0f} req/min :")
        for err_type, count in first_failure_result.errors_by_type.items():
            print(f"   - {err_type} : {count} occurrence(s)")
    else:
        print(f"\n🎉 Plafond configuré atteint ({max_rpm:,.0f} req/min) sans rencontrer d'erreur !")
    print("=" * 70 + "\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Mesure le nombre maximum de requêtes par minute (RPM) sans erreur."
    )
    parser.add_argument("--url", default="http://localhost:8000/health", help="URL cible (défaut: http://localhost:8000/health)")
    parser.add_argument("--method", default="GET", choices=["GET", "POST", "PUT", "DELETE", "HEAD"], help="Méthode HTTP")
    parser.add_argument("--start-rpm", type=float, default=600.0, help="RPM de départ (défaut: 600)")
    parser.add_argument("--multiplier", type=float, default=1.5, help="Facteur de multiplication par palier (défaut: 1.5)")
    parser.add_argument("--max-rpm", type=float, default=60000.0, help="Plafond RPM max (défaut: 60000)")
    parser.add_argument("--step-duration", type=float, default=8.0, help="Durée de chaque palier en secondes (défaut: 8s)")
    parser.add_argument("--timeout", type=float, default=5.0, help="Timeout par requête en secondes (défaut: 5s)")
    parser.add_argument("--header", action="append", help="En-têtes sous format 'Nom: Valeur'")
    parser.add_argument("--data", default=None, help="Données brutes / JSON à envoyer (pour POST/PUT)")
    parser.add_argument("--no-refine", action="store_true", help="Désactive l'affinage dichotomique")
    return parser.parse_args()


def main():
    args = parse_args()

    headers = {}
    if args.header:
        for h in args.header:
            if ":" in h:
                k, v = h.split(":", 1)
                headers[k.strip()] = v.strip()

    asyncio.run(
        evaluate_max_rpm(
            url=args.url,
            method=args.method,
            headers=headers,
            payload=args.data,
            start_rpm=args.start_rpm,
            multiplier=args.multiplier,
            max_rpm=args.max_rpm,
            stage_duration=args.step_duration,
            timeout=args.timeout,
            refine=not args.no_refine,
        )
    )


if __name__ == "__main__":
    main()

