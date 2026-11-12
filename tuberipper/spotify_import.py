"""Import a Spotify playlist (an Exportify .csv) from the player's Create menu.

Each song is searched in the catalog (YouTube Music + iTunes), the best match
is added through the normal quick-add path (the app only downloads when its
own lookup is sure), and at the end the songs are put into one Navidrome
playlist. Runs in the background, one import at a time; the UI polls
/api/app/import/<id>. The matching rules come from the old import_playlist.py
script. It talks to this same server over localhost so every song takes the
same code path as adding one by hand.

Imports survive a restart: the import, its songs and each song's result are saved with the download jobs
(durable.py, every few seconds). On start, resume() carries on with an import that was running: songs that
already have a result are not looked at again, and a song whose download job was under way waits for that
same job (which carries on by itself) instead of starting a second one."""

import csv
import difflib
import io
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import uuid

import requests

from . import durable, navidrome, notify, radio
from .textmatch import artist_ok

BASE = os.environ.get("TUBERIPPER_SELF_URL", "http://127.0.0.1:8000")
MAX_ROWS = 1500
WORKERS = 5

NOISE = re.compile(
    r"\s*[-–]\s*(?:\d{4}\s+)?(?:remaster(?:ed)?(?:\s+\d{4})?|digital remaster|mono|stereo|single version|"
    r"radio edit|album version|\d{4} mix)\b.*$", re.I)
BRACKET = re.compile(r"[\(\[].*?[\)\]]")
# "Layla - 40th Anniversary Version / 2010 Remastered", "Mrs. Robinson - From "The Graduate" Soundtrack", "Shotgun - Extended Stereo Mix 2005":
# a " - " tail that only describes which release/mix this is (not "live": that is a different recording and is matched separately).
VERSIONY = re.compile(r"\s+[-–]\s+(?=[^-–]*\b(?:remaster(?:ed)?|version|edit|mix|soundtrack|from|anniversary|single|extended|mono|stereo|"
                      r"explicit|bonus|deluxe|billboard|chart hit|\d{4})\b).*$", re.I)
NOT_THE_SONG = re.compile(r"karaoke|tribute|in the style of|originally performed|cover version|made famous|lullaby|8-bit", re.I)

_jobs: dict = {}
_work: dict = {}    # import id -> {"rows", "results": {index: [result, meta, detail, label]}, "pending": {index: job id}, "target"}
_lock = threading.Lock()
_running = None
KIND = "import"
KEEP_FINISHED = 2 * 24 * 3600   # a finished import's result stays viewable (and on disk) this long


def parse_csv(text: str) -> list:
    rows = []
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        title = (row.get("Track Name") or "").strip()
        artists = [a.strip() for a in (row.get("Artist Name(s)") or "").replace(",", ";").split(";") if a.strip()]
        if title and artists:
            try:
                secs = int(row.get("Duration (ms)") or 0) / 1000
            except ValueError:
                secs = 0
            rows.append({"title": title, "artists": artists, "secs": secs})
    return rows


def _norm(s):
    s = BRACKET.sub(" ", s.lower())
    s = re.sub(r"[^a-z0-9 ]+", " ", s.replace("&", " and "))
    return re.sub(r"\s+", " ", s).strip()


def _clean(title):
    return VERSIONY.sub("", NOISE.sub("", title)).strip()


def _score(cand, artists, title, secs):
    """0..1 how well a catalog row fits the Spotify song; None = reject."""
    want_live = bool(re.search(r"\blive\b", title, re.I))
    if bool(re.search(r"\blive\b", cand.get("title", ""), re.I)) != want_live:
        return None
    t = difflib.SequenceMatcher(None, _norm(_clean(title)), _norm(_clean(cand.get("title", "")))).ratio()
    a = max((1.0 if artist_ok(x, cand.get("artist", "")) else 0.0) for x in artists)
    if t < 0.75 or a < 1.0:
        return None
    if NOT_THE_SONG.search(f"{cand.get('title', '')} {cand.get('artist', '')}") and not NOT_THE_SONG.search(f"{title} {' '.join(artists)}"):
        return None                                       # a karaoke / tribute version of the song, not the song
    d = cand.get("duration")
    dur = 0.5 if not (d and secs) else max(0.0, 1 - abs(d - secs) / 30)
    return 0.6 * t + 0.4 * dur


def _search(q):
    """Catalog songs for a query. An empty answer is retried: a throttled or failed lookup looks exactly like "no such song"."""
    for attempt in range(3):
        r = requests.get(f"{BASE}/api/search", params={"q": q, "songs_only": "true"}, timeout=90)
        r.raise_for_status()
        songs = r.json().get("songs", [])
        if songs:
            return songs
        time.sleep(2 * (attempt + 1))
    return []


