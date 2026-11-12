"""Problem reports from the Android app and the web page: POST /api/app/diag.

The app sends what it knows after a crash (stack trace, why Android last ended it, the tail of its own little
log), on launch and when you tap "Send diagnostics" in the menu; the page sends JavaScript errors. Each report is
saved as <cache>/diag/<time>-<id>.json and a short note goes to Discord (once per kind of problem per 10 minutes),
so a problem on the phone shows up here without anyone describing it.

Everything is size-capped and type-checked: the body is limited, every string is cut, only the fields below are
kept, at most 30 reports an hour are accepted (the rest get 429), and only the newest 200 files are kept. On the
internet address the usual login applies (auth.PublicGate); on the home network, as everywhere, there is none.

GET /api/app/diag lists the recent reports (newest first) without the long text; ?id=<file name> returns one."""

import hashlib
import json
import os
import re
import threading
import time
import uuid

import requests
from fastapi import APIRouter, HTTPException, Request

from . import config, obs

router = APIRouter()

MAX_BODY = 96 * 1024
MAX_FILES = 200
PER_HOUR = 30
DISCORD_EVERY = 600
KINDS = {"crash", "exit", "manual", "startup", "js"}

_lock = threading.Lock()
_accepted: list = []          # times of accepted reports, for the hourly limit
_told: dict = {}              # problem signature -> last Discord time


def _dir() -> str:
    root = os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(config.DEFAULT_OUTPUT_DIR, ".tuberipper")
    return os.path.join(root, "diag")


def _s(value, limit) -> str:
    return str(value if value is not None else "")[:limit]


