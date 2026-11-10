"""Listening history and the numbers built from it ("Your stats" and the year-in-music slideshow).

The app reports a play once you've heard 30 seconds (or half of a short song). Each play is one row in
<cache>/stats.db with what was played and when. Everything shown is simple counting over those rows: no
guessing, no AI. `python -m tuberipper.stats backfill <export.json>` imports history Navidrome kept from
before this existed."""

import datetime
import json
import os
import sqlite3
import sys
import threading
import time

from fastapi import APIRouter, HTTPException, Request

from . import config, navidrome
from .recommend import _NOT_MUSIC

router = APIRouter()
_lock = threading.Lock()
_conn = None


def _path() -> str:
    root = os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(config.DEFAULT_OUTPUT_DIR, ".tuberipper")
    return os.path.join(root, "stats.db")


def _db():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        _conn = sqlite3.connect(_path(), check_same_thread=False, timeout=10)
        _conn.execute("CREATE TABLE IF NOT EXISTS plays (uid TEXT PRIMARY KEY, ts INTEGER NOT NULL, title TEXT, artist TEXT, album TEXT,"
                      " nd_id TEXT, album_id TEXT, artist_id TEXT, cover TEXT, duration REAL)")
        _conn.execute("CREATE INDEX IF NOT EXISTS plays_ts ON plays (ts)")
        _conn.commit()
    return _conn


def reset() -> None:
    """Forget the open database (tests point the cache somewhere else)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


LONG_SECONDS = 600   # the app's own cut-off: longer than this is a podcast / long mix, not a song


def countable(p: dict) -> bool:
    """Music only: no podcast episodes, hour-long mixes or sleep sounds (they say nothing about taste)."""
    return float(p.get("duration") or 0) <= LONG_SECONDS and not _NOT_MUSIC.search(f"{p.get('artist') or ''} {p.get('album') or ''}")


def log_play(p: dict) -> bool:
    if not countable(p):
        return False
    row = (str(p["uid"])[:64], int(p.get("ts") or time.time()), str(p.get("title") or "")[:300], str(p.get("artist") or "")[:300],
           str(p.get("album") or "")[:300], str(p.get("nd_id") or "")[:64], str(p.get("album_id") or "")[:64],
           str(p.get("artist_id") or "")[:64], str(p.get("cover") or "")[:600], float(p.get("duration") or 0))
    with _lock:
        cur = _db().execute("INSERT OR IGNORE INTO plays VALUES (?,?,?,?,?,?,?,?,?,?)", row)
        _db().commit()
        return cur.rowcount > 0


@router.post("/api/stats/play")
async def record_play(request: Request):
    try:
        b = await request.json()
        if not b.get("uid") or not b.get("title"):
            raise ValueError
    except Exception:
        raise HTTPException(400, "Send {uid, title, artist, ...}.")
    return {"ok": True, "new": log_play(b)}


# ---------------------------------------------------------------------------

_EPOCH = datetime.datetime(1970, 1, 1)


def _local(ts: float, tz_offset: int) -> datetime.datetime:
    """The wall-clock time (naive) for a UTC timestamp. tz_offset is JavaScript's getTimezoneOffset() (minutes behind UTC)."""
    return _EPOCH + datetime.timedelta(seconds=ts - tz_offset * 60)


