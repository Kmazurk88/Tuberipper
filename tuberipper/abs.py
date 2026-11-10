"""Link to Audiobookshelf, which owns the podcast files.

  - delete an episode: asks Audiobookshelf to remove it (including the file), so both apps agree and the
    episode disappears from Navidrome a few seconds later. TubeRipper only has read access to the podcast folder.
  - progress: where you are in an episode (and whether it's finished) is copied between Audiobookshelf and the
    player; the app decides which side is newer and calls these two endpoints.

Needs an Audiobookshelf API key (Settings > API Keys), saved in <cache dir>/abs-token.txt (or the
AUDIOBOOKSHELF_TOKEN env var). Without it everything here says "not configured" and the app hides the features."""

import os
import threading
import time

import requests
from fastapi import APIRouter, HTTPException, Request

from . import navidrome
from .discover import _norm, pod_norm
from .library_delete import same_origin

router = APIRouter()
_lock = threading.Lock()
_cache = {"at": 0.0, "map": {}, "rev": {}}


class AbsError(Exception):
    pass


def _url() -> str:
    return os.environ.get("AUDIOBOOKSHELF_URL", "http://audiobookshelf:80").rstrip("/")


def _token() -> str:
    tok = os.environ.get("AUDIOBOOKSHELF_TOKEN", "").strip()
    if tok:
        return tok
    root = os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(os.environ.get("TUBERIPPER_OUTPUT_DIR", "/music"), ".tuberipper")
    path = os.environ.get("AUDIOBOOKSHELF_TOKEN_FILE") or os.path.join(root, "abs-token.txt")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def configured() -> bool:
    return bool(_token())


def _req(method: str, path: str, **kw) -> dict:
    try:
        r = requests.request(method, _url() + path, headers={"Authorization": "Bearer " + _token()}, timeout=20, **kw)
    except requests.RequestException as exc:
        raise AbsError(f"Couldn't reach Audiobookshelf: {type(exc).__name__}")
    if r.status_code in (401, 403):
        raise AbsError("Audiobookshelf refused the API key.")
    if r.status_code >= 400:
        raise AbsError(f"Audiobookshelf answered {r.status_code}.")
    try:
        return r.json() if r.content else {}
    except ValueError:
        return {}


def _list(x):
    return x if isinstance(x, list) else ([x] if x else [])


def _abs_shows() -> dict:
    """normalised show title -> {item: id, eps: {normalised episode title: episode id}}"""
    shows = {}
    for lib in _req("GET", "/api/libraries").get("libraries", []):
        if lib.get("mediaType") != "podcast":
            continue
        for it in _req("GET", f"/api/libraries/{lib['id']}/items", params={"limit": 200}).get("results", []):
            full = _req("GET", f"/api/items/{it['id']}", params={"expanded": 1})
            media = full.get("media") or {}
            eps = {}
            for e in media.get("episodes", []):
                eps.setdefault(pod_norm(e.get("title", "")), e["id"])
            shows[_norm((media.get("metadata") or {}).get("title", ""))] = {"item": it["id"], "eps": eps}
    return shows


def _nd_podcast_songs() -> list:
    folders = _list(navidrome._call("getMusicFolders").get("musicFolders", {}).get("musicFolder"))
    pod = next((f for f in folders if "podcast" in f.get("name", "").lower()), None)
    if not pod:
        return []
    songs, offset = [], 0
    while True:
        page = _list(navidrome._call("getAlbumList2", [("type", "alphabeticalByName"), ("size", "500"), ("offset", str(offset)), ("musicFolderId", str(pod["id"]))]).get("albumList2", {}).get("album"))
        for a in page:
            for s in _list(navidrome._call("getAlbum", [("id", a["id"])]).get("album", {}).get("song")):
                songs.append({"id": s["id"], "title": s.get("title", ""), "album": s.get("album", "")})
        if len(page) < 500:
            return songs
        offset += 500


