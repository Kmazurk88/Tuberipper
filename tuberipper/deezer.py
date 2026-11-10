"""Deezer's public API: the fast first answer for a song or album.

Deezer allows ~50 requests per 5 seconds without a key (MusicBrainz: ~1 per
second), so a song's album, track number, cover, genre and date show up in
about a second. Its search ranks compilations, live versions and deluxe
reissues highly, though ("Lose Yourself" -> "Curtain Call: The Hits"), so
MusicBrainz still decides *which* album a song belongs to afterwards (see
server._refine_with_musicbrainz); Deezer then supplies that album's cover
and genre when it has it.
"""

import re
import threading
import time

import requests

from . import diskcache
from .textmatch import base_title, is_alt_version, text_score

API_ROOT = "https://api.deezer.com"

ARTIST_THRESHOLD = 0.6
TITLE_THRESHOLD = 0.8
# Albums that are someone else's collection of the song, not its home.
JUNK_ALBUM_RE = re.compile(
    r"\b(karaoke|tribute|lullab(y|ies)|in the style of|originally performed|made popular|covers?|"
    r"hits|greatest|best of|now that'?s music|workout|tabata|piano rendition|8-bit|for babies|"
    r"instrumentals?|remixes|live)\b",
    re.IGNORECASE,
)

VAGUE_GENRES = {"Asian Music", "African Music", "Latin Music", "Brazilian Music", "Indian Music", "Films/Games", "Kids"}

_lock = threading.Lock()
_calls: list = []  # timestamps of recent requests (rate limit: 50 per 5 s)
_session = requests.Session()


def _get(path: str, params: dict | None = None) -> dict | None:
    params = dict(params or {})
    key = "dz:" + path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    hit = diskcache.get(key)
    if hit is not None:
        return hit
    for attempt in range(3):
        with _lock:
            now = time.monotonic()
            while _calls and now - _calls[0] > 5:
                _calls.pop(0)
            if len(_calls) >= 40:  # stay under 50 per 5 s
                time.sleep(5 - (now - _calls[0]) + 0.05)
            _calls.append(time.monotonic())
        try:
            resp = _session.get(f"{API_ROOT}/{path}", params=params, timeout=15)
            data = resp.json()
        except (requests.RequestException, ValueError):
            return None
        err = data.get("error") if isinstance(data, dict) else None
        if err:
            if err.get("code") == 4:  # "Quota limit exceeded"
                time.sleep(1 + attempt)
                continue
            return None
        if data.get("data") or path.startswith(("album/", "track/")):
            diskcache.put(key, data)  # an empty search isn't kept (see musicbrainz._get)
        return data
    return None


def _artist_ok(wanted: str, name: str) -> bool:
    if not wanted:
        return True
    w, n = wanted.lower(), (name or "").lower()
    main = re.split(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b|\bx\b)\s*", w)[0].strip()
    return bool(n) and (main in n or n in w or text_score(main or w, n) >= ARTIST_THRESHOLD)


def album(album_id) -> dict | None:
    """{id, title, artist, record_type, release_date, genre, cover, tracks:
    [{id, title, title_short, artist, duration, track_position, disk_number, explicit}]}."""
    data = _get(f"album/{album_id}")
    if not data or not data.get("id"):
        return None
    tracks = (data.get("tracks") or {}).get("data") or []
    if not tracks or "track_position" not in tracks[0] or len(tracks) < (data.get("nb_tracks") or 0):
        full = _get(f"album/{album_id}/tracks", {"limit": 500})
        tracks = (full or {}).get("data") or tracks
    # Deezer's catch-all regional labels ("Asian Music") say less than
    # iTunes's ("J-Pop", "K-Pop"); leave those blank so iTunes fills them.
    genres = [g for g in (data.get("genres") or {}).get("data") or [] if g.get("name") not in VAGUE_GENRES]
    return {
        "id": data["id"],
        "title": data.get("title") or "",
        "artist": (data.get("artist") or {}).get("name") or "",
        "record_type": data.get("record_type") or "",
        "release_date": data.get("release_date") or "",
        "genre": genres[0]["name"] if genres else "",
        "cover": data.get("cover_xl") or data.get("cover_big") or "",
        "explicit": bool(data.get("explicit_lyrics")),
        "tracks": [{
            "id": t.get("id"),
            "title": t.get("title") or "",
            "title_short": t.get("title_short") or t.get("title") or "",
            "artist": (t.get("artist") or {}).get("name") or "",
            "duration": t.get("duration"),
            "track_position": t.get("track_position"),
            "disk_number": t.get("disk_number"),
            "explicit": bool(t.get("explicit_lyrics")),
        } for t in tracks],
    }


def _match_from(album_info: dict, t: dict) -> dict:
    """A match dict (same shape as metadata's) for track t on album_info."""
    return {
        "artist": t["artist"] or album_info["artist"],
        "album_artist": album_info["artist"],
        "title": t["title_short"] or t["title"],
        "album": album_info["title"],
        "year": (album_info["release_date"] or "")[:4],
        "genre": album_info["genre"],
        "artwork_url": album_info["cover"],
        "track_number": t.get("track_position"),
        "disc_number": t.get("disk_number"),
        "duration": t.get("duration"),
        "deezer_album_id": album_info["id"],
        "deezer_track_id": t.get("id"),
        "record_type": album_info["record_type"],
        "source": "deezer",
    }


