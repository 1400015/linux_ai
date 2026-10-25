"""Loopback-only REST API for the GTK-free core of Linux AI Assistant.

This module exposes a small, read-mostly HTTP API bound to ``127.0.0.1`` so
local tools (scripts, other apps on the same machine, the CLI itself) can
drive the assistant's pure-Python core without GTK:

- ``GET  /api/health`` — liveness probe (no auth required).
- ``GET  /api/stats`` — aggregated token usage (stats_aggregator).
- ``GET  /api/sessions`` — list conversation sessions (history_store).
- ``GET  /api/config`` — non-secret config subset (config_manager).
- ``GET  /api/a11y/contrast`` — WCAG contrast check (a11y).
- ``POST /api/a11y/contrast`` — batch contrast check.
- ``GET  /api/pomodoro`` — current Pomodoro timer state.
- ``POST /api/pomodoro`` — start/pause/skip/reset the timer.
- ``GET  /api/sync/preview`` — plan a sync-bundle import (dry run).
- ``POST /api/sync/import`` — apply a sync bundle (merge-only).
- ``GET  /api/benchmarks`` — last benchmark suite result (cached).
- ``POST /api/benchmarks`` — run the benchmark suite (optionally scaled).

Security model:
- The server binds to ``127.0.0.1`` ONLY. It refuses to bind to ``0.0.0.0``
  or any non-loopback address.
- Every endpoint except ``/api/health`` requires a bearer token. The token
  is generated with ``secrets.token_urlsafe(32)`` and never logged. It can
  be supplied via the ``Authorization: Bearer <token>`` header or the
  ``X-Linux-AI-Token`` header.
- Request bodies are size-capped (default 1 MiB). Unknown routes return 404.
- Methods are restricted per route (GET vs POST). Wrong method → 405.
- The API is read-mostly: the only mutating endpoints are the Pomodoro
  controls and sync import, both of which are local, user-initiated, and
  merge-only (no credentials, no file writes outside config/history).
- No CORS headers are sent; the API is not meant for browsers.

The module is GTK-free and can be embedded in the CLI (``serve`` command)
or run standalone. It uses only the Python standard library (http.server)
plus the project's own pure-Python modules.
"""

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7621
MAX_BODY_BYTES = 1 * 1024 * 1024
MAX_QUERY_LENGTH = 2048
REQUEST_TIMEOUT_SECONDS = 30

# Routes that do not require a bearer token.
PUBLIC_ROUTES = frozenset({"/api/health"})


