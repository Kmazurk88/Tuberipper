"""Home-screen recommendations: music you don't own yet, built from what you play.

Two kinds of shelf, both made of songs that can be played at once (YouTube Music
video ids, streamed through /api/preview) and added to the library on tap:

* "Discover": artists related (YouTube Music) to the ones you play most, minus
  anyone you already have, one or two of their top songs each.
* "Because you listened to X": Last.fm's community "similar artists" for your top
  artists, and each one's most popular song you don't have. Needs a free Last.fm
  API key, pasted at /setup (read-only; no Last.fm account or scrobbling needed).

What you play comes from Navidrome (most-played albums, recently played albums,
liked songs). Podcasts are ignored. The finished shelves are cached for a few
hours and rebuilt in the background, so Home never waits on YouTube.
"""

import datetime
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from fastapi import APIRouter

from . import catalog, config, diskcache, library_index, navidrome
from .discover import _norm, _song_payload, _thumb
from .textmatch import text_score

router = APIRouter()

LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
TTL = 4 * 3600
SEEDS = 8              # artists whose related artists feed "Discover"
LASTFM_SHELVES = 3     # "Because you listened to ..." shelves
SHELF_SONGS = 12
MIN_SHELF = 4          # a shelf with fewer songs than this isn't worth showing
WARM_SONGS = 3         # songs per shelf whose stream is resolved ahead of the tap

_warm = lambda video_id: None  # set by server.py (it owns the stream cache)
_pool = ThreadPoolExecutor(max_workers=6)
_lock = threading.Lock()
_state = {"data": None, "built": 0.0, "building": False}


def set_warmer(fn) -> None:
    global _warm
    _warm = fn


# ---------------------------------------------------------------------------
# Last.fm
# ---------------------------------------------------------------------------

def key_path() -> str:
    root = os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(config.DEFAULT_OUTPUT_DIR, ".tuberipper")
    return os.environ.get("LASTFM_KEY_FILE") or os.path.join(root, "lastfm-key.txt")


