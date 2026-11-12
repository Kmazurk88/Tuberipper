"""Logging you can follow: every request gets an id, answers carry it in X-Request-Id, and the interesting
requests and every error are logged as one JSON line each (to the container log: `docker logs tuberipper`).

  {"t": "2026-11-12T20:10:04", "level": "ERROR", "event": "request", "rid": "3fa9c1d2e4b7", "method": "GET",
   "path": "/api/search", "status": 500, "ms": 412, "user": "alice", "exc": "Traceback ..."}

An unexpected error is answered with JSON {"detail": "... Ref 3fa9c1d2e4b7"}, which the page shows, so an error seen
on the phone leads straight to its log line:  docker logs tuberipper 2>&1 | grep 3fa9c1d2e4b7

Quiet on purpose: streams, covers, static files, health checks and the polling of running jobs are logged only when
they fail or are slow. The query string is never logged (search words, ids)."""

import contextvars
import json
import logging
import re
import sys
import threading
import time
import traceback
import uuid

request_id = contextvars.ContextVar("request_id", default="")
log = logging.getLogger("tuberipper")

SLOW_MS = 1500
_ID = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
# Logged only when slow or failing.
_QUIET_PREFIXES = ("/static/", "/api/nd/stream/", "/api/nd/cover/", "/api/preview", "/api/health", "/api/busy",
                   "/api/jobs/", "/api/nd/rest/", "/api/instance", "/api/meta")
_QUIET_PATHS = {"/", "/favicon.ico", "/api/jobs/active"}
_STREAMS = ("/api/nd/stream/", "/api/preview")     # these last as long as the song: never "slow"


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"t": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)), "level": record.levelname, "event": record.getMessage()}
        rid = request_id.get()
        if rid:
            out["rid"] = rid
        out.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            out["exc"] = "".join(traceback.format_exception(*record.exc_info))[-4000:]
        return json.dumps(out, ensure_ascii=False, default=str)


_ready = False


def setup() -> None:
    """Once per process: JSON lines on stdout, and uncaught errors in background threads logged the same way."""
    global _ready
    if _ready:
        return
    _ready = True
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

    def hook(args):
        if args.exc_type is SystemExit:
            return
        log.error("thread_error", exc_info=(args.exc_type, args.exc_value, args.exc_traceback), extra={"fields": {"thread": args.thread.name if args.thread else None}})
    threading.excepthook = hook


def event(name: str, level: int = logging.INFO, **fields) -> None:
    log.log(level, name, extra={"fields": fields})


def _quiet(method: str, path: str) -> bool:
    if method != "GET":
        return False
    return path in _QUIET_PATHS or path.startswith(_QUIET_PREFIXES)


class RequestLog:
    """Pure ASGI (so streamed audio is untouched). Sits innermost, so the CORS headers still reach its error answer."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        asked = headers.get(b"x-request-id", b"").decode("latin-1")
        rid = asked if _ID.match(asked) else uuid.uuid4().hex[:12]
        token = request_id.set(rid)
        method, path = scope["method"], scope["path"]
        user = headers.get(b"x-tr-user", b"").decode("latin-1") or None
        started = time.monotonic()
        status = None
        began = False

        async def send_wrapped(message):
            nonlocal status, began
            if message["type"] == "http.response.start":
                began = True
                status = message["status"]
                message = dict(message, headers=list(message.get("headers", [])) + [(b"x-request-id", rid.encode())])
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_wrapped)
            except Exception:
                ms = int((time.monotonic() - started) * 1000)
                log.error("request", exc_info=True, extra={"fields": {"rid": rid, "method": method, "path": path, "status": status or 500, "ms": ms, "user": user}})
                if began:
                    raise   # the answer is already on its way: all that is left is to cut it off
                body = json.dumps({"detail": f"Something went wrong on the server. Ref {rid}"}).encode()
                await send_wrapped({"type": "http.response.start", "status": 500, "headers": [
                    (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
                await send_wrapped({"type": "http.response.body", "body": body})
                return
            ms = int((time.monotonic() - started) * 1000)
            failed = status is not None and status >= 400
            slow = ms >= SLOW_MS and not path.startswith(_STREAMS)
            if failed or slow or not _quiet(method, path):
                level = logging.WARNING if (failed and status >= 500) or slow else logging.INFO
                log.log(level, "request", extra={"fields": {"rid": rid, "method": method, "path": path, "status": status, "ms": ms, "user": user}})
        finally:
            request_id.reset(token)