class LocalAPIError(Exception):
    """An error that maps to an HTTP status code."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class APIContext:
    """Shared state injected into every request handler.

    All fields are optional so tests can build a context with only the
    pieces they exercise. The production CLI wires the real ones.
    """

    token: str = ""
    config: Any = None  # ConfigManager
    ai_client: Any = None  # AIClient
    history_store: Any = None  # HistoryStore
    pomodoro: Any = None  # PomodoroTimer
    benchmark_cache: Dict[str, Any] = field(default_factory=dict)
    benchmark_lock: threading.Lock = field(default_factory=threading.Lock)
    started_at: float = field(default_factory=time.time)
    request_count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record_request(self) -> None:
        with self.lock:
            self.request_count += 1


@dataclass(frozen=True)
class Route:
    """A single API route."""

    method: str
    path: str
    handler: Callable[["APIContext", Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
    auth_required: bool = True


def _ok(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def _err(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": message}


# ---------------------------------------------------------------------------
# Route handlers. Each receives (ctx, query_params, body) and returns a
# JSON-serializable dict. They raise LocalAPIError for HTTP errors.
# ---------------------------------------------------------------------------

def handle_health(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    with ctx.lock:
        count = ctx.request_count
    return _ok({
        "status": "ok",
        "uptime_seconds": round(time.time() - ctx.started_at, 3),
        "requests_served": count,
    })


def handle_stats(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    if ctx.ai_client is None:
        raise LocalAPIError(503, "AI client not available")
    from .stats_aggregator import aggregate_usage, format_summary_text
    usage = ctx.ai_client.get_token_usage()
    summary = aggregate_usage(usage)
    result = {
        "providers": {
            p.provider: {
                "input_tokens": p.input_tokens,
                "output_tokens": p.output_tokens,
                "total_tokens": p.total_tokens,
                "requests": p.requests,
            }
            for p in summary.providers
        },
        "total_tokens": summary.total_tokens,
        "input_tokens": summary.total_input,
        "output_tokens": summary.total_output,
        "provider_count": summary.provider_count,
        "text": format_summary_text(summary),
    }
    if summary.most_used_provider:
        result["most_used"] = {
            "provider": summary.most_used_provider,
            "share": summary.provider_share(summary.most_used_provider),
        }
    return _ok(result)


def handle_sessions(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    if ctx.history_store is None:
        raise LocalAPIError(503, "History store not available")
    include_archived = query.get("archived", ["false"])[0].lower() in ("1", "true", "yes")
    sessions = ctx.history_store.list_sessions(include_archived=include_archived)
    return _ok({"sessions": sessions, "count": len(sessions)})


def handle_config(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    if ctx.config is None:
        raise LocalAPIError(503, "Config manager not available")
    # Return only non-secret config: no API keys, no encryption state.
    safe = {
        "app": {
            "theme": ctx.config.get("app.theme", "dark"),
            "language": ctx.config.get("app.language", ""),
            "expert_mode": ctx.config.get("app.expert_mode", False),
            "always_on_top": ctx.config.get("app.always_on_top", True),
            "dock_mode": ctx.config.get("app.dock_mode", "float"),
            "dock_edge": ctx.config.get("app.dock_edge", "right"),
        },
        "assistance": {
            "mode": ctx.config.get_assistance_mode(),
        },
        "local_model": ctx.config.get_local_model_settings(),
        "context": {
            "max_messages": ctx.config.get("context.max_messages", 20),
            "max_chars": ctx.config.get("context.max_chars", 12000),
        },
        "pomodoro": {
            "enabled": ctx.config.get("pomodoro.enabled", False),
            "work_seconds": ctx.config.get("pomodoro.work_seconds", 1500),
            "short_break_seconds": ctx.config.get("pomodoro.short_break_seconds", 300),
            "long_break_seconds": ctx.config.get("pomodoro.long_break_seconds", 900),
            "cycles_before_long_break": ctx.config.get("pomodoro.cycles_before_long_break", 4),
        },
        "ui": {
            "font_family": ctx.config.get("ui.font_family", "Monospace"),
            "font_size": ctx.config.get("ui.font_size", 12),
        },
    }
    # Defensive: never leak a key that looks like a credential.
    flat = json.dumps(safe)
    if "api_key" in flat or "secret" in flat.lower():
        raise LocalAPIError(500, "Config response would leak a credential; refusing")
    return _ok(safe)


def handle_contrast_get(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    from .a11y import check_contrast
    fg = query.get("fg", [None])[0]
    bg = query.get("bg", [None])[0]
    if not fg or not bg:
        raise LocalAPIError(400, "Query parameters 'fg' and 'bg' are required (hex colors)")
    report = check_contrast(fg, bg)
    if report is None:
        raise LocalAPIError(400, "Invalid hex color(s)")
    return _ok({
        "foreground": fg,
        "background": bg,
        "ratio": round(report.ratio, 3),
        "level": report.level,
        "aa_normal": report.aa_normal,
        "aa_large": report.aa_large,
        "aaa_normal": report.aaa_normal,
        "aaa_large": report.aaa_large,
    })


def handle_contrast_post(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    from .a11y import check_contrast
    pairs = body.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise LocalAPIError(400, "Body must contain a non-empty 'pairs' list")
    if len(pairs) > 100:
        raise LocalAPIError(400, "At most 100 pairs per request")
    results = []
    for pair in pairs:
        if not isinstance(pair, dict):
            raise LocalAPIError(400, "Each pair must be an object with 'fg' and 'bg'")
        fg, bg = pair.get("fg"), pair.get("bg")
        report = check_contrast(fg, bg) if fg and bg else None
        if report is None:
            results.append({"fg": fg, "bg": bg, "error": "invalid color"})
        else:
            results.append({
                "fg": fg, "bg": bg,
                "ratio": round(report.ratio, 3),
                "level": report.level,
            })
    return _ok({"results": results, "count": len(results)})


def handle_pomodoro_get(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    if ctx.pomodoro is None:
        raise LocalAPIError(503, "Pomodoro timer not available")
    timer = ctx.pomodoro
    return _ok({
        **timer.to_dict(),
        "remaining_seconds": round(timer.remaining(), 1),
        "progress": round(timer.progress(), 4),
    })


def handle_pomodoro_post(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    if ctx.pomodoro is None:
        raise LocalAPIError(503, "Pomodoro timer not available")
    action = body.get("action")
    if action not in ("start", "pause", "skip", "reset"):
        raise LocalAPIError(400, "Body must contain 'action' (start|pause|skip|reset)")
    timer = ctx.pomodoro
    if action == "start":
        timer.start()
    elif action == "pause":
        timer.pause()
    elif action == "skip":
        timer.skip()
    elif action == "reset":
        timer.reset()
    return _ok({
        **timer.to_dict(),
        "remaining_seconds": round(timer.remaining(), 1),
        "progress": round(timer.progress(), 4),
    })


def handle_sync_preview(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    from .sync_bundle import parse_bundle, plan_import, SyncBundleError
    bundle_text = body.get("bundle")
    if not isinstance(bundle_text, str):
        raise LocalAPIError(400, "Body must contain 'bundle' (JSON string)")
    try:
        bundle = parse_bundle(bundle_text)
    except SyncBundleError as error:
        raise LocalAPIError(400, str(error)) from error
    current_config = ctx.config.config if ctx.config is not None else {}
    existing_ids: List[str] = []
    if ctx.history_store is not None:
        existing_ids = [s["id"] for s in ctx.history_store.list_sessions(include_archived=True)]
    plan = plan_import(bundle, current_config, existing_ids)
    return _ok({
        "settings_to_apply": plan.settings_to_apply,
        "settings_skipped": plan.settings_skipped,
        "sessions_to_add": len(plan.sessions_to_add),
        "sessions_to_update": plan.sessions_to_update,
        "sessions_skipped": plan.sessions_skipped,
        "total_changes": plan.total_changes,
    })


def handle_sync_import(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    from .sync_bundle import (
        SyncBundleError, apply_settings, merge_sessions, parse_bundle,
    )
    if ctx.config is None or ctx.history_store is None:
        raise LocalAPIError(503, "Config or history store not available")
    bundle_text = body.get("bundle")
    if not isinstance(bundle_text, str):
        raise LocalAPIError(400, "Body must contain 'bundle' (JSON string)")
    try:
        bundle = parse_bundle(bundle_text)
    except SyncBundleError as error:
        raise LocalAPIError(400, str(error)) from error
    applied = apply_settings(bundle, ctx.config.config)
    ctx.config.flush()
    document = ctx.history_store._snapshot()
    merged = merge_sessions(bundle, document.get("sessions", []))
    if merged:
        ctx.history_store.replace_sessions(merged)
    return _ok({
        "applied_settings": len(applied),
        "sessions": len(merged),
    })


def handle_benchmarks_get(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    with ctx.benchmark_lock:
        cached = ctx.benchmark_cache.get("last_result")
    if cached is None:
        return _ok({"cached": False, "message": "No benchmark run yet; POST to /api/benchmarks"})
    return _ok({"cached": True, "result": cached})


def handle_benchmarks_post(ctx: APIContext, query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    from .benchmarks import run_default_suite
    scale = body.get("scale", 1.0)
    if not isinstance(scale, (int, float)) or isinstance(scale, bool) or scale <= 0 or scale > 10:
        raise LocalAPIError(400, "'scale' must be a number in (0, 10]")
    with ctx.benchmark_lock:
        suite = run_default_suite(repeat_scale=float(scale))
        result = suite.to_dict()
        ctx.benchmark_cache["last_result"] = result
    return _ok(result)


ROUTES: Tuple[Route, ...] = (
    Route("GET", "/api/health", handle_health, auth_required=False),
    Route("GET", "/api/stats", handle_stats),
    Route("GET", "/api/sessions", handle_sessions),
    Route("GET", "/api/config", handle_config),
    Route("GET", "/api/a11y/contrast", handle_contrast_get),
    Route("POST", "/api/a11y/contrast", handle_contrast_post),
    Route("GET", "/api/pomodoro", handle_pomodoro_get),
    Route("POST", "/api/pomodoro", handle_pomodoro_post),
    Route("POST", "/api/sync/preview", handle_sync_preview),
    Route("POST", "/api/sync/import", handle_sync_import),
    Route("GET", "/api/benchmarks", handle_benchmarks_get),
    Route("POST", "/api/benchmarks", handle_benchmarks_post),
)

_ROUTE_MAP: Dict[Tuple[str, str], Route] = {(r.method, r.path): r for r in ROUTES}
_PATHS: frozenset = frozenset(r.path for r in ROUTES)


def find_route(method: str, path: str) -> Optional[Route]:
    """Look up a route by (method, path). Returns None for unknown paths."""
    return _ROUTE_MAP.get((method, path))


def path_exists(path: str) -> bool:
    """True when `path` matches any registered route (any method)."""
    return path in _PATHS


# ---------------------------------------------------------------------------
# HTTP handler and server.
# ---------------------------------------------------------------------------

def make_handler(ctx: APIContext):
    """Build a BaseHTTPRequestHandler subclass bound to `ctx`."""

    class LocalAPIHandler(BaseHTTPRequestHandler):
        server_version = "LinuxAI/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("local-api: %s", format % args if args else format)

        def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # No CORS: this API is for local non-browser clients only.
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _check_auth(self, route: Route) -> bool:
            if not route.auth_required:
                return True
            if not ctx.token:
                return False
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                provided = header[7:]
            else:
                provided = self.headers.get("X-Linux-AI-Token", "")
            return secrets.compare_digest(provided, ctx.token)

        def _read_body(self) -> Dict[str, Any]:
            length = self.headers.get("Content-Length")
            if length is None:
                return {}
            try:
                size = int(length)
            except ValueError:
                raise LocalAPIError(400, "Invalid Content-Length") from None
            if size < 0 or size > MAX_BODY_BYTES:
                raise LocalAPIError(413, "Request body exceeds the 1 MiB limit")
            raw = self.rfile.read(size)
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise LocalAPIError(400, "Request body must be valid JSON") from None
            if not isinstance(data, dict):
                raise LocalAPIError(400, "Request body must be a JSON object")
            return data

        def _handle(self, method: str) -> None:
            ctx.record_request()
            try:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                if len(parsed.query) > MAX_QUERY_LENGTH:
                    raise LocalAPIError(400, "Query string too long")
                query = parse_qs(parsed.query)
                route = find_route(method, path)
                if route is None:
                    if path_exists(path):
                        raise LocalAPIError(405, "Method not allowed for this path")
                    raise LocalAPIError(404, "Not found: " + path)
                if not self._check_auth(route):
                    raise LocalAPIError(401, "Unauthorized")
                body = self._read_body() if method in ("POST", "PUT", "PATCH") else {}
                result = route.handler(ctx, query, body)
                self._send_json(200, result)
            except LocalAPIError as error:
                self._send_json(error.status, _err(error.message))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                logger.exception("local-api handler error")
                self._send_json(500, _err("Internal error: " + type(error).__name__))

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

        def do_PATCH(self) -> None:
            self._handle("PATCH")

        def do_DELETE(self) -> None:
            self._handle("DELETE")

    return LocalAPIHandler


def is_loopback_host(host: str) -> bool:
    """True when `host` is a loopback address (127.0.0.1, ::1, localhost)."""
    if host in ("localhost",):
        return True
    if host in ("127.0.0.1", "::1", "[::1]"):
        return True
    # 127.0.0.0/8 — any 127.x.y.z is loopback.
    if host.startswith("127."):
        parts = host.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return True
    return False


class LocalAPIServer:
    """A loopback-only HTTP server exposing the local API.

    The server refuses to bind to a non-loopback host. Use
    :meth:`start_background` to run it in a daemon thread, or
    :meth:`serve_forever` for a blocking call.
    """

    def __init__(self, ctx: APIContext, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        if not is_loopback_host(host):
            raise ValueError(
                "Local API refuses to bind to non-loopback host: {!r}. "
                "Use 127.0.0.1, localhost or ::1.".format(host)
            )
        self.ctx = ctx
        self.host = host
        self.port = port
        handler = make_handler(ctx)
        self._server = ThreadingHTTPServer((host, port), handler)
        self._server.daemon_threads = True
        self._thread: Optional[threading.Thread] = None
        # If port was 0, the OS picked one; record the actual port.
        self.port = self._server.server_address[1]

    @property
    def url(self) -> str:
        return "http://{}:{}".format(self.host, self.port)

    def start_background(self) -> threading.Thread:
        """Start serving in a daemon thread. Returns the thread."""
        if self._thread is not None:
            raise RuntimeError("Server already started")
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="local-api-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("Local API listening on %s", self.url)
        return self._thread

    def serve_forever(self) -> None:
        """Serve in the calling thread (blocking)."""
        logger.info("Local API listening on %s (blocking)", self.url)
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Stop the server and release the port."""
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Local API stopped")


def generate_token() -> str:
    """Generate a new random bearer token for the local API."""
    return secrets.token_urlsafe(32)