def _period_start(period: str, tz_offset: int, now: float) -> float:
    """Start of the period as a UTC timestamp. tz_offset is JavaScript's getTimezoneOffset() (minutes behind UTC)."""
    local_now = _local(now, tz_offset)
    if period == "week":
        start = (local_now - datetime.timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        start = (local_now - datetime.timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "year":
        start = local_now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return 0
    return (start - _EPOCH).total_seconds() + tz_offset * 60


def _persona(by_hour: list) -> str:
    buckets = {"Night owl": sum(by_hour[h] for h in (22, 23, 0, 1, 2, 3, 4)), "Early bird": sum(by_hour[h] for h in range(5, 10)),
               "Daytime listener": sum(by_hour[h] for h in range(10, 18)), "Evening listener": sum(by_hour[h] for h in range(18, 22))}
    return max(buckets, key=buckets.get) if sum(buckets.values()) else ""


def compute(period: str = "year", tz_offset: int = 0, now: float | None = None) -> dict:
    now = now or time.time()
    since = _period_start(period, tz_offset, now)
    with _lock:
        rows = _db().execute("SELECT ts,title,artist,album,nd_id,album_id,cover,duration FROM plays WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
        first_by_artist = dict(_db().execute("SELECT lower(artist), MIN(ts) FROM plays GROUP BY lower(artist)").fetchall())
        first_ever = _db().execute("SELECT MIN(ts) FROM plays").fetchone()[0]
    artists, songs, albums, days = {}, {}, {}, {}
    by_hour, by_weekday = [0] * 24, [0] * 7
    seconds = 0.0
    for ts, title, artist, album, nd_id, album_id, cover, duration in rows:
        local = _local(ts, tz_offset)
        by_hour[local.hour] += 1
        by_weekday[local.weekday()] += 1
        day = local.date().isoformat()
        d = days.setdefault(day, [0, 0.0])
        d[0] += 1
        d[1] += duration or 0
        seconds += duration or 0
        a = artists.setdefault(artist or "Unknown", {"name": artist or "Unknown", "plays": 0, "seconds": 0.0, "cover": "", "covers": {}})
        a["plays"] += 1
        a["seconds"] += duration or 0
        if cover:
            a["covers"][cover] = a["covers"].get(cover, 0) + 1
        s = songs.setdefault((title, artist), {"title": title, "artist": artist, "plays": 0, "cover": cover, "nd_id": nd_id})
        s["plays"] += 1
        if album:
            al = albums.setdefault((album, artist), {"album": album, "artist": artist, "plays": 0, "cover": cover, "album_id": album_id})
            al["plays"] += 1
    for a in artists.values():
        a["cover"] = max(a["covers"], key=a["covers"].get) if a["covers"] else ""
        a["minutes"] = round(a.pop("seconds") / 60)
        a.pop("covers")
    top = lambda d, n: sorted(d.values(), key=lambda x: -x["plays"])[:n]
    ordered_days = sorted(days)
    longest = run = 0
    prev = None
    for d in ordered_days:
        date = datetime.date.fromisoformat(d)
        run = run + 1 if prev and (date - prev).days == 1 else 1
        longest = max(longest, run)
        prev = date
    busiest = max(days.items(), key=lambda kv: kv[1][0], default=None)
    new_artists = sum(1 for a in artists if first_by_artist.get(a.lower(), 0) >= since) if since > 0 and first_ever and first_ever < since else None
    return {
        "period": period, "plays": len(rows), "minutes": round(seconds / 60), "unique_artists": len(artists), "unique_songs": len(songs),
        "new_artists": new_artists,
        "top_artists": top(artists, 10), "top_songs": top(songs, 10), "top_albums": top(albums, 6),
        "by_hour": by_hour, "by_weekday": by_weekday, "persona": _persona(by_hour),
        "busiest_day": {"date": busiest[0], "plays": busiest[1][0], "minutes": round(busiest[1][1] / 60)} if busiest else None,
        "longest_streak": longest, "active_days": len(days), "history_since": first_ever,
    }


@router.get("/api/stats")
def get_stats(period: str = "year", tz: int = 0):
    if period not in ("week", "month", "year", "all"):
        raise HTTPException(400, "period is week, month, year or all.")
    return compute(period, max(-840, min(840, tz)))


# ---------------------------------------------------------------------------
# One-time import of what Navidrome knew before this existed (run from the container; see HANDOFF.md)
# ---------------------------------------------------------------------------

def backfill(export_path: str) -> int:
    """export.json: [[navidrome media_file_id, unix seconds], ...] -> plays rows. Safe to run twice."""
    with open(export_path, encoding="utf-8") as f:
        rows = json.load(f)
    cache: dict = {}
    added = 0
    for mid, ts in rows:
        if mid not in cache:
            try:
                cache[mid] = navidrome._call("getSong", [("id", mid)]).get("song") or {}
            except navidrome.NavidromeError:
                cache[mid] = {}
        s = cache[mid]
        if not s.get("title"):
            continue
        added += log_play({"uid": f"nd:{mid}:{ts}", "ts": ts, "title": s["title"], "artist": s.get("artist"), "album": s.get("album"), "nd_id": mid,
                           "album_id": s.get("albumId"), "artist_id": s.get("artistId"), "cover": s.get("coverArt"), "duration": s.get("duration")})
    return added


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "backfill":
        print("imported", backfill(sys.argv[2]), "plays")
    else:
        sys.exit("usage: python -m tuberipper.stats backfill <export.json>")