def _n(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def clean(body: dict) -> dict:
    """Only the fields we know, each cut to size."""
    kind = body.get("kind")
    if kind not in KINDS:
        raise ValueError("unknown kind")
    app = body.get("app") if isinstance(body.get("app"), dict) else {}
    device = body.get("device") if isinstance(body.get("device"), dict) else {}
    out = {
        "kind": kind,
        "app": {"name": _s(app.get("name"), 20), "code": _n(app.get("code"))},
        "device": {"android": _n(device.get("android")), "model": _s(device.get("model"), 60)},
        "note": _s(body.get("note"), 500),
        "crashes": [], "exits": [], "log": _s(body.get("log"), 16000),
        "js": None,
    }
    for c in (body.get("crashes") if isinstance(body.get("crashes"), list) else [])[:5]:
        if isinstance(c, dict):
            out["crashes"].append({"ts": _n(c.get("ts")), "thread": _s(c.get("thread"), 60), "stack": _s(c.get("stack"), 6000),
                                   "playing": bool(c.get("playing")), "where": _s(c.get("where"), 20)})
    for e in (body.get("exits") if isinstance(body.get("exits"), list) else [])[:10]:
        if isinstance(e, dict):
            out["exits"].append({"ts": _n(e.get("ts")), "reason": _s(e.get("reason"), 40), "status": _n(e.get("status")),
                                 "importance": _n(e.get("importance")), "description": _s(e.get("description"), 300),
                                 "rss_kb": _n(e.get("rss_kb"))})
    js = body.get("js")
    if isinstance(js, dict):
        out["js"] = {"message": _s(js.get("message"), 500), "source": _s(js.get("source"), 200), "line": _n(js.get("line")),
                     "stack": _s(js.get("stack"), 3000), "shell": _s(js.get("shell"), 20), "ua": _s(js.get("ua"), 200)}
    return out


def _signature(rep: dict) -> str:
    if rep["js"]:
        text = f"js|{rep['js']['message']}|{rep['js']['source']}|{rep['js']['line']}"
    elif rep["crashes"]:
        text = "crash|" + "\n".join(rep["crashes"][0]["stack"].splitlines()[:3])
    elif rep["exits"]:
        text = "exit|" + ",".join(sorted({e["reason"] for e in rep["exits"]}))
    else:
        text = f"{rep['kind']}|{rep['note']}"
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _summary(rep: dict) -> str:
    who = f"app {rep['app']['name']} · Android {rep['device']['android']} · {rep['device']['model']}"
    if rep["js"]:
        return f"JavaScript error: {rep['js']['message']} ({rep['js']['source']}:{rep['js']['line']}) · page {rep['js']['shell']}"
    if rep["crashes"]:
        first = rep["crashes"][0]["stack"].splitlines()[0] if rep["crashes"][0]["stack"] else "crash"
        return f"Crash: {first} · {who}"
    if rep["exits"]:
        return "Android ended the app: " + ", ".join(sorted({e["reason"] for e in rep["exits"]})) + f" · {who}"
    return f"{rep['kind'].capitalize()} report{': ' + rep['note'] if rep['note'] else ''} · {who}"


def _discord(text: str, rid: str) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    label = os.environ.get("INSTANCE_LABEL", "").strip()
    try:
        requests.post(url, json={"embeds": [{"title": (f"[{label}] " if label else "") + "tuberipper problem report", "color": 0xE67E22,
                                             "description": text[:1500], "footer": {"text": f"ref {rid}"}}]},
                      headers={"User-Agent": "tuberipper"}, timeout=10)
    except requests.RequestException:
        pass


def _prune(folder: str) -> None:
    try:
        files = sorted(f for f in os.listdir(folder) if f.endswith(".json"))
        for old in files[:-MAX_FILES]:
            os.remove(os.path.join(folder, old))
    except OSError:
        pass


@router.post("/api/app/diag")
async def receive(request: Request):
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "Report too large.")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("not an object")
        rep = clean(body)
    except ValueError:
        raise HTTPException(400, "Not a valid report.")
    now = time.time()
    with _lock:
        _accepted[:] = [t for t in _accepted if now - t < 3600]
        if len(_accepted) >= PER_HOUR:
            raise HTTPException(429, "Too many reports; try again later.")
        _accepted.append(now)
        sig = _signature(rep)
        tell = now - _told.get(sig, 0) > DISCORD_EVERY
        if tell:
            _told[sig] = now
    rid = obs.request_id.get() or uuid.uuid4().hex[:12]
    rep.update({"received": int(now), "rid": rid, "user": request.headers.get("x-tr-user") or None, "signature": sig})
    folder = _dir()
    name = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{rid}.json"
    try:
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, "." + name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(folder, name))
        _prune(folder)
    except OSError as exc:
        raise HTTPException(500, f"Couldn't save the report: {type(exc).__name__}")
    obs.event("diag_report", kind=rep["kind"], signature=sig, summary=_summary(rep)[:200], file=name)
    if tell:
        threading.Thread(target=_discord, args=(_summary(rep) + f"\nfile: diag/{name}", rid), daemon=True).start()
    return {"ok": True, "ref": rid}


_SAFE_NAME = re.compile(r"^[0-9]{8}-[0-9]{6}-[A-Za-z0-9_-]{6,40}\.json$")


@router.get("/api/app/diag")
def list_reports(id: str | None = None, limit: int = 30):
    folder = _dir()
    if id is not None:
        if not _SAFE_NAME.match(id):
            raise HTTPException(400, "Bad id.")
        try:
            with open(os.path.join(folder, id), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            raise HTTPException(404, "Not found.")
    try:
        files = sorted((f for f in os.listdir(folder) if _SAFE_NAME.match(f)), reverse=True)[:max(1, min(limit, 100))]
    except OSError:
        return []
    out = []
    for f in files:
        try:
            with open(os.path.join(folder, f), encoding="utf-8") as fh:
                rep = json.load(fh)
            out.append({"id": f, "kind": rep.get("kind"), "received": rep.get("received"), "user": rep.get("user"),
                        "app": rep.get("app"), "summary": _summary(rep)})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out
