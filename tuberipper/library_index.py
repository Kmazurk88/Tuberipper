"""A quick index of the library for search: who/what is in it, from the
folder and file names alone (Artist/Album/"3 - Title.m4a", as tagger files
them) -- no tag reading, so it builds in well under a second even on the NAS.

Used to show library matches in the main search and to mark online results
that are already in the library.
"""

import os
import re
import threading
import time
import unicodedata

from . import playlists, tagger
from .textmatch import base_title, is_alt_version

TTL = 120  # seconds; downloads invalidate it right away

_lock = threading.Lock()
_cache = {"root": None, "built": 0.0, "songs": [], "keys": set(), "albums": set()}

_TRACK_RE = re.compile(r"^(?:(\d+)-)?(\d+)\s+-\s+(.+)$")
_FEAT_RE = re.compile(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b|\bx\b)\s.*$", re.IGNORECASE)


def fold(text: str) -> str:
    """Lowercase, no accents, dashes/quotes unified, punctuation dropped."""
    text = tagger.normalize_dashes(text or "").translate(tagger.QUOTE_TRANSLATION)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^\w]+", " ", text).strip()


def song_key(artist: str, title: str) -> tuple:
    """Same song = same main artist + same title, ignoring "(feat. X)" and
    "(Remastered)" -- but "(Sped Up)", "(Live)" etc. are other recordings."""
    title = title or ""
    return fold(_FEAT_RE.sub("", artist or "")), fold(title if is_alt_version(title) else base_title(title))


def _parse(stem: str) -> tuple:
    m = _TRACK_RE.match(stem)
    if m:
        return (int(m.group(1)) if m.group(1) else 1), int(m.group(2)), m.group(3)
    return None, None, stem


def _build(root: str) -> dict:
    songs, keys, albums = [], set(), set()
    try:
        artists = [a for a in os.listdir(root)
                   if not a.startswith(".") and a != playlists.PLAYLISTS_DIR and os.path.isdir(os.path.join(root, a))]
    except OSError:
        artists = []
    for artist in artists:
        adir = os.path.join(root, artist)
        try:
            album_names = [b for b in os.listdir(adir) if os.path.isdir(os.path.join(adir, b))]
        except OSError:
            continue
        for album in album_names:
            bdir = os.path.join(adir, album)
            try:
                files = os.listdir(bdir)
            except OSError:
                continue
            count = 0
            for f in files:
                stem, ext = os.path.splitext(f)
                if ext.lower() not in tagger.AUDIO_EXTENSIONS:
                    continue
                disc, number, title = _parse(stem)
                rel = "/".join((artist, album, f))
                try:
                    mtime = os.path.getmtime(os.path.join(bdir, f))
                except OSError:
                    mtime = 0
                songs.append({"artist": artist, "album": album, "title": title, "track_number": number,
                              "disc_number": disc, "path": rel, "mtime": mtime,
                              "has_lyrics": os.path.exists(os.path.join(bdir, stem + ".lrc")),
                              "_fold": fold(f"{title} {artist} {album}"), "_title": fold(title), "_artist": fold(artist),
                              "_album": fold(album)})
                keys.add(song_key(artist, title))
                count += 1
            if count:
                albums.add((fold(artist), fold(tagger_canon(album))))
    return {"songs": songs, "keys": keys, "albums": albums}


def tagger_canon(album: str) -> str:
    from .metadata import canonicalize_album  # late: metadata is heavier to import
    return canonicalize_album(album or "")


def _get(root: str) -> dict:
    with _lock:
        if _cache["root"] == root and time.time() - _cache["built"] < TTL:
            return _cache
    data = _build(root)
    with _lock:
        _cache.update(data, root=root, built=time.time())
        return _cache


def invalidate() -> None:
    with _lock:
        _cache["built"] = 0.0


def has_song(root: str, artist: str, title: str) -> bool:
    return song_key(artist, title) in _get(root)["keys"]


def has_album(root: str, artist: str, album: str) -> bool:
    return (fold(_FEAT_RE.sub("", artist or "")), fold(tagger_canon(album))) in _get(root)["albums"]


def _public(s: dict) -> dict:
    return {k: v for k, v in s.items() if not k.startswith("_") and k != "mtime"}


def recent_albums(root: str, limit: int = 12) -> list:
    """Albums by when their newest song arrived, newest first."""
    albums: dict = {}
    for s in _get(root)["songs"]:
        a = albums.setdefault((s["artist"], s["album"]), {"artist": s["artist"], "album": s["album"], "count": 0, "mtime": 0})
        a["count"] += 1
        a["mtime"] = max(a["mtime"], s["mtime"])
    return sorted(albums.values(), key=lambda a: -a["mtime"])[:limit]


def all_albums(root: str) -> list:
    albums: dict = {}
    for s in _get(root)["songs"]:
        a = albums.setdefault((s["artist"], s["album"]), {"artist": s["artist"], "album": s["album"], "count": 0})
        a["count"] += 1
    return sorted(albums.values(), key=lambda a: (fold(a["album"]), fold(a["artist"])))


def all_songs(root: str) -> list:
    return [_public(s) for s in sorted(_get(root)["songs"], key=lambda s: (s["_title"], s["_artist"]))]


def song_for_path(root: str, rel: str) -> dict | None:
    return next((_public(s) for s in _get(root)["songs"] if s["path"] == rel), None)


def find_song(root: str, artist: str, title: str) -> dict | None:
    """The library song that is this artist + title (as search marks them), if any."""
    key = song_key(artist, title)
    return next((_public(s) for s in _get(root)["songs"] if song_key(s["artist"], s["title"]) == key), None)


def search(root: str, q: str, limit: int = 25) -> dict:
    """{songs: [...], albums: [{artist, album, count}], artists: [{artist, count}]}
    for every word of q appearing in the song's title/artist/album."""
    words = fold(q).split()
    if not words:
        return {"songs": [], "albums": [], "artists": []}
    data = _get(root)
    whole = " ".join(words)

    def rank(s):
        # Title hits before artist/album hits; whole-phrase before scattered words.
        return (whole not in s["_title"], not s["_title"].startswith(whole), whole not in s["_fold"],
                s["_artist"], s["_album"], s["track_number"] or 0)

    hits = [s for s in data["songs"] if all(w in s["_fold"] for w in words)]
    hits.sort(key=rank)
    albums: dict = {}
    artists: dict = {}
    for s in data["songs"]:
        if all(w in f"{s['_artist']} {s['_album']}" for w in words):
            albums.setdefault((s["artist"], s["album"]), 0)
            albums[(s["artist"], s["album"])] += 1
        if all(w in s["_artist"] for w in words):
            artists[s["artist"]] = artists.get(s["artist"], 0) + 1
    clean = [{k: v for k, v in s.items() if not k.startswith("_")} for s in hits[:limit]]
    return {
        "songs": clean,
        "albums": [{"artist": a, "album": b, "count": n} for (a, b), n in sorted(albums.items(), key=lambda x: x[0][1].casefold())][:limit],
        "artists": [{"artist": a, "count": n} for a, n in sorted(artists.items(), key=lambda x: x[0].casefold())][:limit],
    }
