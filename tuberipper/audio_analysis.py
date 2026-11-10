"""Loudness (for volume levelling) and silences (for "skip silence" in podcasts), measured with ffmpeg.

  - loudness: every music file is measured once (EBU R128 integrated loudness, in the background at low priority) and
    cached in <cache>/loudness.json. The app asks for the levels of the songs it is about to play and turns loud songs
    down so the volume stays about the same from song to song. Songs not measured yet are measured first.
  - silences: the quiet gaps of one podcast episode, measured on request and cached in <cache>/silence/.

Files are matched to Navidrome songs by size + title (as in library_delete.py); nothing here ever modifies a file."""

import json
import os
import re
import subprocess
import threading
import time

from fastapi import APIRouter, HTTPException

from . import config, library_delete, navidrome, tagger
from .discover import _norm, pod_norm

router = APIRouter()
_lock = threading.Lock()
_loud = {}            # "<size>|<normalised title>" -> LUFS
_seen = {}            # file path -> "<size>|<mtime>" already measured
_song_key = {}        # Navidrome song id -> key (or None if it has no size/title)
_want = []            # keys' songs waiting for a priority measurement: [(key, path)]
_started = False
FF = ["nice", "-n", "19", "ffmpeg", "-nostats", "-hide_banner", "-vn"]


def _cache_dir() -> str:
    return os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(os.environ.get("TUBERIPPER_OUTPUT_DIR", "/music"), ".tuberipper")


def _json_path() -> str:
    return os.path.join(_cache_dir(), "loudness.json")


def _load() -> None:
    try:
        with open(_json_path(), encoding="utf-8") as f:
            d = json.load(f)
        _loud.update(d.get("loud", {}))
        _seen.update(d.get("seen", {}))
    except (OSError, ValueError):
        pass


def _save() -> None:
    try:
        os.makedirs(_cache_dir(), exist_ok=True)
        tmp = _json_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"loud": _loud, "seen": _seen}, f)
        os.replace(tmp, _json_path())
    except OSError:
        pass


def measure(path: str):
    """Integrated loudness of a file in LUFS, or None."""
    try:
        r = subprocess.run(FF + ["-i", path, "-af", "ebur128=framelog=quiet", "-f", "null", "-"], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.findall(r"^\s+I:\s+(-?[\d.]+) LUFS", r.stderr, re.M)
    return float(m[-1]) if m else None


def _title(path: str) -> str:
    return library_delete._file_tags(path).get("title", "")


def _key_for(path: str) -> str:
    return f"{os.path.getsize(path)}|{_norm(_title(path))}"


def _sweep() -> None:
    """Measure every music file once, slowly. Files that change are measured again."""
    _load()
    while True:
        try:
            for folder, dirs, files in os.walk(library_delete._root()):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    if not f.lower().endswith(tuple(tagger.AUDIO_EXTENSIONS)):
                        continue
                    p = os.path.join(folder, f)
                    try:
                        sig = f"{os.path.getsize(p)}|{int(os.path.getmtime(p))}"
                    except OSError:
                        continue
                    while _want:   # songs about to play go first
                        with _lock:
                            key, wp = _want.pop(0)
                        if key not in _loud:
                            v = measure(wp)
                            if v is not None:
                                with _lock:
                                    _loud[key] = v
                    if _seen.get(p) == sig:
                        continue
                    v = measure(p)
                    with _lock:
                        _seen[p] = sig
                        if v is not None:
                            _loud[_key_for(p)] = v
                        if len(_seen) % 50 == 0:
                            _save()
                    time.sleep(0.05)
            with _lock:
                _save()
        except Exception:
            pass
        time.sleep(1800)   # look again for new files every half hour


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_sweep, daemon=True, name="loudness-sweep").start()


def _resolve(sid: str):
    """(key, song) for a Navidrome song id."""
    if sid in _song_key and _song_key[sid][0] is not None:
        return _song_key[sid]
    song = navidrome._call("getSong", [("id", sid)]).get("song") or {}
    key = f"{int(song['size'])}|{_norm(song.get('title', ''))}" if song.get("size") else None
    _song_key[sid] = (key, song)
    return _song_key[sid]


@router.get("/api/loudness")
def loudness(ids: str):
    """?ids=a,b,c -> {"levels": {id: LUFS}} for the songs that have been measured; the rest are measured soon."""
    start()
    if not navidrome.configured():
        raise HTTPException(503, "Navidrome isn't set up.")
    out = {}
    for sid in [i for i in ids.split(",") if i][:12]:
        try:
            key, song = _resolve(sid)
        except Exception:
            continue
        if not key:
            continue
        if key in _loud:
            out[sid] = _loud[key]
        elif not any(k == key for k, _ in _want):
            p = library_delete.find_file(song)
            if p:
                with _lock:
                    _want.append((key, p))
    return {"levels": out, "measured": len(_loud)}


# ---------------- silences in podcast episodes ----------------

_sil_busy = set()


def _podcasts_dir() -> str:
    return os.environ.get("TUBERIPPER_PODCASTS_DIR", "/podcasts")


def _find_episode(song: dict):
    size = song.get("size")
    if not size:
        return None
    want = pod_norm(song.get("title", ""))
    hits = []
    for folder, _, files in os.walk(_podcasts_dir()):
        for f in files:
            p = os.path.join(folder, f)
            if f.lower().endswith(tuple(tagger.AUDIO_EXTENSIONS)) and os.path.getsize(p) == int(size):
                hits.append(p)
    if len(hits) > 1:
        hits = [p for p in hits if pod_norm(_title(p)) == want] or hits
    return hits[0] if len(hits) == 1 else None


def detect_silences(path: str):
    try:
        r = subprocess.run(FF + ["-i", path, "-af", "silencedetect=noise=-38dB:d=0.7", "-f", "null", "-"], capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return None
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", r.stderr)]
    return [[round(max(0, s), 2), round(e, 2)] for s, e in zip(starts, ends)]


@router.get("/api/podcast/silences")
def silences(id: str):
    """{"ready": true, "ranges": [[start, end], ...]} in seconds; {"ready": false} while it is being measured."""
    if not navidrome.configured():
        raise HTTPException(503, "Navidrome isn't set up.")
    if not re.fullmatch(r"[\w-]{1,64}", id):
        raise HTTPException(400, "Bad id.")
    cache = os.path.join(_cache_dir(), "silence", id + ".json")
    try:
        with open(cache, encoding="utf-8") as f:
            return {"ready": True, "ranges": json.load(f)}
    except (OSError, ValueError):
        pass
    with _lock:
        if id in _sil_busy:
            return {"ready": False}
        _sil_busy.add(id)

    def work():
        try:
            song = navidrome._call("getSong", [("id", id)]).get("song") or {}
            p = _find_episode(song)
            ranges = detect_silences(p) if p else None
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(ranges or [], f)
        except Exception:
            pass
        finally:
            with _lock:
                _sil_busy.discard(id)

    threading.Thread(target=work, daemon=True).start()
    return {"ready": False}
