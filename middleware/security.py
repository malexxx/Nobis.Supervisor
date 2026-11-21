"""Middlewares de sécurité HTTP (CSP, HSTS, anti-CSRF, GZip hors SSE, cache statique) et handlers FastAPI."""
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from database import db_log, get_active_mode
from tls_service import get_active_runtime_tls_mode

_routes_cache: list[tuple] = []


class SelectiveGZipMiddleware:
    """Compression HTTP GZip (>= 512 o) contournant explicitement le flux SSE /api/events."""

    def __init__(self, app: ASGIApp, minimum_size: int = 512, compresslevel: int = 6) -> None:
        self.app = app
        self._gzip = GZipMiddleware(app, minimum_size=minimum_size, compresslevel=compresslevel)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Applique GZip sauf sur /api/events et requêtes Accept: text/event-stream."""
        if scope["type"] == "http":
            path = scope.get("path", "")
            accept = next(
                (v.decode("latin-1", errors="ignore").lower() for k, v in scope.get("headers", ()) if k.lower() == b"accept"),
                "",
            )
            if path == "/api/events" or "text/event-stream" in accept:
                await self.app(scope, receive, send)
                return
            await self._gzip(scope, receive, send)
            return
        await self.app(scope, receive, send)

_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "script-src-elem 'self'; "
    "script-src-attr 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self' ws: wss: https:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'self';"
)


def _get_allowed_methods_for_path(app: FastAPI, path: str) -> set[str]:
    global _routes_cache
    if not _routes_cache:
        def _collect(router):
            for route in router.routes:
                if hasattr(route, "routes"):
                    _collect(route)
                elif hasattr(route, "original_router"):
                    _collect(route.original_router)
                elif hasattr(route, "path_regex") and hasattr(route, "methods"):
                    _routes_cache.append((route.path_regex, route.methods))

        _collect(app)

    methods: set[str] = set()
    for regex, r_methods in _routes_cache:
        if regex.match(path):
            methods.update(r_methods)
    return methods


def _check_csrf_protection(request: Request) -> JSONResponse | None:
    """Vérifie Content-Type et Origin pour prévenir les attaques CSRF sur les routes /api/* mutantes."""
    if (
        request.method not in ("POST", "PUT", "DELETE", "PATCH")
        or not request.url.path.startswith("/api/")
        or request.url.path == "/api/matter/webhook"
    ):
        return None

    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data", "text/plain")):
        return JSONResponse(
            status_code=403,
            content={"detail": "Type de contenu de formulaire refusé (protection anti-CSRF)."},
        )

    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return None
    if origin == "null":
        return JSONResponse(
            status_code=403,
            content={"detail": "Origine Cross-Origin refusée (protection anti-CSRF)."},
        )
    orig_host = urlparse(origin).netloc.lower()
    req_host = (request.headers.get("host") or "").lower()
    if orig_host and req_host and orig_host != req_host:
        return JSONResponse(
            status_code=403,
            content={"detail": "Origine Cross-Origin refusée (protection anti-CSRF)."},
        )
    return None


def _apply_security_headers(request: Request, response: Response) -> None:
    """Applique les en-têtes HTTP de sécurité (CSP stricte script-src-elem, HSTS, anti-sniff, cache statique)."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if get_active_runtime_tls_mode() == "letsencrypt":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = _CSP_POLICY
    if request.url.path.startswith("/static/"):
        if request.query_params.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"


def _build_options_response(app: FastAPI, path: str) -> Response | None:
    allowed = _get_allowed_methods_for_path(app, path)
    if not allowed:
        return None
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


async def _handle_global_exception(request: Request, exc: Exception) -> JSONResponse:
    active_mode = get_active_mode()
    err_msg = f"{request.method} {request.url.path} -> {type(exc).__name__}: {str(exc)}"
    db_log("ERROR", "API", err_msg, active_mode)
    if isinstance(exc, OverflowError) or "too large to convert to SQLite" in str(exc):
        return JSONResponse(
            status_code=422,
            content={"detail": [{"loc": ["body"], "msg": "Entier hors limites pour SQLite", "type": "overflow_error"}]},
        )
    return JSONResponse(status_code=500, content={"detail": f"Erreur interne : {str(exc)}"})


async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    active_mode = get_active_mode()
    severity = "ERROR" if exc.status_code >= 400 else "WARNING"
    db_log(severity, "API", f"{request.method} {request.url.path} [{exc.status_code}] : {exc.detail}", active_mode)
    if exc.status_code == 400 and ("parsing the body" in str(exc.detail) or "JSONDecodeError" in str(exc.detail)):
        return JSONResponse(
            status_code=422,
            content={"detail": [{"loc": ["body"], "msg": "Erreur décodage JSON", "type": "value_error.jsondecode"}]},
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def _build_custom_openapi(app: FastAPI):
    def custom_openapi():
        """Génère le schéma OpenAPI enrichi des réponses 400 standardisées."""
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            description=app.description,
            routes=app.routes,
        )
        for path_item in openapi_schema.get("paths", {}).values():
            for method, op in path_item.items():
                if method.lower() in ("get", "post", "put", "delete", "patch") and "responses" in op:
                    op["responses"].setdefault("400", {"description": "Bad Request / Requête invalide"})
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    return custom_openapi


def register_security_and_error_handlers(app: FastAPI) -> None:
    """Enregistre les exception handlers, GZip sélectif hors SSE, middleware sécurité/anti-CSRF et OpenAPI."""
    app.add_exception_handler(Exception, _handle_global_exception)
    app.add_exception_handler(HTTPException, _handle_http_exception)
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=512, compresslevel=6)

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        """Gère OPTIONS, contrôle CSRF anti-cross-origin, en-tête Allow sur 405 et en-têtes de sécurité."""
        if request.method == "OPTIONS":
            opt_resp = _build_options_response(app, request.url.path)
            if opt_resp is not None:
                return opt_resp

        csrf_err = _check_csrf_protection(request)
        if csrf_err is not None:
            return csrf_err

        response = await call_next(request)
        if response.status_code == 405:
            allowed = _get_allowed_methods_for_path(app, request.url.path)
            if allowed:
                response.headers["Allow"] = ", ".join(sorted(allowed))

        _apply_security_headers(request, response)
        return response

    app.openapi = _build_custom_openapi(app)