def _find(artists, title, secs):
    # The artist then title, then (if that matched nothing) title first: YouTube Music ranks the two orders differently.
    for q in (f"{artists[0]} {_clean(title)}", f"{_clean(title)} {artists[0]}"):
        best = None
        for cand in _search(q):
            s = _score(cand, artists, title, secs)
            if s is not None and (best is None or s > best[0]):
                best = (s, cand)
        if best:
            return best[1]
    return None


def _job_alive(job_id):
    """True if this server still knows the job (after a restart: it was restored and carries on)."""
    try:
        return requests.get(f"{BASE}/api/jobs/{job_id}", timeout=30).status_code == 200
    except requests.RequestException:
        return False


def _wait_for(job_id, timeout=420):
    end, idle, job = time.time() + timeout, 0, {}
    while time.time() < end:
        job = requests.get(f"{BASE}/api/jobs/{job_id}", timeout=30).json()
        if job.get("status") in ("done", "error", "cancelled"):
            return job
        if job.get("status") == "awaiting_confirmation" and not job.get("refining"):
            idle += 1
            if idle >= 6:  # not auto-accepted: the lookup was unsure
                return job
        else:
            idle = 0
        time.sleep(1.5)
    return job


class _ServerGone(Exception):
    """This server stopped answering itself: it is shutting down (or restarting)."""


def _import_one_safe(row, pending=None):
    """_import_one, but "can't reach this very server" is not a result: that happens while the server shuts down, and
    the song must then be left for the restart (not reported as failed). Waits up to a minute for the server to answer
    again (a hiccup) and tries once more; raises _ServerGone if it doesn't."""
    for attempt in range(2):
        try:
            return _import_one(row, pending)
        except requests.ConnectionError:
            if attempt or not _wait_for_server(60):
                raise _ServerGone()


def _import_one(row, pending=None):
    """-> (result, meta, detail). result: added | already | review | nomatch | failed.
    pending: {"job", "meta"} of a download this song already started before a restart; wait for that one."""
    if pending and pending.get("job") and _job_alive(pending["job"]):
        meta, job_id = pending["meta"], pending["job"]
    else:
        cand = _find(row["artists"], row["title"], row["secs"])
        if not cand:
            return "nomatch", None, ""
        meta = {k: cand.get(k) for k in ("title", "artist", "album", "duration", "explicit", "video_id", "artwork", "year")}
        if cand.get("in_library"):
            return "already", meta, ""
        r = requests.post(f"{BASE}/api/jobs/catalog", json={"song": meta, "add": {"playlist_mode": "none", "notify": False}}, timeout=60)
        r.raise_for_status()
        job_id = r.json()["id"]
        if row.get("_on_job"):
            row["_on_job"]({"job": job_id, "meta": meta})
    job = _wait_for(job_id)
    tr = (job.get("tracks") or [{}])[0]
    if job.get("status") == "done" and tr.get("status") == "done":
        return "added", meta, ""
    if tr.get("status") == "skipped_exists":
        return "already", meta, ""
    if tr.get("status") == "failed" or job.get("status") == "error":
        return "failed", None, tr.get("detail") or job.get("error") or ""
    return "review", None, "needs a look in the classic view"


