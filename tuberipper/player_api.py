"""Backend for the player UI: a thin Navidrome (Subsonic) proxy and a little
saved state (pins, recents).

The browser never sees Navidrome's credentials: it calls /api/nd/rest/<name>
and this signs the request with NAVIDROME_URL/USER/PASSWORD. Only a short
list of Subsonic calls is forwarded, so a page on the LAN can browse, play,
star, bookmark and edit playlists but can't reach anything administrative.
Audio and cover art are streamed through, with Range requests passed on so
seeking (and resuming a long track) works."""

import csv
import json
import os
import threading
import time

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from . import navidrome, spotify_import

router = APIRouter()

ALLOWED = {
    "ping", "getMusicFolders", "getArtists", "getArtist", "getAlbum", "getSong", "getAlbumList2",
    "getGenres", "getSongsByGenre", "getRandomSongs", "search3", "getStarred2", "star", "unstar",
    "getPlaylists", "getPlaylist", "createPlaylist", "updatePlaylist", "deletePlaylist",
    "getBookmarks", "createBookmark", "deleteBookmark", "scrobble", "getScanStatus", "getLyricsBySongId",
}
# Never let a client override who we sign in as.
AUTH_KEYS = {"u", "p", "t", "s", "c", "v", "f", "apiKey"}

_http = requests.Session()


def _failed(message: str, status: int = 200):
    return JSONResponse({"subsonic-response": {"status": "failed", "version": navidrome.API_VERSION,
                                               "error": {"code": 0, "message": message}}}, status_code=status)


def _signed(params) -> tuple:
    url, user, password = navidrome._config()
    if not (url and user and password):
        raise HTTPException(503, "Navidrome isn't set up (NAVIDROME_URL / NAVIDROME_USER / NAVIDROME_PASSWORD).")
    import hashlib
    import secrets
    salt = secrets.token_hex(6)
    auth = [("u", user), ("t", hashlib.md5((password + salt).encode()).hexdigest()), ("s", salt),
            ("v", navidrome.API_VERSION), ("c", "tuberipper-player")]
    return url, auth + [(k, v) for k, v in params if k not in AUTH_KEYS]


@router.api_route("/api/nd/rest/{endpoint}", methods=["GET", "POST"])
def nd_rest(endpoint: str, request: Request):
    if endpoint not in ALLOWED:
        raise HTTPException(404, "Not available.")
    url, params = _signed(request.query_params.multi_items())
    try:
        resp = _http.get(f"{url}/rest/{endpoint}", params=params + [("f", "json")], timeout=30)
        resp.raise_for_status()
        return JSONResponse(resp.json(), headers={"Cache-Control": "no-store"})
    except (requests.RequestException, ValueError) as exc:
        return _failed(f"Couldn't reach Navidrome: {exc}", 502)


def _pass_through(endpoint: str, request: Request, params, media_default: str, cache: str):
    url, signed = _signed(params)
    headers = {}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    try:
        upstream = _http.get(f"{url}/rest/{endpoint}", params=signed, headers=headers, stream=True, timeout=(10, 60))
    except requests.RequestException as exc:
        raise HTTPException(502, f"Couldn't reach Navidrome: {exc}")
    if upstream.status_code >= 400:
        upstream.close()
        raise HTTPException(upstream.status_code if upstream.status_code != 401 else 502, "Navidrome refused the request.")
    ctype = upstream.headers.get("Content-Type", media_default)
    if "json" in ctype or "xml" in ctype or ctype.startswith("text/"):
        # A Subsonic error comes back as JSON/XML with HTTP 200.
        upstream.close()
        raise HTTPException(404, "Not found in Navidrome.")
    out = {"Cache-Control": cache}
    for h in ("Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag"):
        if h in upstream.headers:
            out[h] = upstream.headers[h]
    out.setdefault("Accept-Ranges", "bytes")

    def body():
        try:
            for chunk in upstream.iter_content(64 * 1024):
                yield chunk
        finally:
            upstream.close()

    return StreamingResponse(body(), status_code=upstream.status_code, media_type=ctype, headers=out)


