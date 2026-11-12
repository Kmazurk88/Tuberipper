"""Keeps download jobs and playlist imports on disk (<cache>/jobs.db), so a restart (deploy, crash, reboot) doesn't
lose them: on start they are loaded again and whatever was running carries on (see server.resume_jobs and
spotify_import.resume).

Each record is one JSON blob per (kind, id). Jobs and imports change all the time (statuses, progress), so instead of
saving at every change a background thread saves whatever changed every SAVE_EVERY seconds, plus once at shutdown.
At most those few seconds of progress are lost, and the work they describe is simply done again (downloads skip songs
that are already in the library)."""

import json
import os
import sqlite3
import threading
import time
import traceback

SAVE_EVERY = 2.0

_lock = threading.Lock()
_conn = None
_conn_path = None
_saved: dict = {}          # (kind, id) -> the JSON last written, to skip unchanged records
_savers: list = []         # functions that call put() for everything they hold
_started = False
_frozen = False            # set at shutdown: see freeze()


def _path() -> str:
    root = os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(os.environ.get("TUBERIPPER_OUTPUT_DIR", "/music"), ".tuberipper")
    return os.path.join(root, "jobs.db")


def _db():
    """The connection for the current cache dir (tests switch it per test). Call with _lock held."""
    global _conn, _conn_path
    path = _path()
    if _conn is None or _conn_path != path:
        if _conn is not None:
            _conn.close()
        _saved.clear()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,"
                      " updated REAL NOT NULL, PRIMARY KEY (kind, id))")
        _conn_path = path
    return _conn


def put(kind: str, rid: str, data: dict) -> None:
    text = json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)
    with _lock:
        if _frozen:
            return
        db = _db()
        if _saved.get((kind, rid)) == text:
            return
        db.execute("INSERT INTO records (kind, id, data, updated) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT (kind, id) DO UPDATE SET data = excluded.data, updated = excluded.updated",
                   (kind, rid, text, time.time()))
        db.commit()
        _saved[(kind, rid)] = text


def delete(kind: str, rid: str) -> None:
    with _lock:
        _db().execute("DELETE FROM records WHERE kind = ? AND id = ?", (kind, rid))
        _db().commit()
        _saved.pop((kind, rid), None)


def load(kind: str) -> list:
    """Every record of this kind, oldest first. A record that can't be read is skipped (and kept for a look)."""
    with _lock:
        rows = _db().execute("SELECT id, data FROM records WHERE kind = ? ORDER BY updated", (kind,)).fetchall()
    out = []
    for rid, text in rows:
        try:
            data = json.loads(text)
        except ValueError:
            continue
        with _lock:
            _saved[(kind, rid)] = text
        out.append(data)
    return out


def register(saver) -> None:
    """saver() is called every few seconds and should put() what it holds."""
    if saver not in _savers:
        _savers.append(saver)


def save_now() -> None:
    for saver in list(_savers):
        try:
            saver()
        except Exception:
            traceback.print_exc()


def freeze() -> None:
    """Called at shutdown after the last save_now(). While the process winds down, background work fails in odd ways
    ("cannot schedule new futures after interpreter shutdown") and would mark jobs as failed; none of that may reach
    the disk. The restart then redoes that work from the last good state."""
    global _frozen
    with _lock:
        _frozen = True


def start() -> None:
    """Starts the background saver (once per process)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True

    def loop():
        while True:
            time.sleep(SAVE_EVERY)
            save_now()

    threading.Thread(target=loop, daemon=True, name="durable-save").start()
