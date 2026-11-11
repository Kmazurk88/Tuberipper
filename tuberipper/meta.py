"""Version information shared by the server, the web page and the Android app.

GET /api/meta ->
  api         API_VERSION below. Raise it when a route the page or the app uses changes incompatibly.
  shell       the web app's asset version (changes whenever app.html/js/css change); the same value the
              page's ?v= addresses carry, so an open page can tell that it is out of date.
  latest_app  versionCode/versionName of the APK published at /app.apk (from tuberipper.apk.json, written
              next to the APK by the publish script), or null.
  min_app     the oldest app versionCode this server still works with (minVersionCode in that file, 0 = any).

Every response also carries X-TR-Shell (the current asset version), so the page notices a new version on its
next request without polling."""

import json
import os
import threading
import time

from fastapi import APIRouter

API_VERSION = 1

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_SHELL_FILES = ("app.html", "app.js", "app.css")

router = APIRouter()

_lock = threading.Lock()
_cached = ("", 0.0)


def asset_version() -> str:
    """Changes whenever the app's files change, so a phone can never mix a new script with an old stylesheet.
    Re-read at most every 5 s (it is sent on every response)."""
    global _cached
    with _lock:
        value, at = _cached
        if value and time.monotonic() - at < 5:
            return value
        value = str(int(max(os.path.getmtime(os.path.join(STATIC_DIR, f)) for f in _SHELL_FILES)))
        _cached = (value, time.monotonic())
        return value


def _cache_dir() -> str:
    return os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(os.environ.get("TUBERIPPER_OUTPUT_DIR", "/music"), ".tuberipper")


def app_release() -> dict:
    """{"latest": {"versionCode", "versionName"} | None, "min": int} from <cache>/tuberipper.apk.json."""
    try:
        with open(os.path.join(_cache_dir(), "tuberipper.apk.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    latest = None
    if isinstance(data.get("versionCode"), int) and os.path.isfile(os.path.join(_cache_dir(), "tuberipper.apk")):
        latest = {"versionCode": data["versionCode"], "versionName": str(data.get("versionName") or "")}
    min_app = data.get("minVersionCode")
    return {"latest": latest, "min": min_app if isinstance(min_app, int) and min_app > 0 else 0}


@router.get("/api/meta")
def meta():
    rel = app_release()
    return {"api": API_VERSION, "shell": asset_version(), "latest_app": rel["latest"], "min_app": rel["min"]}


class ShellHeader:
    """ASGI middleware: adds X-TR-Shell to every HTTP response (pure ASGI, so streamed audio is untouched)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                message = dict(message, headers=list(message.get("headers", [])) + [(b"x-tr-shell", asset_version().encode())])
            await send(message)

        await self.app(scope, receive, send_wrapped)
