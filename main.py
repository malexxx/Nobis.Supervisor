"""
Superviseur Nobis A10 C Light / Style / 2020 / Coax - Point d'entrée principal FastAPI.
Architecture modulaire : routeurs spécialisés (silence, matter, calibration, installer, stove, system),
diffusion temps réel Server-Sent Events (SSE), gestionnaire asynchrone APScheduler et sécurité HTTP.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import json
import logging
import os
import sys
import time
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from analytics import enrich_telemetry_record
from clients import close_http_client, init_http_client
from config import APP_VERSION, DOCKER_VERSION, MODE_DB_PATHS, STATIC_DIR
from database import (
    db_log,
    get_active_mode,
    get_db,
    get_public_settings_dict,
    get_registers_dict,
    get_settings_dict,
    init_all_databases,
    seed_simulation_db_if_empty,
)
from matter_service import matter_manager
from routers.calibration import router as calibration_router
from routers.installer import (
    _failed_auth_attempts,
    _installer_sessions,
    is_installer_authenticated,
    router as installer_router,
)
from routers.matter import router as matter_router
from routers.silence import (
    _auto_expire_silence_job,
    activate_silence_mode,
    deactivate_silence_mode,
    get_silence_state,
    get_silence_status,
    router as silence_router,
    save_silence_state,
)
from routers.stove import router as stove_router
from routers.system import router as system_router
from scheduler import scheduler
from telemetry import (
    collect_telemetry_cycle,
    get_comm_status,
    prune_database_retention,
    sse_subscribers,
)

# Export explicite pour rétro-compatibilité
__all__ = [
    "app",
    "scheduler",
    "activate_silence_mode",
    "deactivate_silence_mode",
    "get_silence_status",
    "get_silence_state",
    "save_silence_state",
    "is_installer_authenticated",
    "_installer_sessions",
    "_failed_auth_attempts",
]

_is_shutting_down = False


def _notify_sse_shutdown():
    """Notifie l'ensemble des clients SSE de l'arrêt imminent du serveur."""
    global _is_shutting_down
    _is_shutting_down = True
    msg = json.dumps({"event": "shutdown", "message": "Arrêt du serveur en cours..."})
    for q in list(sse_subscribers):
        try:
            q.put_nowait(msg)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_shutting_down
    _is_shutting_down = False
    init_all_databases()
    seed_simulation_db_if_empty()
    await init_http_client()

    settings = get_settings_dict()
    try:
        interval_s = max(15, min(3600, int(float(settings.get("poll_interval_s", "120")))))
    except (ValueError, TypeError):
        interval_s = 120

    active_mode = get_active_mode()

    # Démarrage de la boucle de télémétrie périodique et rétention
    scheduler.add_job(collect_telemetry_cycle, "interval", seconds=interval_s, id="telemetry_job", replace_existing=True)
    scheduler.add_job(prune_database_retention, "interval", hours=12, id="retention_prune_job", replace_existing=True)
    scheduler.start()

    # Initialisation de la passerelle Matter
    if matter_manager.is_enabled():
        await matter_manager.ensure_started()

    # Premier cycle de collecte initial
    try:
        await collect_telemetry_cycle(active_mode)
    except Exception as e:
        db_log("ERROR", "STARTUP", f"Échec première collecte au démarrage : {str(e)}", active_mode)

    # Vérification et reprise d'un compte à rebours Silence ! actif
    st = get_silence_state()
    if st.get("active"):
        end_ep = float(st.get("end_epoch") or 0.0)
        rem_s = int(end_ep - time.time())
        if rem_s <= 0:
            db_log("INFO", "SILENCE", "Mode Silence ! expiré pendant l'arrêt du superviseur : restauration immédiate.", active_mode)
            try:
                await deactivate_silence_mode(is_auto_expired=True)
            except Exception as e:
                db_log("ERROR", "SILENCE", f"Échec restauration Silence au démarrage: {e}", active_mode)
        else:
            end_dt = datetime.fromtimestamp(end_ep)
            scheduler.add_job(
                _auto_expire_silence_job,
                "date",
                run_date=end_dt,
                id="silence_timer_job",
                replace_existing=True,
            )
            db_log("INFO", "SILENCE", f"Reprise du compte à rebours Silence ! ({rem_s}s restantes jusqu'à {end_dt.strftime('%H:%M:%S')}).", active_mode)

    try:
        yield
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        _notify_sse_shutdown()
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await matter_manager.stop()
        await close_http_client()


app = FastAPI(
    title="Nobis.Supervisor - Supervision Micronova & Pont Matter",
    version=APP_VERSION,
    lifespan=lifespan,
)


class DbLogHandler(logging.Handler):
    """Redirige les logs ERROR et CRITICAL de Python directement dans app_logs."""
    def emit(self, record: logging.LogRecord):
        if record.levelno >= logging.ERROR:
            try:
                active_mode = get_active_mode()
                msg = self.format(record)
                if "app_logs" not in msg:
                    db_log("ERROR", record.name[:25], msg[:500], active_mode)
            except Exception:
                pass


_db_log_handler = DbLogHandler()
_db_log_handler.setLevel(logging.ERROR)
logging.getLogger().addHandler(_db_log_handler)


_routes_cache = []


def _get_allowed_methods_for_path(path: str) -> set:
    global _routes_cache
    if not _routes_cache:
        def collect(router):
            for route in router.routes:
                if hasattr(route, "routes"):
                    collect(route)
                elif hasattr(route, "original_router"):
                    collect(route.original_router)
                elif hasattr(route, "path_regex") and hasattr(route, "methods"):
                    _routes_cache.append((route.path_regex, route.methods))
        collect(app)

    methods = set()
    for regex, r_methods in _routes_cache:
        if regex.match(path):
            methods.update(r_methods)
    return methods


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    active_mode = get_active_mode()
    err_msg = f"{request.method} {request.url.path} -> {type(exc).__name__}: {str(exc)}"
    db_log("ERROR", "API", err_msg, active_mode)
    if isinstance(exc, OverflowError) or "too large to convert to SQLite" in str(exc):
        return JSONResponse(status_code=422, content={"detail": [{"loc": ["body"], "msg": "Entier hors limites pour SQLite", "type": "overflow_error"}]})
    return JSONResponse(status_code=500, content={"detail": f"Erreur interne : {str(exc)}"})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    active_mode = get_active_mode()
    severity = "ERROR" if exc.status_code >= 400 else "WARNING"
    db_log(severity, "API", f"{request.method} {request.url.path} [{exc.status_code}] : {exc.detail}", active_mode)
    if exc.status_code == 400 and ("parsing the body" in str(exc.detail) or "JSONDecodeError" in str(exc.detail)):
        return JSONResponse(status_code=422, content={"detail": [{"loc": ["body"], "msg": "Erreur décodage JSON", "type": "value_error.jsondecode"}]})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        allowed = _get_allowed_methods_for_path(request.url.path)
        if allowed:
            allow_str = ", ".join(sorted(allowed))
            headers = {
                "Allow": allow_str,
                "Access-Control-Allow-Methods": allow_str,
                "Access-Control-Allow-Headers": "*",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "SAMEORIGIN",
                "Referrer-Policy": "strict-origin-when-cross-origin",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            }
            return Response(status_code=200, headers=headers)

    if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.url.path.startswith("/api/") and request.url.path != "/api/matter/webhook":
        ctype = (request.headers.get("content-type") or "").lower()
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data", "text/plain")):
            return JSONResponse(status_code=403, content={"detail": "Type de contenu de formulaire refusé (protection anti-CSRF)."})
        origin = (request.headers.get("origin") or "").strip()
        if origin:
            if origin == "null":
                return JSONResponse(status_code=403, content={"detail": "Origine Cross-Origin refusée (protection anti-CSRF)."})
            orig_host = urlparse(origin).netloc.lower()
            req_host = (request.headers.get("host") or "").lower()
            if orig_host and req_host and orig_host != req_host:
                return JSONResponse(status_code=403, content={"detail": "Origine Cross-Origin refusée (protection anti-CSRF)."})

    response = await call_next(request)
    if response.status_code == 405:
        allowed = _get_allowed_methods_for_path(request.url.path)
        if allowed:
            response.headers["Allow"] = ", ".join(sorted(allowed))

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self' ws: wss: https:;"
    )
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    return response


# Inclusion des routeurs modulaires
app.include_router(silence_router)
app.include_router(matter_router)
app.include_router(calibration_router)
app.include_router(installer_router)
app.include_router(stove_router)
app.include_router(system_router)

# Fichiers statiques et SPA
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_spa():
    """Sert l'interface utilisateur web monopage (SPA) index.html."""
    index_file = os.path.join(STATIC_DIR, "index.html")
    no_cache = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers=no_cache)
    return HTMLResponse(content="<h1>Nobis.Supervisor API Active</h1>", headers=no_cache)


