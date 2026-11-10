"""Fetches lyrics (.lrc format) from the LRCLIB API (free, no auth).

Synced lyrics are timed to one specific recording. They're only used when
the downloaded audio is the same length as that recording (within
SYNC_TOLERANCE seconds) -- otherwise every line lands early or late, which
is worse than no timing at all. In that case the plain (unsynced) text is
saved instead.
"""

import re
from difflib import SequenceMatcher

import requests

LRCLIB_GET_URL = "https://lrclib.net/api/get"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
HEADERS = {"User-Agent": "tuberipper"}

SYNC_TOLERANCE = 3  # seconds
TIMESTAMP_RE = re.compile(r"^\s*(\[\d+:\d+(?:[.:]\d+)?\]\s*)+", re.MULTILINE)


def _close(a, b, tol=SYNC_TOLERANCE) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def _plain(record: dict) -> str | None:
    if record.get("plainLyrics"):
        return record["plainLyrics"]
    if record.get("syncedLyrics"):
        text = TIMESTAMP_RE.sub("", record["syncedLyrics"]).strip()
        return text or None
    return None


def fetch_lrc(artist: str, title: str, album: str = "", official_duration: float | None = None,
              audio_duration: float | None = None):
    """Returns (text, kind): kind is "synced", "plain", or None (nothing
    found). Synced lyrics are only returned when their recording's length
    matches the downloaded audio (`audio_duration`)."""
    audio = audio_duration or official_duration
    # Audio that isn't the album-length recording (a music video kept on
    # purpose) can't use any synced lyrics.
    sync_possible = not (official_duration and audio_duration
                         and not _close(official_duration, audio_duration))

    def synced_ok(record):
        return (sync_possible and record.get("syncedLyrics")
                and (not audio or not record.get("duration") or _close(record["duration"], audio)))

    plain_fallback = None

    lookup_duration = official_duration or audio_duration
    if lookup_duration:
        try:
            resp = requests.get(
                LRCLIB_GET_URL,
                params={"artist_name": artist, "track_name": title, "album_name": album,
                        "duration": round(lookup_duration)},
                headers=HEADERS, timeout=10,
            )
            if resp.ok:
                data = resp.json() or {}
                if synced_ok(data):
                    return data["syncedLyrics"], "synced"
                plain_fallback = _plain(data)
        except (requests.RequestException, ValueError):
            pass

    try:
        resp = requests.get(LRCLIB_SEARCH_URL, params={"artist_name": artist, "track_name": title},
                            headers=HEADERS, timeout=10)
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError):
        results = []
    if not isinstance(results, list):
        results = []

    # The search is fuzzy; only take a result that's actually this song, or
    # a different song's lyrics end up next to the file.
    want = _norm(title)
    same_song = [
        r for r in results
        if _norm(r.get("trackName", "")) == want
        or SequenceMatcher(None, _norm(r.get("trackName", "")), want).ratio() >= 0.85
    ]
    # Closest length first, so the best-timed version wins.
    if audio:
        same_song.sort(key=lambda r: abs((r.get("duration") or 1e9) - audio))
    for r in same_song:
        if synced_ok(r):
            return r["syncedLyrics"], "synced"
    if plain_fallback:
        return plain_fallback, "plain"
    for r in same_song:
        text = _plain(r)
        if text:
            return text, "plain"
    return None, None


def _norm(s: str) -> str:
    s = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", s or "")  # drop (feat. X), [Remastered] etc.
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