def lastfm_key() -> str:
    try:
        with open(key_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _lastfm(method: str, **params) -> dict:
    r = requests.get(LASTFM_URL, params={"method": method, "api_key": lastfm_key(), "format": "json", **params},
                     headers={"User-Agent": "tuberipper/1.0 (personal media server)"}, timeout=12)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Last.fm error {data['error']}: {data.get('message', '')}")
    return data


def check_lastfm_key(value: str) -> None:
    """Raises ValueError with a plain sentence when Last.fm doesn't accept the key."""
    try:
        r = requests.get(LASTFM_URL, params={"method": "chart.gettopartists", "limit": 1, "api_key": value, "format": "json"}, timeout=10)
    except requests.RequestException:
        raise ConnectionError("Couldn't reach Last.fm to check the key, so it wasn't saved.")
    if r.status_code >= 500:
        raise ConnectionError(f"Last.fm answered {r.status_code}, so the key wasn't saved.")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if body.get("error") in (10, 26):
        raise ValueError("Last.fm refused that key. Copy the \"API key\" (not the shared secret) from last.fm/api/accounts.")
    if body.get("error") or r.status_code >= 400:
        raise ConnectionError("Last.fm didn't accept the key, so it wasn't saved.")


def _similar_artists(name: str) -> list:
    def go():
        d = _lastfm("artist.getsimilar", artist=name, limit=30, autocorrect=1)
        return [a["name"] for a in (d.get("similarartists") or {}).get("artist", []) if a.get("name")]
    return _cached(f"rec:lfm-similar:{_norm(name)}", go)


def _top_tracks(name: str) -> list:
    def go():
        d = _lastfm("artist.gettoptracks", artist=name, limit=8, autocorrect=1)
        return [t["name"] for t in (d.get("toptracks") or {}).get("track", []) if t.get("name")]
    return _cached(f"rec:lfm-top:{_norm(name)}", go)


# ---------------------------------------------------------------------------
# YouTube Music
# ---------------------------------------------------------------------------

def _cached(key, fn):
    hit = diskcache.get(key)
    if hit is not None:
        return hit
    value = fn()
    diskcache.put(key, value)
    return value


def _artist_id(name: str):
    from .discover import resolve_artist
    return resolve_artist(name)


def _artist_page(browse_id: str) -> dict:
    """Related artists and a few top songs, one YouTube Music request."""
    def go():
        a = catalog.ytm().get_artist(browse_id)
        return {
            "name": a.get("name") or "",
            "related": [{"name": r.get("title") or "", "browse_id": r["browseId"]} for r in (a.get("related") or {}).get("results", []) if r.get("browseId")][:12],
            "songs": [_song_payload(t, a.get("name") or "") for t in (a.get("songs") or {}).get("results", []) if t.get("videoId")][:4],
        }
    return _cached("rec:artist:" + browse_id, go)


def _find_song(artist: str, title: str):
    """The YouTube Music song that is this artist + title, or None."""
    def go():
        for r in catalog.ytm().search(f"{artist} {title}", filter="songs", limit=5) or []:
            if r.get("resultType") != "song" or not r.get("videoId"):
                continue
            names = [x.get("name", "") for x in r.get("artists") or []]
            if text_score(_norm(title), _norm(r.get("title", ""))) >= 0.85 and catalog.artist_match(artist, names) >= 0.85:
                return _song_payload(r, artist)
        return {}
    return _cached(f"rec:song:{_norm(artist)}|{_norm(title)}", go) or None


# ---------------------------------------------------------------------------
# What you play
# ---------------------------------------------------------------------------

def _list(x):
    return x if isinstance(x, list) else ([x] if x else [])


def _music_folder():
    folders = _list(navidrome._call("getMusicFolders").get("musicFolders", {}).get("musicFolder"))
    return next((f["id"] for f in folders if "podcast" not in f.get("name", "").lower()), None)


# Sleep sounds, white noise and the like are played a lot but say nothing about taste.
_NOT_MUSIC = re.compile(r"\b(white noise|deep sleep|sleep (?:sounds?|music|aid)|rain sounds?|relax(?:ing|ation)|meditation|asmr|nature sounds?|lullab\w*|binaural|spa therapy|healing sounds?)\b", re.I)


def top_artists(limit: int = SEEDS * 2) -> list:
    """Artist names ranked by how much you play them, most-played first."""
    folder = _music_folder()
    extra = [("musicFolderId", str(folder))] if folder is not None else []
    weight: dict = {}
    names: dict = {}

    def add(name, w, album=""):
        if _NOT_MUSIC.search(f"{name} {album}"):
            return
        name = catalog.main_artist(name or "")
        if name and _norm(name) not in ("various artists", "unknown artist", ""):
            weight[_norm(name)] = weight.get(_norm(name), 0) + w
            names.setdefault(_norm(name), name)

    for rank, a in enumerate(_list(navidrome._call("getAlbumList2", [("type", "frequent"), ("size", "40")] + extra).get("albumList2", {}).get("album"))):
        add(a.get("artist"), (a.get("playCount") or 1) + 1.0 / (rank + 1), a.get("name", ""))
    for rank, a in enumerate(_list(navidrome._call("getAlbumList2", [("type", "recent"), ("size", "15")] + extra).get("albumList2", {}).get("album"))):
        add(a.get("artist"), 3.0 / (rank + 1), a.get("name", ""))
    try:
        for s in _list(navidrome._call("getStarred2").get("starred2", {}).get("song")):
            add(s.get("artist"), 2)
    except navidrome.NavidromeError:
        pass
    ranked = [names[k] for k in sorted(weight, key=weight.get, reverse=True)]
    if len(ranked) < SEEDS:
        # Little or no play history yet (a new library, or one built from imported playlists):
        # the artists with the most songs in the library stand in for taste.
        counts: dict = {}
        for song in library_index.all_songs(config.DEFAULT_OUTPUT_DIR):
            n = catalog.main_artist(song.get("artist") or "")
            if n and not _NOT_MUSIC.search(f"{n} {song.get('album', '')}") and _norm(n) not in ("various artists", "unknown artist", ""):
                counts.setdefault(_norm(n), [n, 0])[1] += 1
        have = {_norm(x) for x in ranked}
        ranked += [n for k, (n, c) in sorted(counts.items(), key=lambda kv: -kv[1][1]) if k not in have][:SEEDS - len(ranked)]
    return ranked[:limit]


# ---------------------------------------------------------------------------
# Shelves
# ---------------------------------------------------------------------------

def _owned(song: dict) -> bool:
    return library_index.has_song(config.DEFAULT_OUTPUT_DIR, song.get("artist", ""), song.get("title", ""))


def _discover_shelf(seeds: list) -> dict | None:
    """Songs by artists related to your favourites that you don't have a single song of."""
    def related_of(seed):
        try:
            bid = _artist_id(seed)
            return [(seed, r) for r in _artist_page(bid)["related"]] if bid else []
        except Exception:
            return []

    per_seed = list(_pool.map(related_of, seeds))
    mine = {_norm(s) for s in seeds}
    pairs, seen = [], set()
    for rank in range(12):                                   # take turns between seeds so one artist can't fill the row
        for rel in per_seed:
            if rank < len(rel):
                seed, r = rel[rank]
                if _norm(r["name"]) not in seen | mine:
                    seen.add(_norm(r["name"]))
                    pairs.append((seed, r))

    def songs_of(pair):
        seed, r = pair
        try:
            return [dict(s, because=seed) for s in _artist_page(r["browse_id"])["songs"] if not _owned(s)][:1]
        except Exception:
            return []

    songs = [s for batch in _pool.map(songs_of, pairs[:SHELF_SONGS * 2]) for s in batch][:SHELF_SONGS]
    if len(songs) < MIN_SHELF:
        return None
    return {"id": "discover", "title": "Discover", "sub": "Artists you might like, based on what you play", "songs": songs}


def _because_shelf(seed: str) -> dict | None:
    """Last.fm's most-played song of each artist similar to `seed`, skipping what you own."""
    try:
        similar = [a for a in _similar_artists(seed) if _norm(a) != _norm(seed)][:SHELF_SONGS * 2]
    except Exception:
        return None

    def pick(artist):
        try:
            for title in _top_tracks(artist):
                if library_index.has_song(config.DEFAULT_OUTPUT_DIR, artist, title):
                    continue
                song = _find_song(artist, title)
                if song and not _owned(song):
                    return dict(song, because=seed)
        except Exception:
            pass
        return None

    songs = [s for s in _pool.map(pick, similar) if s][:SHELF_SONGS]
    if len(songs) < MIN_SHELF:
        return None
    return {"id": "because:" + _norm(seed), "title": f"Because you listened to {seed}", "sub": "", "songs": songs}


def _dedupe(shelves: list) -> list:
    """A song appears once on Home, in the first shelf that has it; shelves left too short are dropped."""
    seen, out = set(), []
    for sh in shelves:
        songs = []
        for s in sh["songs"]:
            k = (s.get("video_id"), _norm(s.get("artist", "")), _norm(s.get("title", "")))
            if k[0] in seen or k[1:] in seen:
                continue
            seen.update((k[0], k[1:]))
            songs.append(s)
        if len(songs) >= MIN_SHELF:
            out.append(dict(sh, songs=songs))
    return out


def build() -> dict:
    seeds = top_artists()
    shelves = []
    if seeds:
        # The top artists, in an order that changes each day so the shelves don't look the same every visit.
        day = random.Random(datetime.date.today().toordinal())
        pool = seeds[:SEEDS]
        day.shuffle(pool)
        d = _discover_shelf(pool[:5])
        if d:
            shelves.append(d)
        if lastfm_key():
            futures = [_pool.submit(_because_shelf, s) for s in pool[:LASTFM_SHELVES + 2]]
            shelves += [s for s in (f.result() for f in futures) if s]
        shelves = _dedupe(shelves)[:1 + LASTFM_SHELVES]
    return {"shelves": shelves, "built": time.time(), "lastfm": bool(lastfm_key()), "seeds": seeds[:SEEDS]}


def _refresh_bg():
    try:
        data = build()
        diskcache.put("rec:home", data)
        with _lock:
            _state.update(data=data, built=data["built"])
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        with _lock:
            _state["building"] = False


def _current(force: bool):
    """Cached shelves (rebuilding in the background when stale); builds inline only when there is nothing at all."""
    with _lock:
        data, fresh = _state["data"], time.time() - _state["built"] < TTL and not force
        if data is None:
            data = diskcache.get("rec:home")
            if data:
                _state.update(data=data, built=data.get("built", 0))
                fresh = time.time() - data.get("built", 0) < TTL and not force
        start = not fresh and not _state["building"]
        if start:
            _state["building"] = True
    if data is None:
        _refresh_bg() if start else _wait_for_build()
        return _state["data"]
    if start:
        threading.Thread(target=_refresh_bg, daemon=True).start()
    return data


def _wait_for_build(timeout: float = 90):
    end = time.time() + timeout
    while time.time() < end and _state["building"]:
        time.sleep(0.3)


@router.get("/api/recommend/home")
def home(refresh: bool = False):
    try:
        data = _current(refresh)
    except navidrome.NavidromeError:
        data = None
    if not data:
        return {"shelves": [], "lastfm": bool(lastfm_key())}
    shelves = []
    for sh in data["shelves"]:
        songs = [s for s in sh["songs"] if not _owned(s)]       # songs you've added since drop out
        if len(songs) >= MIN_SHELF:
            shelves.append(dict(sh, songs=songs))
    for sh in shelves[:2]:                                       # so the first taps start without the YouTube wait
        for s in sh["songs"][:WARM_SONGS]:
            _warm(s.get("video_id"))
    return {"shelves": shelves, "lastfm": data.get("lastfm", False)}
