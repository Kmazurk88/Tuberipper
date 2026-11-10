"""Library health: find what's wrong in the music folder and fix the safe things with one button.

A scan (read-only) reports:
  * duplicates   real copies of a song: same album folder, or a greatest-hits copy of the same recording (the best copy is
                 kept, the rest can be moved to the trash). The same title on different albums is left alone.
  * unreadable   empty or damaged files (can be moved to the trash, then re-added from search)
  * no cover     albums with no art at all (a cover.jpg is fetched from Deezer/iTunes and put in the folder)
  * stored twice the same album under two folder names, e.g. "X" and "X (1983)" (listed only: which one to keep is your call)
  * low quality  lossy files under 112 kbps (listed only: replace them by searching the song again)
  * no lyrics    songs without a .lrc file (the existing "repair" pass fills these in)

Nothing is ever deleted by a fix: files move to <library>/.tuberipper/trash/<batch>/ with their folder
structure, and "Restore" puts a whole batch back. Emptying the trash is a separate, explicit step.
Fixing and restoring are home-network only (auth.py); the report itself is fine anywhere."""

import json
import os
import re
import shutil
import threading
import time
import uuid

import requests
from fastapi import APIRouter, HTTPException, Request

from . import config, deezer, library_index, library_delete, navidrome, playlists, tagger
from .library_delete import same_origin
from .textmatch import text_score

router = APIRouter()
LOW_KBPS = 112
COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png")
COMPILATION = re.compile(r"greatest hits|best of|collection|essential|anthology|compilation|\bhits\b|number ones|the very best", re.I)
LOSSLESS = (".flac", ".wav")

_jobs: dict = {}
_lock = threading.Lock()


def _root() -> str:
    return os.path.realpath(config.DEFAULT_OUTPUT_DIR)


def _trash_root() -> str:
    return os.path.join(_root(), ".tuberipper", "trash")


def _has_embedded_cover(audio) -> bool:
    tags = getattr(audio, "tags", None)
    if tags is None:
        return False
    if hasattr(audio, "pictures") and audio.pictures:      # flac
        return True
    try:
        if any(k.startswith("APIC") for k in tags.keys()):  # mp3
            return True
        if tags.get("covr"):                               # m4a
            return True
        if tags.get("metadata_block_picture"):             # opus
            return True
    except Exception:
        pass
    return False


def _inspect(path: str) -> dict:
    from mutagen import File
    size = os.path.getsize(path)
    info = {"size": size, "ok": False, "bitrate": 0, "length": 0.0, "cover": False}
    if size == 0:
        return info
    try:
        audio = File(path)
    except Exception:
        return info
    if audio is None or not getattr(audio, "info", None):
        return info
    info.update(ok=True, bitrate=int((getattr(audio.info, "bitrate", 0) or 0) / 1000), length=float(getattr(audio.info, "length", 0) or 0),
                cover=_has_embedded_cover(audio))
    if info["length"] <= 0:
        info["ok"] = False
    elif not info["bitrate"]:                               # opus: the file does not state one
        info["bitrate"] = int(size * 8 / info["length"] / 1000)
    return info


def _podcast_bytes() -> int:
    """Size of the podcast audio (a separate folder from the music library; missing or unreadable = 0)."""
    total = 0
    for folder, _dirs, files in os.walk(os.environ.get("TUBERIPPER_PODCASTS_DIR", "/podcasts")):
        for f in files:
            if f.lower().endswith(tagger.AUDIO_EXTENSIONS):
                try:
                    total += os.path.getsize(os.path.join(folder, f))
                except OSError:
                    pass
    return total


def _walk(root: str):
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != playlists.PLAYLISTS_DIR]
        for f in files:
            if f.lower().endswith(tagger.AUDIO_EXTENSIONS):
                yield folder, f


def _quality(f: dict) -> tuple:
    """Higher is better: lossless, then not-a-compilation, then bitrate, then size."""
    return (f["ext"] in LOSSLESS, not COMPILATION.search(f["album"]), f["bitrate"], f["size"])


