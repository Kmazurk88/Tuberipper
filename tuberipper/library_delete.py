"""Delete songs from the music library, from the app's song/album menus.

Only available on the home-network and Tailscale addresses: the internet-facing port blocks it (auth.py), and
every request must come from the app's own page (no cross-site requests). Deletes are permanent: the audio
file, its .lrc lyrics and, once an album folder holds no audio any more, its cover art and folder.

Navidrome doesn't reveal real file paths, so the file is found by its size (which Navidrome reports) and then
confirmed by its tags; if that is ambiguous nothing is deleted."""

import os
import threading
import time

from fastapi import APIRouter, HTTPException, Request

from . import config, library_index, navidrome, tagger
from .discover import _norm

router = APIRouter()
_lock = threading.Lock()
_cache = {"at": 0.0, "by_size": {}}
SIDECARS = (".lrc",)
COVERS = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png")


def _root() -> str:
    return os.path.realpath(config.DEFAULT_OUTPUT_DIR)


def same_origin(request: Request) -> bool:
    """A browser tags cross-site requests with a different Origin; refuse those (other clients send none)."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    return origin.split("://", 1)[-1].rstrip("/") in (request.headers.get("host"), request.headers.get("x-forwarded-host"))


def _by_size() -> dict:
    with _lock:
        if time.time() - _cache["at"] < 60:
            return _cache["by_size"]
        out = {}
        for folder, dirs, files in os.walk(_root()):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.lower().endswith(tuple(tagger.AUDIO_EXTENSIONS)):
                    p = os.path.join(folder, f)
                    try:
                        out.setdefault(os.path.getsize(p), []).append(p)
                    except OSError:
                        pass
        _cache.update(at=time.time(), by_size=out)
        return out


def _file_tags(path: str) -> dict:
    from mutagen import File
    try:
        a = File(path, easy=True)
        g = lambda k: (a.get(k) or [""])[0] if a else ""
        return {"title": g("title"), "album": g("album"), "artist": g("artist")}
    except Exception:
        return {}


def find_file(song: dict) -> str | None:
    """The one audio file that is this Navidrome song, or None if it can't be found for certain."""
    size = song.get("size")
    if not size:
        return None
    want_t, want_a = _norm(song.get("title", "")), _norm(song.get("album", ""))
    hits = [p for p in _by_size().get(int(size), []) if _norm(_file_tags(p).get("title", "")) == want_t]
    if len(hits) > 1:  # identical copies: take the one that sits in a folder named like the album
        hits = [p for p in hits if _norm(os.path.basename(os.path.dirname(p))) == want_a] or hits
    return hits[0] if len(hits) == 1 else None


def delete_file(path: str) -> None:
    root, real = _root(), os.path.realpath(path)
    if not real.startswith(root + os.sep) or not os.path.isfile(real):
        raise HTTPException(400, "Refusing to delete a file outside the music library.")
    os.remove(real)
    stem = os.path.splitext(real)[0]
    for ext in SIDECARS:
        if os.path.isfile(stem + ext):
            os.remove(stem + ext)
    folder = os.path.dirname(real)
    while folder.startswith(root + os.sep) and os.path.isdir(folder):
        entries = os.listdir(folder)
        # Only a folder with no music and no sub-folders left is cleaned up (never an artist folder that still has albums).
        if any(os.path.isdir(os.path.join(folder, e)) or e.lower().endswith(tuple(tagger.AUDIO_EXTENSIONS)) for e in entries):
            break
        for e in entries:
            if e.lower() in COVERS or e.lower().endswith(SIDECARS):
                try:
                    os.remove(os.path.join(folder, e))
                except OSError:
                    pass
        try:
            os.rmdir(folder)  # only succeeds when nothing else is left in it
        except OSError:
            break
        folder = os.path.dirname(folder)


@router.post("/api/library/delete")
async def delete_songs(request: Request):
    """Body: {"ids": ["<Navidrome song id>", ...]} -> {"deleted": [...], "failed": [{"id", "reason"}]}"""
    if not same_origin(request):
        raise HTTPException(403, "Cross-site request refused.")
    try:
        ids = [str(i) for i in (await request.json()).get("ids", [])][:300]
    except Exception:
        raise HTTPException(400, "Send {\"ids\": [...]}.")
    if not ids:
        raise HTTPException(400, "Nothing to delete.")
    if not navidrome.configured():
        raise HTTPException(503, "Navidrome isn't set up.")
    deleted, failed = [], []
    for sid in ids:
        try:
            song = navidrome._call("getSong", [("id", sid)]).get("song") or {}
            path = find_file(song)
            if not path:
                failed.append({"id": sid, "reason": "Couldn't find that file in the music library (or more than one file matched), so nothing was deleted."})
                continue
            delete_file(path)
            deleted.append(sid)
        except HTTPException as exc:
            failed.append({"id": sid, "reason": exc.detail})
        except Exception as exc:
            failed.append({"id": sid, "reason": f"{type(exc).__name__}: {exc}"})
    with _lock:
        _cache["at"] = 0
    library_index.invalidate()
    return {"deleted": deleted, "failed": failed}
