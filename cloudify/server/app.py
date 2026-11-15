"""FastAPI application: the JSON API plus the single web UI at /."""

import ipaddress
import socket
import time
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from cloudify import __version__
from cloudify.core.jsonfile import JsonFileError
from cloudify.server.auth import UserStoreError, current_user, valid_user
from cloudify.server.routes import router

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# The interactive docs are served only with `cloudify serve --api-docs`. They load Swagger UI from a
# CDN into the same origin that keeps the login token, so the version is pinned exactly and the files
# are checked by the browser (Subresource Integrity; hashes verified against the npm package).
SWAGGER_UI = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.35.0"
SWAGGER_JS_SRI = "sha384-gjRm0bEPMeTmMh5jT2cHccIwCUZSTd8YC+j0d0sXw0MdGEfScduGpSyF3ZSSLkX9"
SWAGGER_CSS_SRI = "sha384-WlYgdmXYOOBWL5apwjDEZwN+U3HmXSSu5R6V5aGxEcf7z+KuRNwBhoxKVN1i3D5I"
DOCS_HTML = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Cloudify API</title><link rel="icon" href="data:,">
<link rel="stylesheet" href="{SWAGGER_UI}/swagger-ui.css" integrity="{SWAGGER_CSS_SRI}" crossorigin="anonymous">
</head><body><div id="swagger-ui"></div>
<script src="{SWAGGER_UI}/swagger-ui-bundle.js" integrity="{SWAGGER_JS_SRI}" crossorigin="anonymous"></script>
<script src="/api/docs/init.js"></script>
</body></html>
"""
DOCS_INIT_JS = """SwaggerUIBundle({url: '/openapi.json', dom_id: '#swagger-ui', layout: 'BaseLayout',
  deepLinking: true, validatorUrl: null,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset]});
"""

app = FastAPI(title="Cloudify", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

_settings = {"check_host": False, "allowed_hosts": set(), "api_docs": False}

MAX_BODY = 256 * 1024  # bytes; the largest real request (a replica file) is a few KB
# The only requests that may change something without a login token (their bodies are read before
# anyone is signed in); every other one is refused before its body is read when the token is missing
# or invalid. FastAPI itself parses a body before it checks the token.
PUBLIC_WRITES = {"/auth/register", "/auth/login"}


class BodyLimit:
    """Refuse request bodies over MAX_BODY before anything parses them, also without Content-Length:
    otherwise anyone who reaches the port could make the server read and decode any amount of JSON
    before authentication is even checked."""

    def __init__(self, app, limit: int = MAX_BODY):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers") or []:
            if name == b"content-length" and (not value.isdigit() or int(value) > self.limit):
                return await self._reject(send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return  # the client went away
            body = message.get("body", b"")
            size += len(body)
            if size > self.limit:
                return await self._reject(send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send):
        body = b'{"detail":"Request body too large"}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")
DOCS_CSP = (f"default-src 'self'; script-src 'self' {SWAGGER_UI}/; style-src 'self' 'unsafe-inline' {SWAGGER_UI}/; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'")


def configure(allowed_hosts: Iterable[str] = (), api_docs: bool = False, check_host: bool = True):
    """Settings from `cloudify serve` (tests that use the app directly skip the host check)."""
    _settings.update(check_host=check_host, api_docs=api_docs,
                     allowed_hosts={h.strip().lower().rstrip(".") for h in allowed_hosts if h and h.strip()})


def _machine_names() -> set:
    names = {"localhost"}
    for name in (socket.gethostname(), socket.getfqdn()):
        if name:
            name = name.lower().rstrip(".")
            names.update({name, name.split(".")[0], name.split(".")[0] + ".local"})
    return names


_route_hosts = {"at": 0.0, "hosts": set()}


def _route_hostnames() -> set:
    """Public hostnames of managed tunnels (the dashboard may be published through one)."""
    if time.time() - _route_hosts["at"] > 10:
        from cloudify.core.state import StateStore
        hosts = set()
        for info in StateStore().tunnels().values():
            hosts.update(str(r.get("domain", "")).lower() for r in info.get("routes", []))
        _route_hosts.update(at=time.time(), hosts=hosts)
    return _route_hosts["hosts"]


def host_allowed(header: str) -> bool:
    """DNS rebinding guard: a page on another site can point its own DNS name at this
    machine, but it can't make the browser send an IP address, localhost or one of this
    machine's names as Host."""
    host = (header or "").strip().lower()
    host = host[1:host.index("]")] if host.startswith("[") and "]" in host else host.rsplit(":", 1)[0]
    host = host.rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host == "localhost" or host.endswith(".localhost") or host in _settings["allowed_hosts"]:
        return True
    if host in _machine_names():
        return True
    routes = _route_hostnames()
    return host in routes or any(r.startswith("*.") and host.endswith(r[1:]) for r in routes)


