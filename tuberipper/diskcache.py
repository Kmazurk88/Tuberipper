"""A small persistent key -> JSON cache (SQLite, zlib-compressed values).

Used for MusicBrainz responses: the API allows ~1 request/second, so a song
costs ~6 s to look up. The answers barely change, so keeping them on disk
makes looking up the same song again (re-lookups, retries, a playlist pasted
twice, after a container restart) instant.

Lives in <library>/.tuberipper/cache.sqlite by default (the only persistent
storage the container has; Navidrome skips dot-folders), or
TUBERIPPER_CACHE_DIR. Any failure (read-only library, corrupt file) just
means no caching -- never an error.
"""

import json
import os
import sqlite3
import threading
import time
import zlib

from . import config

TTL = 30 * 24 * 3600
MAX_ROWS = 3000  # a recording search is ~50 KB compressed: ~150 MB at most

_lock = threading.Lock()
_conn = None
_disabled = False


def _path() -> str:
    root = os.environ.get("TUBERIPPER_CACHE_DIR")
    if not root:
        lib = config.DEFAULT_OUTPUT_DIR
        if lib.startswith(("\\\\", "//")):
            # The library is a network share (Windows dev runs): SQLite
            # locking over SMB isn't safe, and the server writes the same
            # file from its side. Keep a local cache instead.
            root = os.path.join(os.path.expanduser("~"), ".cache", "tuberipper")
        else:
            root = os.path.join(lib, ".tuberipper")
    return os.path.join(root, "cache.sqlite")


def _db():
    global _conn, _disabled
    if _conn is not None or _disabled:
        return _conn
    try:
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=5)
        conn.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, saved REAL, value BLOB)")
        conn.commit()
        _conn = conn
    except Exception:
        _disabled = True
    return _conn


def get(key: str):
    with _lock:
        conn = _db()
        if conn is None:
            return None
        try:
            row = conn.execute("SELECT saved, value FROM cache WHERE key = ?", (key,)).fetchone()
            if not row or time.time() - row[0] > TTL:
                return None
            return json.loads(zlib.decompress(row[1]))
        except Exception:
            return None


def put(key: str, value) -> None:
    with _lock:
        conn = _db()
        if conn is None:
            return
        try:
            blob = zlib.compress(json.dumps(value, separators=(",", ":")).encode("utf-8"))
            conn.execute("INSERT OR REPLACE INTO cache (key, saved, value) VALUES (?, ?, ?)", (key, time.time(), blob))
            # Keep it bounded: drop expired rows, then the oldest if still big.
            if conn.total_changes % 500 == 0:
                conn.execute("DELETE FROM cache WHERE saved < ?", (time.time() - TTL,))
                conn.execute(
                    "DELETE FROM cache WHERE key IN (SELECT key FROM cache ORDER BY saved DESC LIMIT -1 OFFSET ?)",
                    (MAX_ROWS,),
                )
            conn.commit()
        except Exception:
            pass


def reset() -> None:
    """Forget the open database (tests point the cache somewhere else)."""
    global _conn, _disabled
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn, _disabled = None, False