# A Deezer version label that's still the same recording.
_SAME_RECORDING_RE = re.compile(r"remaster|feat|ft|from|with|mono|stereo", re.IGNORECASE)
_EDIT_RE = re.compile(r"radio edit|single version|single edit|radio version|album version", re.IGNORECASE)


def _version_penalty(version: str, wanted: str) -> float:
    """Deezer puts what makes a track a different take in title_version:
    "(12" Version)", "(Out-Take)", "(Alternate Mix)", "(Live ...)". Unless
    the user asked for it, that isn't the song."""
    v = (version or "").strip(" ()[]-").lower()
    if not v or v in (wanted or "").lower() or _SAME_RECORDING_RE.search(v):
        return 0
    return 1 if _EDIT_RE.search(v) else 3


def _score(r: dict, artist: str, title: str, target, explicit) -> float | None:
    if not _artist_ok(artist, (r.get("artist") or {}).get("name", "")):
        return None
    full = f"{r.get('title', '')} {r.get('title_version', '')}".strip()
    if text_score(base_title(title).lower(), base_title(r.get("title_short") or r.get("title", "")).lower()) < TITLE_THRESHOLD:
        return None
    score = (r.get("rank") or 0) / 1_000_000  # popularity, a tie-breaker
    if is_alt_version(full, title):
        score -= 3
    else:
        score -= _version_penalty(r.get("title_version", ""), title)
    album_title = (r.get("album") or {}).get("title", "")
    if JUNK_ALBUM_RE.search(album_title) and not JUNK_ALBUM_RE.search(title):
        score -= 1  # a compilation still has the right recording; MusicBrainz moves it to its album
    if target and r.get("duration"):
        diff = abs(r["duration"] - target)
        score += 2 if diff <= 3 else (1 if diff <= 8 else -1)
    if explicit is not None and bool(r.get("explicit_lyrics")) == bool(explicit):
        score += 0.5
    return score


def match_song(artist: str, title: str, target: float | None = None, explicit: bool | None = None) -> dict | None:
    """Deezer's best guess for a song: its album, track number, cover, genre."""
    data = _get("search", {"q": f"{artist} {base_title(title)}".strip(), "limit": 25})
    scored = [(s, r) for r in (data or {}).get("data", []) if (s := _score(r, artist, title, target, explicit)) is not None]
    if not scored:
        return None
    _, best = max(scored, key=lambda x: x[0])
    info = album((best.get("album") or {}).get("id"))
    if not info:
        return None
    t = next((x for x in info["tracks"] if x["id"] == best.get("id")), None)
    if t is None:
        t = {"id": best.get("id"), "title": best.get("title", ""), "title_short": best.get("title_short", ""),
             "artist": (best.get("artist") or {}).get("name", ""), "duration": best.get("duration"),
             "track_position": None, "disk_number": None, "explicit": bool(best.get("explicit_lyrics"))}
    return _match_from(info, t)


def same_album(a: str, b: str) -> bool:
    """Same album under different punctuation/edition labels: "8 Mile: Music
    From..." vs "8 Mile (Music From...)", "Nevermind" vs "Nevermind (Deluxe
    Edition)"."""
    from .metadata import canonicalize_album  # late import: metadata imports this module

    def key(t):
        return re.sub(r"[^0-9a-z]+", "", canonicalize_album(t or "").lower())
    ka, kb = key(a), key(b)
    return bool(ka) and (ka == kb or text_score(ka, kb) >= 0.92)


def find_album(artist: str, title: str) -> dict | None:
    """The Deezer album that is `title` by `artist` (any edition, plain one
    preferred), with its tracks -- or None."""
    if not title:
        return None
    data = _get("search/album", {"q": f"{artist} {title}".strip(), "limit": 25})
    hits = [a for a in (data or {}).get("data", [])
            if (_artist_ok(artist, (a.get("artist") or {}).get("name", ""))
                or (a.get("artist") or {}).get("name", "").lower() == "various artists")  # soundtracks
            and same_album(a.get("title", ""), title)]
    if not hits:
        return None
    # The plain title over "(Deluxe Edition)", an album over a single of the same name.
    hits.sort(key=lambda a: (a.get("title", "").lower() != title.lower(), a.get("record_type") != "album"))
    return album(hits[0]["id"])


def match_on_album(info: dict, artist: str, title: str, position: int | None = None) -> dict | None:
    """Track `title` on a known Deezer album, as a match dict."""
    want = base_title(title).lower()

    def score(t):
        s = text_score(want, base_title(t["title_short"] or t["title"]).lower())
        return s - (0.3 if is_alt_version(t["title"], title) else 0)

    best = max(info["tracks"], key=score, default=None)
    if best is None or score(best) < TITLE_THRESHOLD:
        if position and 0 < position <= len(info["tracks"]):
            best = info["tracks"][position - 1]
            if text_score(want, base_title(best["title_short"]).lower()) < 0.5:
                return None
        else:
            return None
    return _match_from(info, best)