@app.get("/manifest.json", responses={200: {"content": {"application/manifest+json": {}}}})
@app.head("/manifest.json", include_in_schema=False)
async def serve_manifest():
    """Sert le fichier de configuration PWA manifest.json."""
    manifest_file = os.path.join(STATIC_DIR, "manifest.json")
    if os.path.exists(manifest_file):
        return FileResponse(
            manifest_file,
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    raise HTTPException(status_code=404, detail="manifest.json introuvable")


@app.get("/sw.js", responses={200: {"content": {"application/javascript": {}}}})
@app.head("/sw.js", include_in_schema=False)
async def serve_service_worker():
    """Sert le script du Service Worker PWA sw.js."""
    sw_file = os.path.join(STATIC_DIR, "sw.js")
    if os.path.exists(sw_file):
        return FileResponse(
            sw_file,
            media_type="application/javascript",
            headers={
                "Service-Worker-Allowed": "/",
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )
    raise HTTPException(status_code=404, detail="sw.js introuvable")


@app.get("/health")
async def healthcheck():
    """Point de contrôle de santé (healthcheck) pour Docker et les sondes de disponibilité."""
    mode = get_active_mode()
    return {
        "status": "healthy",
        "version": APP_VERSION,
        "docker_version": DOCKER_VERSION,
        "timestamp": datetime.now().isoformat(),
        "active_mode": mode,
        "active_db": os.path.basename(MODE_DB_PATHS.get(mode, MODE_DB_PATHS["simulation"])),
        "comm_status": get_comm_status(),
    }


MAX_SSE_SUBSCRIBERS = 32


@app.get("/api/events", responses={200: {"content": {"text/event-stream": {}}}, 503: {"description": "Trop de connexions SSE simultanées"}})
async def api_sse_events(request: Request):
    """Flux temps réel Server-Sent Events (SSE) poussant chaque mise à jour de télémétrie."""
    if len(sse_subscribers) >= MAX_SSE_SUBSCRIBERS:
        raise HTTPException(status_code=503, detail="Trop de connexions SSE simultanées.")
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    sse_subscribers.add(queue)

    async def event_generator():
        idle_ticks = 0
        try:
            yield f"data: {json.dumps({'event': 'connected', 'active_mode': get_active_mode(), 'docker_version': DOCKER_VERSION})}\n\n"
            while not _is_shutting_down:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=2.0)
                    if msg is None or _is_shutting_down:
                        break
                    idle_ticks = 0
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    idle_ticks += 1
                    if idle_ticks >= 12:
                        idle_ticks = 0
                        yield ": keepalive\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            sse_subscribers.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/latest")
