"""GET /api/health/deps: is everything TubeRipper depends on in working order?

/api/health (server.py) only says "the process answers" and is what deploy.sh waits for. This one looks at the
things that break quietly: the music folder (mounted? writable? room left?), the cache folder and jobs.db,
Navidrome, and the tools downloads need (yt-dlp, ffmpeg, Deno). It never calls YouTube (download-check.sh does a
real download every morning).

status: "ok"; "degraded" (something optional is wrong: Navidrome unreachable, tools missing, disk getting full);
"down" (the library or cache folder is unusable: HTTP 503, so a monitor such as Uptime Kuma can alert on it).
Answers are kept 20 s so a monitor polling every few seconds doesn't hammer Navidrome."""

import os
import shutil
import threading
import time

import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from . import config, durable, navidrome

router = APIRouter()

LOW_DISK_GB = 5
CACHE_SECONDS = 20
_lock = threading.Lock()
_cached = (0.0, None)


def _cache_dir() -> str:
    return os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(config.DEFAULT_OUTPUT_DIR, ".tuberipper")


def check_library() -> dict:
    root = config.DEFAULT_OUTPUT_DIR
    if not os.path.isdir(root):
        return {"ok": False, "critical": True, "detail": f"{root} is not there (share not mounted?)"}
    try:
        os.listdir(root)
        usage = shutil.disk_usage(root)
    except OSError as exc:
        return {"ok": False, "critical": True, "detail": f"{type(exc).__name__}: {exc}"}
    free_gb = usage.free / 1e9
    if free_gb < LOW_DISK_GB:
        return {"ok": False, "critical": free_gb < 1, "detail": f"only {free_gb:.1f} GB free"}
    return {"ok": True, "detail": f"{free_gb:.0f} GB free"}


def check_cache() -> dict:
    path = _cache_dir()
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f".health-{os.getpid()}")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        durable.load("job")          # jobs.db opens and reads
    except Exception as exc:
        return {"ok": False, "critical": True, "detail": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "detail": "writable"}


def check_navidrome() -> dict:
    if not navidrome.configured():
        return {"ok": False, "detail": "not set up"}
    try:
        url, user, password = navidrome._config()
        import hashlib
        import secrets
        salt = secrets.token_hex(6)
        r = requests.get(f"{url}/rest/ping", timeout=3, params={
            "u": user, "t": hashlib.md5((password + salt).encode()).hexdigest(), "s": salt, "v": navidrome.API_VERSION, "c": "tuberipper-health", "f": "json"})
        body = r.json().get("subsonic-response", {})
        if r.ok and body.get("status") == "ok":
            return {"ok": True, "detail": "answers"}
        return {"ok": False, "detail": f"HTTP {r.status_code} {body.get('status', '')}".strip()}
    except Exception as exc:
        return {"ok": False, "detail": type(exc).__name__}     # the message can contain the address; the class says enough


def check_tools() -> dict:
    missing = [t for t in ("ffmpeg", "deno") if not shutil.which(t)]
    try:
        import yt_dlp.version as v
        ytdlp = v.__version__
    except Exception:
        ytdlp = None
        missing.append("yt-dlp")
    if missing:
        return {"ok": False, "detail": "missing: " + ", ".join(missing)}
    return {"ok": True, "detail": f"yt-dlp {ytdlp}"}


CHECKS = {"library": check_library, "cache": check_cache, "navidrome": check_navidrome, "tools": check_tools}


def run_checks() -> dict:
    checks = {}
    for name, fn in CHECKS.items():
        try:
            checks[name] = fn()
        except Exception as exc:
            checks[name] = {"ok": False, "detail": f"check failed: {type(exc).__name__}"}
    down = any(not c["ok"] and c.get("critical") for c in checks.values())
    degraded = any(not c["ok"] for c in checks.values())
    return {"status": "down" if down else "degraded" if degraded else "ok",
            "checks": {k: {"ok": v["ok"], "detail": v["detail"]} for k, v in checks.items()}, "checked": int(time.time())}


@router.get("/api/health/deps")
def health_deps():
    global _cached
    with _lock:
        at, result = _cached
        if result is None or time.monotonic() - at > CACHE_SECONDS:
            result = run_checks()
            _cached = (time.monotonic(), result)
    return JSONResponse(result, status_code=503 if result["status"] == "down" else 200, headers={"Cache-Control": "no-store"})