def origin_allowed(origin: str, host_header: str) -> bool:
    """CSRF guard for requests that change something: a browser always sends Origin with them. Accept the
    dashboard's own origin (same host and port as the request) and names given with --allowed-host or used
    by the tunnels' routes (the dashboard published through a proxy or tunnel); refuse every other site,
    including `null` (sandboxed frames, local files)."""
    netloc = urlsplit(origin).netloc.lower() if origin and origin != "null" else ""
    if not netloc:
        return False
    if netloc == (host_header or "").strip().lower():
        return True
    name = netloc[1:netloc.index("]")] if netloc.startswith("[") and "]" in netloc else netloc.rsplit(":", 1)[0]
    routes = _route_hostnames()
    return (name in _settings["allowed_hosts"] or name in routes
            or any(r.startswith("*.") and name.endswith(r[1:]) for r in routes))


app.add_middleware(BodyLimit)  # inside the security middleware below: Host/Origin are checked first


@app.middleware("http")
async def security(request: Request, call_next):
    if _settings["check_host"] and not host_allowed(request.headers.get("host", "")):
        host = request.headers.get("host", "")
        return JSONResponse(status_code=400, content={"detail": (
            f"This dashboard can't be opened as '{host}'. Use its IP address or localhost, or start it with "
            f"`cloudify serve --allowed-host {host.rsplit(':', 1)[0] or 'NAME'}`.")})
    origin = request.headers.get("origin")
    writes = request.method not in ("GET", "HEAD", "OPTIONS")
    if writes and origin is not None and not origin_allowed(origin, request.headers.get("host", "")):
        return JSONResponse(status_code=403, content={"detail": "Cross-site request refused"})
    if writes and request.url.path not in PUBLIC_WRITES:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        try:
            signed_in = scheme.lower() == "bearer" and valid_user(token.strip()) is not None
        except UserStoreError as e:
            return JSONResponse(status_code=503, content={"detail": str(e)})
        if not signed_in:
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"},
                                headers={"WWW-Authenticate": "Bearer"})
    response = await call_next(request)
    docs = request.url.path == "/api/docs"
    response.headers.setdefault("Content-Security-Policy", DOCS_CSP if docs else CSP)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


@app.get("/openapi.json", include_in_schema=False)
def openapi_schema():
    if not _settings["api_docs"]:
        raise HTTPException(status_code=404, detail="Not Found")
    return app.openapi()


@app.get("/api/docs", include_in_schema=False)
def api_docs():
    if not _settings["api_docs"]:
        raise HTTPException(status_code=404, detail="Not Found (start the dashboard with --api-docs)")
    return HTMLResponse(DOCS_HTML)


@app.get("/api/docs/init.js", include_in_schema=False)
def api_docs_init():
    if not _settings["api_docs"]:
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(DOCS_INIT_JS, media_type="text/javascript")

_server = None
_shutdown_callback: Optional[Callable] = None


def set_server(server):
    """Give the app the uvicorn server so /shutdown can stop it."""
    global _server
    _server = server


def set_shutdown_callback(callback: Callable):
    global _shutdown_callback
    _shutdown_callback = callback


@app.exception_handler(JsonFileError)
async def json_file_error(request: Request, exc: JsonFileError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(UserStoreError)
async def user_store_error(request: Request, exc: UserStoreError):
    """Fail closed: no login and no registration while the user list can't be read."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def value_error(request: Request, exc: ValueError):
    """Invalid input (e.g. a bad tunnel name) is a client error, not a 500."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Liveness for health checks (Docker): answers without touching files, Cloudflare or cloudflared."""
    return {"status": "ok"}


@app.post("/shutdown")
def shutdown(user: dict = Depends(current_user)):
    if _server:
        _server.should_exit = True
    if _shutdown_callback:
        _shutdown_callback()
    return {"message": "Server shutting down..."}


app.include_router(router)
# Mounted last so API routes take precedence
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