def scan_library(job: dict) -> None:
    root = _root()
    entries = list(_walk(root))
    job["total"] = len(entries)
    files = []
    for n, (folder, name) in enumerate(entries, 1):
        rel = os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/")
        parts = rel.split("/")
        stem, ext = os.path.splitext(name)
        title = library_index._parse(stem)[2]
        f = {"path": rel, "artist": parts[0] if len(parts) >= 2 else "", "album": parts[1] if len(parts) >= 3 else "", "title": title, "ext": ext.lower(),
             "lrc": os.path.exists(os.path.join(folder, stem + ".lrc"))}
        try:
            f.update(_inspect(os.path.join(folder, name)))
        except OSError:
            f.update(ok=False, size=0, bitrate=0, length=0.0, cover=False)
        files.append(f)
        job["scanned"] = n

    unreadable = [f for f in files if not f["ok"]]
    good = [f for f in files if f["ok"]]

    groups: dict = {}
    for f in good:
        if f["artist"]:                                    # loose files with no artist folder can't be matched up safely
            groups.setdefault(library_index.song_key(f["artist"], f["title"]), []).append(f)
    duplicates, several_albums = [], 0
    for g in groups.values():
        if len(g) < 2:
            continue
        ranked = sorted(g, key=_quality, reverse=True)
        keep = ranked[0]
        # Only real copies are offered for removal: the same recording (length within 3 s) sitting in the same album folder, or
        # in a greatest-hits style compilation of an album that has it. The same title on two different albums is left alone
        # (it may be a different recording, e.g. a skit, and removing it would leave an album with a hole in it).
        remove = [f for f in ranked[1:] if abs(f["length"] - keep["length"]) <= 3
                  and (f["album"] and (f["album"] == keep["album"] or (COMPILATION.search(f["album"]) and not COMPILATION.search(keep["album"]))))]
        if remove:
            duplicates.append({"keep": keep, "remove": remove})
        if len(remove) < len(ranked) - 1:
            several_albums += 1

    albums: dict = {}
    for f in good:
        albums.setdefault((f["artist"], f["album"]), []).append(f)
    no_cover = []
    for (artist, album), fs in albums.items():
        folder = os.path.join(root, artist, album)
        if not any(f["cover"] for f in fs) and not any(os.path.exists(os.path.join(folder, c)) for c in COVER_NAMES) and artist and album:
            no_cover.append({"artist": artist, "album": album, "songs": len(fs)})

    twins: dict = {}
    for (artist, album), fs in albums.items():
        base = re.sub(r"\s*[\(\[]\s*(?:19|20)\d\d\s*[\)\]]\s*$", "", album).strip()           # "An Innocent Man (1983)" is "An Innocent Man"
        twins.setdefault((library_index.fold(artist), library_index.fold(base)), []).append((artist, album, fs))
    twin_albums = [{"artist": g[0][0], "albums": [{"album": a, "songs": len(fs), "formats": sorted({f["ext"].lstrip(".") for f in fs}),
                                                   "kbps": int(sum(f["bitrate"] for f in fs) / len(fs))} for _, a, fs in g]}
                   for g in twins.values() if len(g) > 1 and g[0][0] and g[0][1]]

    low = [f for f in good if f["ext"] not in LOSSLESS and 0 < f["bitrate"] < LOW_KBPS and f["length"] >= 30]
    no_lyrics = [f for f in good if not f["lrc"]]
    job["result"] = {
        "songs": len(files), "bytes": sum(f["size"] for f in files), "podcast_bytes": _podcast_bytes(), "when": time.time(),
        "duplicates": duplicates, "unreadable": unreadable, "no_cover": sorted(no_cover, key=lambda a: (a["artist"], a["album"])),
        "low_quality": sorted(low, key=lambda f: f["bitrate"]), "no_lyrics": len(no_lyrics), "several_albums": several_albums, "twin_albums": sorted(twin_albums, key=lambda t: t["artist"]),
    }


