"""Song / artist radio: "more like this", used by the "Start radio" buttons and by autoplay
when the queue runs out.

YouTube Music builds the radio (its "watch playlist" of similar songs). Songs you already
have come back with their Navidrome record so they play from the library; everything else
streams through /api/preview, exactly like the Home recommendations, and is NOT added to the
library unless you add it."""

import re
import threading
import time

from fastapi import APIRouter, HTTPException

from . import catalog, config, library_index, navidrome, recommend
from .discover import _norm, _song_payload, _thumb
from .textmatch import artist_ok

router = APIRouter()
VIDEO_ID = re.compile(r"^[\w-]{11}$")
_cache: dict = {}
_lock = threading.Lock()
TTL = 15 * 60
SLIM = ("id", "title", "artist", "album", "albumId", "artistId", "coverArt", "duration", "track", "suffix", "year", "genre")


def slim(song: dict) -> dict:
    """The Navidrome song fields the app uses."""
    return {k: song[k] for k in SLIM if k in song}


def library_song(artist: str, title: str) -> dict | None:
    """This song as Navidrome knows it, or None (not in the library, or not scanned yet)."""
    try:
        # Words only: Navidrome's search returns nothing for a query holding "&", brackets or quotes.
        q = re.sub(r"[^\w\s]+", " ", f"{title} {catalog.main_artist(artist)}")
        body = navidrome._call("search3", [("query", " ".join(q.split())), ("songCount", "20"), ("artistCount", "0"), ("albumCount", "0")])
    except navidrome.NavidromeError:
        return None
    want_t, want_a = _norm(title), catalog.main_artist(artist)
    for s in recommend._list(body.get("searchResult3", {}).get("song")):
        have = catalog.main_artist(s.get("artist", ""))
        if _norm(s.get("title", "")) == want_t and (artist_ok(want_a, have) or _norm(have) == _norm(want_a)):
            return slim(s)
    return None


def seed_video(video_id: str = "", artist: str = "", title: str = ""):
    """A YouTube Music video id to start the radio from."""
    if video_id and VIDEO_ID.match(video_id):
        return video_id
    if artist and title:
        found = recommend._find_song(artist, title)
        if found:
            return found["video_id"]
        for r in catalog.ytm().search(f"{catalog.main_artist(artist)} {title}", filter="songs", limit=3) or []:
            if r.get("videoId"):
                return r["videoId"]
    if artist:
        bid = recommend._artist_id(artist)
        songs = recommend._artist_page(bid)["songs"] if bid else []
        if songs:
            return songs[0]["video_id"]
    return None


def _seconds(length) -> int | None:
    try:
        parts = [int(x) for x in str(length).split(":")]
    except ValueError:
        return None
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts))) if parts else None


def radio_for(video_id: str, limit: int) -> list:
    key = (video_id, limit)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    wp = catalog.ytm().get_watch_playlist(videoId=video_id, radio=True, limit=limit + 5)
    out, seen = [], set()
    for t in wp.get("tracks") or []:
        vid = t.get("videoId")
        if not vid or vid == video_id or (t.get("videoType") or "").endswith("UGC"):
            continue                                              # the seed itself, and random uploads that aren't songs
        artists = t.get("artists") or ([{"name": t["artist"]}] if t.get("artist") else [])
        song = _song_payload({"videoId": vid, "title": t.get("title"), "artists": artists, "album": t.get("album"),
                              "duration_seconds": _seconds(t.get("length")), "thumbnails": t.get("thumbnail") or t.get("thumbnails")})
        song["artwork"] = _thumb(t.get("thumbnail") or t.get("thumbnails"), 544)
        k = (_norm(song["artist"]), _norm(song["title"]))
        if k in seen or not song["title"]:
            continue
        seen.add(k)
        out.append(song)
    with _lock:
        _cache[key] = (time.time(), out)
        if len(_cache) > 60:
            _cache.clear()
    return out


@router.get("/api/radio")
def radio(video_id: str = "", artist: str = "", title: str = "", limit: int = 25):
    limit = max(5, min(limit, 40))
    try:
        vid = seed_video(video_id, artist, title)
        if not vid:
            raise HTTPException(404, "Couldn't find that song online, so there's no radio for it.")
        tracks = radio_for(vid, limit)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Couldn't build a radio: {type(exc).__name__}")
    songs = []
    for s in tracks[:limit]:
        owned = library_index.has_song(config.DEFAULT_OUTPUT_DIR, s["artist"], s["title"])
        lib = library_song(s["artist"], s["title"]) if owned else None
        songs.append(dict(s, in_library=bool(lib), lib=lib))
    return {"seed": vid, "songs": songs}