def _mapping() -> tuple:
    """(Navidrome song id -> (item id, episode id), episode id -> Navidrome song id)"""
    with _lock:
        if time.time() - _cache["at"] < 120 and _cache["map"]:
            return _cache["map"], _cache["rev"]
    shows, m, rev = _abs_shows(), {}, {}
    for s in _nd_podcast_songs():
        show = shows.get(_norm(s["album"]))
        ep = show and show["eps"].get(pod_norm(s["title"]))
        if ep:
            m[s["id"]] = (show["item"], ep)
            rev[ep] = s["id"]
    with _lock:
        _cache.update(at=time.time(), map=m, rev=rev)
    return m, rev


def invalidate():
    with _lock:
        _cache["at"] = 0


def _guard():
    if not configured():
        raise HTTPException(503, "Audiobookshelf isn't connected (no API key).")


@router.get("/api/podcast/capabilities")
def capabilities():
    return {"abs": configured()}


@router.get("/api/podcast/abs-progress")
def abs_progress():
    """Progress Audiobookshelf has for episodes the player knows: [{song, position (ms), duration (s), finished, ts (ms)}]"""
    _guard()
    try:
        _, rev = _mapping()
        out = []
        for p in _req("GET", "/api/me").get("mediaProgress", []):
            song = rev.get(p.get("episodeId"))
            if song and (p.get("isFinished") or (p.get("currentTime") or 0) > 0):
                out.append({"song": song, "position": int((p.get("currentTime") or 0) * 1000), "duration": p.get("duration") or 0,
                            "finished": bool(p.get("isFinished")), "ts": int(p.get("lastUpdate") or 0)})
        return {"progress": out}
    except AbsError as exc:
        raise HTTPException(502, str(exc))


@router.post("/api/podcast/abs-progress")
async def push_progress(request: Request):
    """Body: {song, position (ms), duration (s), finished}"""
    _guard()
    if not same_origin(request):
        raise HTTPException(403, "Cross-site request refused.")
    try:
        b = await request.json()
        song, pos, dur, fin = str(b["song"]), float(b.get("position") or 0) / 1000, float(b.get("duration") or 0), bool(b.get("finished"))
    except Exception:
        raise HTTPException(400, "Send {song, position, duration, finished}.")
    try:
        item_ep = _mapping()[0].get(song)
        if not item_ep:
            raise HTTPException(404, "That episode isn't in Audiobookshelf.")
        cur = dur if fin and dur else pos
        _req("PATCH", f"/api/me/progress/{item_ep[0]}/{item_ep[1]}",
             json={"currentTime": cur, "duration": dur, "progress": min(1.0, cur / dur) if dur else 0, "isFinished": fin})
        return {"ok": True}
    except AbsError as exc:
        raise HTTPException(502, str(exc))


@router.post("/api/podcast/delete")
async def delete_episode(request: Request):
    """Body: {song}. Home network / Tailscale only (blocked on the public port). Deletes the episode and its file."""
    _guard()
    if not same_origin(request):
        raise HTTPException(403, "Cross-site request refused.")
    try:
        song = str((await request.json())["song"])
    except Exception:
        raise HTTPException(400, "Send {song}.")
    try:
        item_ep = _mapping()[0].get(song)
        if not item_ep:
            raise HTTPException(404, "That episode isn't in Audiobookshelf, so it can't be deleted from here.")
        _req("DELETE", f"/api/podcasts/{item_ep[0]}/episode/{item_ep[1]}", params={"hard": 1})
        invalidate()
        return {"ok": True}
    except AbsError as exc:
        raise HTTPException(502, str(exc))


@router.post("/api/podcast/delete-show")
async def delete_show(request: Request):
    """Body: {songs: [Navidrome song ids of the whole podcast]}. Home network / Tailscale only. Deletes the podcast, all
    its episodes and their files, through Audiobookshelf."""
    _guard()
    if not same_origin(request):
        raise HTTPException(403, "Cross-site request refused.")
    try:
        songs = [str(i) for i in (await request.json())["songs"]]
    except Exception:
        raise HTTPException(400, "Send {songs: [...]}.")
    try:
        m = _mapping()[0]
        items = sorted({m[s][0] for s in songs if s in m})
        if not items:
            raise HTTPException(404, "That podcast isn't in Audiobookshelf, so it can't be deleted from here.")
        for item in items:
            _req("DELETE", f"/api/items/{item}", params={"hard": 1})
        invalidate()
        return {"ok": True, "podcasts": len(items)}
    except AbsError as exc:
        raise HTTPException(502, str(exc))