def _run(job: dict) -> None:
    try:
        scan_library(job)
        job["status"] = "done"
    except Exception as exc:
        job["status"], job["error"] = "error", f"{type(exc).__name__}: {exc}"


def _public(job: dict) -> dict:
    out = {k: v for k, v in job.items() if k != "result"}
    r = job.get("result")
    if r:
        out["summary"] = {"songs": r["songs"], "bytes": r["bytes"], "podcast_bytes": r["podcast_bytes"], "when": r["when"], "duplicate_groups": len(r["duplicates"]),
                          "duplicate_files": sum(len(g["remove"]) for g in r["duplicates"]), "unreadable": len(r["unreadable"]),
                          "no_cover": len(r["no_cover"]), "low_quality": len(r["low_quality"]), "no_lyrics": r["no_lyrics"],
                          "several_albums": r["several_albums"], "twin_albums": len(r["twin_albums"])}
        out["duplicates"] = [{"keep": _brief(g["keep"]), "remove": [_brief(f) for f in g["remove"]]} for g in r["duplicates"]]
        out["unreadable"] = [_brief(f) for f in r["unreadable"]]
        out["no_cover"] = r["no_cover"]
        out["twin_albums"] = r["twin_albums"]
        out["low_quality"] = [_brief(f) for f in r["low_quality"]]
    return out


def _brief(f: dict) -> dict:
    return {k: f[k] for k in ("path", "artist", "album", "title", "bitrate", "size", "ext")}


@router.post("/api/libhealth/scan")
def start_scan():
    with _lock:
        running = next((j for j in _jobs.values() if j["status"] == "scanning"), None)
        if running:
            return _public(running)
        job = {"id": uuid.uuid4().hex[:10], "status": "scanning", "scanned": 0, "total": 0}
        _jobs.clear()
        _jobs[job["id"]] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return _public(job)


@router.get("/api/libhealth/scan/{job_id}")
def get_scan(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "That scan is gone (the server restarted). Scan again.")
    return _public(job)


# ---------------------------------------------------------------------------
# Fixes (home network only)
# ---------------------------------------------------------------------------

def _to_trash(rel_paths: list, batch: str) -> list:
    """Moves files (and their .lrc) into the trash batch, keeping their folders. Returns what moved."""
    root, moved = _root(), []
    for rel in rel_paths:
        src = os.path.realpath(os.path.join(root, rel))
        if not src.startswith(root + os.sep) or not os.path.isfile(src):
            continue
        for ext in ("", ".lrc"):
            s = src if not ext else os.path.splitext(src)[0] + ext
            if not os.path.isfile(s):
                continue
            r = os.path.relpath(s, root)
            dest = os.path.join(_trash_root(), batch, r)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(s, dest)
            moved.append(r.replace(os.sep, "/"))
        _prune_empty(os.path.dirname(src))
    if moved:
        with open(os.path.join(_trash_root(), batch, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"when": time.time(), "files": moved}, f)
    return moved


def _prune_empty(folder: str) -> None:
    root = _root()
    while folder.startswith(root + os.sep) and os.path.isdir(folder):
        left = [e for e in os.listdir(folder) if not (e.lower() in library_delete.COVERS)]
        if left:
            break
        for e in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, e))
            except OSError:
                pass
        try:
            os.rmdir(folder)
        except OSError:
            break
        folder = os.path.dirname(folder)


def _after_change() -> None:
    library_index.invalidate()
    try:
        navidrome.start_scan()
    except Exception:
        pass