def save_upload(text: str, name: str) -> None:
    """Keeps the uploaded Exportify file (<cache>/import-files/), so a playlist can be checked against it later."""
    try:
        folder = os.path.join(os.path.dirname(_report_path()), "import-files")
        os.makedirs(folder, exist_ok=True)
        safe = re.sub(r"[^\w.-]+", "_", name)[:60] or "playlist"
        with open(os.path.join(folder, f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}.csv"), "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def start(rows: list, name: str, playlist_id: str | None = None) -> dict:
    """Returns the new import's state, or raises RuntimeError if one is running."""
    global _running
    with _lock:
        if _running and _jobs.get(_running, {}).get("status") == "running":
            raise RuntimeError("Another import is still running.")
        imp = {"id": uuid.uuid4().hex[:10], "name": name, "status": "running", "total": len(rows), "done": 0,
               "counts": {"added": 0, "already": 0, "review": 0, "nomatch": 0, "failed": 0}, "problems": [], "unplaced": [],
               "playlist": "", "playlist_id": None, "started": time.time()}
        _jobs[imp["id"]] = imp
        _work[imp["id"]] = {"rows": rows, "results": {}, "pending": {}, "target": playlist_id}
        _running = imp["id"]
    _save(imp["id"])
    threading.Thread(target=_run, args=(imp, rows, playlist_id), daemon=True).start()
    return imp


def _save(import_id) -> None:
    with _lock:
        imp, work = _jobs.get(import_id), _work.get(import_id)
        if not imp:
            return
        data = {"imp": dict(imp, counts=dict(imp["counts"]), problems=list(imp["problems"]))}
        if work:
            data["work"] = {"rows": work["rows"], "results": dict(work["results"]), "pending": dict(work["pending"]), "target": work["target"]}
    durable.put(KIND, import_id, data)


def save_all() -> None:
    for import_id in list(_jobs):
        _save(import_id)


def _recount(imp, rows, results) -> None:
    """counts / problems / done from the per-song results (after a restart, so nothing is counted twice)."""
    imp["counts"] = {"added": 0, "already": 0, "review": 0, "nomatch": 0, "failed": 0}
    imp["problems"] = []
    for i in sorted(results, key=int):
        result, _meta, detail, label = results[i]
        imp["counts"][result] += 1
        if result in ("review", "nomatch", "failed"):
            imp["problems"].append({"song": label, "result": result, "detail": detail})
    imp["done"] = len(results)


def _wait_for_server(timeout=180) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if requests.get(f"{BASE}/api/health", timeout=5).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def resume() -> list:
    """Loads saved imports; carries on with one that was running. Returns the ids it restarted."""
    global _running
    restarted = []
    for data in durable.load(KIND):
        imp, work = data.get("imp") or {}, data.get("work")
        if not imp.get("id"):
            continue
        if imp.get("status") != "running" and time.time() - imp.get("started", 0) > KEEP_FINISHED:
            durable.delete(KIND, imp["id"])
            continue
        with _lock:
            if imp["id"] in _jobs:
                continue
            _jobs[imp["id"]] = imp
            if imp.get("status") != "running":
                continue
            if not work or (_running and _running != imp["id"]):
                imp["status"], imp["playlist"] = "error", "Import stopped by a restart and could not carry on."
                continue
            work["results"] = {str(k): v for k, v in (work.get("results") or {}).items()}
            work["pending"] = {str(k): v for k, v in (work.get("pending") or {}).items()}
            _work[imp["id"]] = work
            _recount(imp, work["rows"], work["results"])
            imp["current"] = "Carrying on after a restart…"
            _running = imp["id"]

        def go(imp=imp, work=work):
            if not _wait_for_server():
                imp["status"], imp["playlist"] = "error", "Import stopped: the server did not come back after a restart."
                _save(imp["id"])
                return
            _run(imp, work["rows"], work["target"])
        threading.Thread(target=go, daemon=True).start()
        restarted.append(imp["id"])
    return restarted


def get(import_id: str):
    return _jobs.get(import_id)


def library_id(artists, title, meta=None):
    """The Navidrome id of this song, or None. Tries the artist(s) and title as Spotify wrote them, the catalog's spelling of
    them, and each with its release tail ("- Remaster", "(2015 Paisley Park Remaster)") removed, so "Paint It Black" finds
    "Paint It, Black" and "Stairway to Heaven (Remaster)" finds "Stairway to Heaven"."""
    meta = meta or {}
    titles = [t for t in (meta.get("title"), title, _clean(title), BRACKET.sub("", _clean(title)).strip(), _clean(meta.get("title") or ""),
                          BRACKET.sub("", _clean(meta.get("title") or "")).strip()) if t]
    names = [n for n in (meta.get("artist"), *artists) if n]
    for t in dict.fromkeys(titles):
        for a in dict.fromkeys(names):
            hit = radio.library_song(a, t)
            if hit:
                return hit["id"]
    return None


def _playlist_ids(entries, retries=2, with_names=False):
    """[(artists, title, meta)] -> (ids in order, how many weren't found). A just-finished scan can lag, so misses are retried."""
    ids = [None] * len(entries)
    for attempt in range(retries + 1):
        for i, (artists, title, meta) in enumerate(entries):
            if ids[i] is None:
                ids[i] = library_id(artists, title, meta) or navidrome.find_song_id(meta)   # the old exact matcher as a fallback
        if all(ids) or attempt == retries:
            break
        time.sleep(5)
    found = [i for i in ids if i]
    if with_names:   # the songs that are in the library (or were just added) but could not be put in the playlist
        return found, len(ids) - len(found), [f"{', '.join(a)} - {t}" for (a, t, _m), sid in zip(entries, ids) if not sid]
    return found, len(ids) - len(found)


def _run(imp, rows, playlist_id):
    metas, gone = [], False
    state = _work.setdefault(imp["id"], {"rows": rows, "results": {}, "pending": {}, "target": playlist_id})
    try:
        lock = threading.Lock()

        def work(item):
            i, row = item
            key = str(i)
            label = f"{', '.join(row['artists'])} - {row['title']}"
            with lock:
                done = state["results"].get(key)
            if done:   # finished before a restart
                return (row["artists"], row["title"], done[1]) if done[1] else None

            def on_job(p):
                with lock:
                    state["pending"][key] = p
            try:
                result, meta, detail = _import_one_safe(dict(row, _on_job=on_job), state["pending"].get(key))
            except _ServerGone:
                raise
            except Exception as exc:
                result, meta, detail = "failed", None, f"{type(exc).__name__}: {exc}"
            with lock:
                state["results"][key] = [result, meta, detail, label]
                state["pending"].pop(key, None)
                imp["counts"][result] += 1
                if result in ("review", "nomatch", "failed"):
                    imp["problems"].append({"song": label, "result": result, "detail": detail})
                imp["done"] += 1
                imp["current"] = label
            return (row["artists"], row["title"], meta) if meta else None

        # A few songs at a time; map() keeps the CSV order.
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            metas = [m for m in pool.map(work, enumerate(rows)) if m]
        _make_playlist(imp, metas, playlist_id, imp["counts"]["added"] > 0)
        imp["status"] = "done"
    except _ServerGone:
        # The server is going down: leave the import "running" with the songs that have no result yet; resume()
        # carries on after the restart. No report, no Discord message for a run that isn't finished.
        gone = True
    except Exception as exc:
        imp["status"] = "error"
        imp["playlist"] = f"Import stopped: {type(exc).__name__}: {exc}"
    finally:
        imp["current"] = ""
        if gone:
            return
        _work.pop(imp["id"], None)    # the songs list is only needed while running
        _save(imp["id"])
        _save_report(imp)
        _discord(imp)


def _make_playlist(imp, metas, playlist_id, scan_first):
    if not metas:
        imp["playlist"] = "No songs were found, so no playlist was made."
        return
    if not navidrome.configured():
        imp["playlist"] = "Songs were added, but Navidrome isn't connected so there is no playlist."
        return
    try:
        if scan_first:
            navidrome.wait_for_scan()
        ids, missing, unplaced = _playlist_ids(metas, retries=2, with_names=True)
        imp["unplaced"] = unplaced
        if not ids:
            imp["playlist"] = "Navidrome couldn't find the songs, so the playlist wasn't made."
            return
        # The same song may appear twice in a CSV; keep the first.
        ids = list(dict.fromkeys(ids))
        if playlist_id:
            navidrome.add_to_playlist(playlist_id, ids)
        else:
            navidrome.create_playlist(imp["name"], ids)
            for p in navidrome.list_playlists():
                if p["name"] == imp["name"]:
                    imp["playlist_id"] = p["id"]
        imp["playlist"] = f"Playlist “{imp['name']}” has {len(ids)} song{'' if len(ids) == 1 else 's'}" + (
            f" ({missing} not found in Navidrome)." if missing else ".")
    except navidrome.NavidromeError as exc:
        imp["playlist"] = f"Songs were added but the playlist failed: {exc}"


def _report_path():
    root = os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(os.environ.get("TUBERIPPER_OUTPUT_DIR", "/music"), ".tuberipper")
    return os.path.join(root, "import-reports.json")


def _save_report(imp):
    """Keeps every finished import's results (what was missed) in a file, so a report can be made later."""
    try:
        path = _report_path()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = []
        data.append({"name": imp["name"], "status": imp["status"], "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "total": imp["total"], "counts": imp["counts"], "problems": imp["problems"], "playlist": imp["playlist"],
                     "unplaced": imp.get("unplaced", [])})
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass


def _footer(imp):
    secs = max(1, int(time.time() - imp["started"]))
    added = imp["counts"]["added"]
    text = f"{imp['total']} songs in {secs // 60}m{secs % 60:02d}s · {WORKERS} at once"
    if added:
        text += f" · {added / (secs / 60):.1f} downloaded/min"
    return text


def _who():
    label = os.environ.get("INSTANCE_LABEL", "").strip()
    return f"[{label}] " if label else ""


def _discord(imp):
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    c = imp["counts"]
    lines = [f"✅ {c['added']} added", f"📁 {c['already']} already in library"]
    for key, icon, label in (("review", "⚠️", "need a look"), ("nomatch", "❓", "not found"), ("failed", "❌", "failed")):
        items = [p for p in imp["problems"] if p["result"] == key]
        if items:
            lines.append(f"{icon} {len(items)} {label}:")
            lines += [f"   • {p['song']}" for p in items[:15]]
    if imp.get("unplaced"):
        lines.append(f"📋 {len(imp['unplaced'])} in the library but not in the playlist:")
        lines += [f"   • {u}" for u in imp["unplaced"][:10]]
    if imp["playlist"]:
        lines.append(imp["playlist"])
    try:
        requests.post(url, json={"embeds": [{"title": f"{_who()}Playlist import: {imp['name']}", "color": notify.COLOR_OK if not imp["problems"] else notify.COLOR_WARN,
                                             "description": "\n".join(lines)[:4000],
                                             "footer": {"text": _footer(imp)}}]},
                      headers={"User-Agent": "tuberipper"}, timeout=10)
    except requests.RequestException:
        pass