@router.get("/api/nd/stream/{song_id}")
def nd_stream(song_id: str, request: Request):
    # format=raw: the original file, so Range works and nothing is re-encoded.
    return _pass_through("stream", request, [("id", song_id), ("format", "raw")], "audio/mpeg", "no-store")


@router.get("/api/nd/cover/{cover_id}")
def nd_cover(cover_id: str, request: Request, size: int = 300):
    size = max(32, min(size, 1200))
    return _pass_through("getCoverArt", request, [("id", cover_id), ("size", str(size))], "image/jpeg",
                         "public, max-age=604800")


# ---------- Saved state (pins, recents) ----------

STATE_MAX_BYTES = 256 * 1024
_state_lock = threading.Lock()


def _state_path() -> str:
    root = os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(os.environ.get("TUBERIPPER_OUTPUT_DIR", "/music"), ".tuberipper")
    return os.path.join(root, "app-state.json")


def _read_state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@router.get("/api/app/state")
def get_state():
    with _state_lock:
        s = _read_state()
    return {"pins": s.get("pins", []), "recents": s.get("recents", []), "finished": s.get("finished", []), "finishedAt": s.get("finishedAt", {}), "updated": s.get("updated", 0)}


@router.put("/api/app/state")
async def put_state(request: Request):
    raw = await request.body()
    if len(raw) > STATE_MAX_BYTES:
        raise HTTPException(413, "State too large.")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Invalid JSON.")

    def clean(items, limit):
        out = []
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and isinstance(it.get("id"), str) and isinstance(it.get("type"), str):
                out.append({k: it.get(k) for k in ("type", "id", "name", "sub", "cover", "ts") if k in it})
        return out[:limit]

    finished = [x for x in (body.get("finished") if isinstance(body.get("finished"), list) else []) if isinstance(x, str) and 0 < len(x) <= 40][:5000]
    fa = body.get("finishedAt") if isinstance(body.get("finishedAt"), dict) else {}
    finished_at = {k: int(v) for k, v in list(fa.items())[:5000] if isinstance(k, str) and 0 < len(k) <= 40 and isinstance(v, (int, float))}
    state = {"pins": clean(body.get("pins"), 100), "recents": clean(body.get("recents"), 40), "finished": finished, "finishedAt": finished_at, "updated": time.time()}
    with _state_lock:
        path = _state_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, path)
        except OSError as exc:
            raise HTTPException(500, f"Couldn't save: {exc}")
    return {"ok": True}


# ---------- Spotify CSV import ----------

@router.post("/api/app/import")
async def start_import(request: Request):
    """Body: {csv: "<Exportify file text>", name: "Playlist name", playlist_id?: "<existing Navidrome playlist>"}"""
    raw = await request.body()
    if len(raw) > 4 * 1024 * 1024:
        raise HTTPException(413, "That file is too big.")
    try:
        body = json.loads(raw)
        rows = spotify_import.parse_csv(str(body.get("csv", "")))
    except (ValueError, csv.Error):
        raise HTTPException(400, "Couldn't read that file. Use an Exportify .csv.")
    if not rows:
        raise HTTPException(400, "No songs found. Use an Exportify .csv (it needs Track Name and Artist Name(s) columns).")
    if len(rows) > spotify_import.MAX_ROWS:
        raise HTTPException(400, f"That's {len(rows)} songs; the limit is {spotify_import.MAX_ROWS}.")
    name = str(body.get("name") or "").strip() or "Imported playlist"
    spotify_import.save_upload(str(body.get("csv", "")), name)
    try:
        return spotify_import.start(rows, name, body.get("playlist_id") or None)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@router.get("/api/app/import/{import_id}")
def import_status(import_id: str):
    imp = spotify_import.get(import_id)
    if not imp:
        raise HTTPException(404, "Import not found (the server may have restarted).")
    return imp


# ---------- Android app download ----------

@router.get("/app.apk")
def download_apk():
    """The Android wrapper, for installing on the phone: put tuberipper.apk in the cache folder."""
    path = os.path.join(os.path.dirname(_state_path()), "tuberipper.apk")
    if not os.path.isfile(path):
        raise HTTPException(404, "No app has been published yet.")
    return FileResponse(path, media_type="application/vnd.android.package-archive", filename="tuberipper.apk")