async def api_latest():
    """Endpoint ultra-léger retournant uniquement le dernier point, les registres et les paramètres."""
    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        latest = conn.execute("SELECT * FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
        latest_dict = dict(latest) if latest else {}
        if latest_dict:
            enrich_telemetry_record(conn, latest_dict)
    finally:
        conn.close()

    stats = {
        "conso_week_kg": latest_dict.get("conso_week_kg", 0.0),
        "conso_month_kg": latest_dict.get("conso_month_kg", 0.0),
    }
    return {
        "version": APP_VERSION,
        "docker_version": DOCKER_VERSION,
        "active_mode": active_mode,
        "active_db_file": os.path.basename(MODE_DB_PATHS.get(active_mode, MODE_DB_PATHS["simulation"])),
        "latest": latest_dict,
        "comm_status": get_comm_status(),
        "settings": get_public_settings_dict(),
        "registers": get_registers_dict(active_mode),
        "consumption_stats": stats,
        "pellet_quality": latest_dict.get("pellet_quality"),
        "exhaust_quality": latest_dict.get("exhaust_quality"),
        "silence_mode": get_silence_status(),
    }


@app.get("/api/dashboard")
async def api_dashboard(range_key: str = Query("24h")):
    """Fournit les séries temporelles échantillonnées pour les graphiques du tableau de bord."""
    active_mode = get_active_mode()
    now_epoch = int(time.time())
    durations = {
        "3h": 3 * 3600,
        "6h": 6 * 3600,
        "24h": 24 * 3600,
        "7j": 7 * 86400,
        "30j": 30 * 86400,
        "3M": 90 * 86400,
    }
    min_epoch = now_epoch - durations.get(range_key, 86400)

    conn = get_db(active_mode)
    try:
        if active_mode == "agua_cloud":
            rows = conn.execute("""
                SELECT * FROM telemetry
                WHERE epoch >= ?
                  AND id NOT BETWEEN 1 AND 27
                  AND NOT (status_code != -1 AND (raw_buffer_json = '{}' OR raw_buffer_json IS NULL))
                ORDER BY epoch ASC
            """, (min_epoch,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM telemetry WHERE epoch >= ? ORDER BY epoch ASC", (min_epoch,)).fetchall()

        latest_row = conn.execute("SELECT * FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
        latest_dict = dict(latest_row) if latest_row else {}
        if latest_dict:
            enrich_telemetry_record(conn, latest_dict)
    finally:
        conn.close()

    total_pts = len(rows)
    max_pts = 600
    if total_pts > max_pts:
        step = total_pts / max_pts
        sampled = [dict(rows[int(i * step)]) for i in range(max_pts)]
    else:
        sampled = [dict(r) for r in rows]

    if len(sampled) == 1:
        dup = dict(sampled[0])
        base_epoch = int(sampled[0].get("epoch") or int(time.time()))
        dup_epoch = base_epoch - 300
        dup["epoch"] = dup_epoch
        dup["timestamp"] = datetime.fromtimestamp(dup_epoch).strftime("%Y-%m-%d %H:%M:%S")
        sampled = [dup, sampled[0]]

    stats = {
        "conso_week_kg": latest_dict.get("conso_week_kg", 0.0),
        "conso_month_kg": latest_dict.get("conso_month_kg", 0.0),
    }

    return {
        "version": APP_VERSION,
        "docker_version": DOCKER_VERSION,
        "active_mode": active_mode,
        "active_db_file": os.path.basename(MODE_DB_PATHS.get(active_mode, MODE_DB_PATHS["simulation"])),
        "latest": latest_dict,
        "comm_status": get_comm_status(),
        "series": sampled,
        "settings": get_public_settings_dict(),
        "registers": get_registers_dict(active_mode),
        "consumption_stats": stats,
        "pellet_quality": latest_dict.get("pellet_quality"),
        "exhaust_quality": latest_dict.get("exhaust_quality"),
        "silence_mode": get_silence_status(),
    }


@app.post("/api/poll-now")
async def api_poll_now():
    """Déclenche une interrogation manuelle immédiate du poêle."""
    record = await collect_telemetry_cycle()
    return {"status": "ok", "latest": record, "comm_status": get_comm_status()}


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    for path, path_item in openapi_schema.get("paths", {}).items():
        for method, op in path_item.items():
            if method.lower() in ("get", "post", "put", "delete", "patch"):
                if "responses" in op:
                    op["responses"].setdefault("400", {"description": "Bad Request / Requête invalide"})
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