def _fetch_cover(artist: str, album: str) -> bool:
    url = ""
    try:
        found = deezer.find_album(artist, album)
        url = (found or {}).get("cover") or ""
    except Exception:
        pass
    if not url:
        try:
            from . import catalog
            for r in catalog._itunes_albums(f"{artist} {album}", 5):
                if text_score(r["title"].lower(), album.lower()) >= 0.85 and r.get("artwork"):
                    url = r["artwork"]
                    break
        except Exception:
            pass
    if not url:
        return False
    r = requests.get(url, timeout=20, headers={"User-Agent": "tuberipper/1.0"})
    if r.status_code != 200 or len(r.content) < 5000 or not r.headers.get("content-type", "").startswith("image/"):
        return False
    folder = os.path.join(_root(), artist, album)
    if not os.path.isdir(folder):
        return False
    with open(os.path.join(folder, "cover.jpg"), "wb") as f:
        f.write(r.content)
    return True


@router.post("/api/libhealth/fix")
async def fix(request: Request):
    """Body: {"job": "<scan id>", "kind": "duplicates" | "unreadable" | "covers"} -- acts on that scan's findings."""
    if not same_origin(request):
        raise HTTPException(403, "Cross-site request refused.")
    try:
        b = await request.json()
        job, kind = _jobs.get(str(b["job"])), str(b["kind"])
    except Exception:
        raise HTTPException(400, "Send {job, kind}.")
    if not job or job["status"] != "done":
        raise HTTPException(409, "Scan again first: those results are gone.")
    res = job["result"]
    batch = time.strftime("%Y%m%d-%H%M%S")
    if kind == "duplicates":
        moved = _to_trash([f["path"] for g in res["duplicates"] for f in g["remove"]], batch)
        res["duplicates"] = []
    elif kind == "unreadable":
        moved = _to_trash([f["path"] for f in res["unreadable"]], batch)
        res["unreadable"] = []
    elif kind == "covers":
        done, failed = [], []
        for a in res["no_cover"]:
            try:
                (done if _fetch_cover(a["artist"], a["album"]) else failed).append(a)
            except Exception:
                failed.append(a)
        res["no_cover"] = failed
        _after_change()
        return {"fixed": len(done), "couldnt": len(failed)}
    else:
        raise HTTPException(400, "kind is duplicates, unreadable or covers.")
    _after_change()
    return {"moved": len([m for m in moved if not m.endswith(".lrc")]), "batch": batch if moved else None}


def _batches() -> list:
    out = []
    try:
        names = sorted(os.listdir(_trash_root()), reverse=True)
    except OSError:
        return out
    for n in names:
        try:
            with open(os.path.join(_trash_root(), n, "manifest.json"), encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        files = [x for x in m["files"] if not x.endswith(".lrc")]
        out.append({"batch": n, "when": m.get("when"), "songs": len(files), "examples": [os.path.splitext(os.path.basename(x))[0] for x in files[:3]]})
    return out


@router.get("/api/libhealth/trash")
def trash():
    return {"batches": _batches()}


@router.post("/api/libhealth/restore")
async def restore(request: Request):
    if not same_origin(request):
        raise HTTPException(403, "Cross-site request refused.")
    try:
        batch = str((await request.json())["batch"])
    except Exception:
        raise HTTPException(400, "Send {batch}.")
    base = os.path.realpath(os.path.join(_trash_root(), batch))
    if os.path.dirname(base) != os.path.realpath(_trash_root()) or not os.path.isdir(base):
        raise HTTPException(404, "No such trash batch.")
    root, back, skipped = _root(), 0, 0
    with open(os.path.join(base, "manifest.json"), encoding="utf-8") as f:
        files = json.load(f)["files"]
    for rel in files:
        src, dest = os.path.join(base, rel), os.path.join(root, rel)
        if not os.path.isfile(src):
            continue
        if os.path.exists(dest):
            skipped += 1
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)
        back += 1 if not rel.endswith(".lrc") else 0
    shutil.rmtree(base, ignore_errors=True) if skipped == 0 else None
    _after_change()
    return {"restored": back, "skipped": skipped}


@router.post("/api/libhealth/trash/empty")
async def empty_trash(request: Request):
    if not same_origin(request):
        raise HTTPException(403, "Cross-site request refused.")
    n = sum(b["songs"] for b in _batches())
    shutil.rmtree(_trash_root(), ignore_errors=True)
    return {"deleted": n}
