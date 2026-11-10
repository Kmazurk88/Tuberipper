"""Song/album search against the music catalogs (YouTube Music + iTunes),
used as the main search instead of raw YouTube videos.

Why: a YouTube video search puts official music videos first, and their
audio has intros/outros/skits, so it never lines up with synced lyrics or
the album. Picking from the catalog means picking *the song* -- with its
real length, album and artwork -- and the audio is then sourced to match
(see sourcing.py).

- YouTube Music "songs" results are YouTube's auto-generated album-audio
  uploads ("Artist - Topic"), so a YouTube Music pick already *is* the
  right audio (its videoId).
- iTunes adds clean album/year/artwork and catches songs YouTube Music's
  search ranks poorly. An iTunes-only pick gets its audio found by length.
"""

import re
import threading
from concurrent.futures import ThreadPoolExecutor

from . import metadata
from .textmatch import base_title, text_score

_local = threading.local()


def ytm():
    """A YouTube Music client (no account needed for public search). One per
    thread: the client holds a requests session."""
    client = getattr(_local, "ytm", None)
    if client is None:
        from ytmusicapi import YTMusic
        client = YTMusic(language="en", location="US")
        _local.ytm = client
    return client


def main_artist(artist: str) -> str:
    """"David Guetta feat. Akon" / "Jennifer Lopez, Pitbull" -> the first
    credited artist, for searching and comparing."""
    return re.split(
        r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b|\bx\b|\bvs\.?)\s*",
        artist or "", maxsplit=1, flags=re.IGNORECASE,
    )[0].strip() or (artist or "").strip()


def artist_match(wanted: str, names: list) -> float:
    """Best similarity (0..1) between the wanted artist and any of `names`;
    1.0 when one contains the other ("Eminem" in "Eminem, Dido")."""
    w = main_artist(wanted).lower()
    if not w:
        return 1.0
    best = 0.0
    for n in names:
        n_l = (n or "").lower()
        if not n_l:
            continue
        if w in n_l or main_artist(n_l) in w:
            return 1.0
        best = max(best, text_score(w, main_artist(n_l)))
    return best


def _big_ytm_thumb(thumbs: list) -> str:
    if not thumbs:
        return ""
    url = thumbs[-1].get("url", "")
    # lh3.googleusercontent.com/...=w120-h120-l90-rj -> ask for a larger one.
    return re.sub(r"=w\d+-h\d+", "=w544-h544", url)


def _big_itunes_art(url: str) -> str:
    return (url or "").replace("100x100bb", "600x600bb")


# ---------------------------------------------------------------------------
# Raw searches
# ---------------------------------------------------------------------------

def _ytm_songs(q: str, limit: int) -> list:
    out = []
    for r in ytm().search(q, filter="songs", limit=limit) or []:
        if r.get("resultType") != "song" or not r.get("videoId"):
            continue
        album = r.get("album") or {}
        out.append({
            "title": r.get("title") or "",
            "artist": ", ".join(a.get("name", "") for a in r.get("artists") or [] if a.get("name")),
            "album": album.get("name") or "",
            "year": r.get("year") or "",
            "duration": r.get("duration_seconds"),
            "explicit": r.get("isExplicit"),
            "artwork": _big_ytm_thumb(r.get("thumbnails")),
            "video_id": r["videoId"],
            "ytm_album_id": album.get("id"),
        })
    return out


def _itunes_songs(q: str, limit: int) -> list:
    data = metadata._itunes_get(metadata.ITUNES_SEARCH_URL, {"term": q, "entity": "song", "limit": limit})
    out = []
    for r in (data or {}).get("results", []):
        if r.get("wrapperType") != "track" or r.get("kind") not in (None, "song"):
            continue
        out.append({
            "title": r.get("trackName") or "",
            "artist": r.get("artistName") or "",
            "album": metadata.SINGLE_SUFFIX_RE.sub("", r.get("collectionName") or ""),
            "year": (r.get("releaseDate") or "")[:4],
            "duration": metadata.ms_to_seconds(r.get("trackTimeMillis")),
            "explicit": r.get("trackExplicitness") == "explicit" if r.get("trackExplicitness") else None,
            "artwork": _big_itunes_art(r.get("artworkUrl100")),
            "itunes_id": r.get("trackId"),
            "genre": r.get("primaryGenreName") or "",
        })
    return out


def _ytm_albums(q: str, limit: int) -> list:
    out = []
    for r in ytm().search(q, filter="albums", limit=limit) or []:
        if r.get("resultType") != "album" or not r.get("browseId"):
            continue
        artist = r.get("artist") or ", ".join(a.get("name", "") for a in r.get("artists") or [] if a.get("name"))
        out.append({
            "title": r.get("title") or "",
            "artist": artist,
            "year": r.get("year") or "",
            "type": r.get("type") or "Album",
            "explicit": r.get("isExplicit"),
            "artwork": _big_ytm_thumb(r.get("thumbnails")),
            "track_count": None,
            "ytm_browse_id": r["browseId"],
        })
    return out


def _itunes_albums(q: str, limit: int) -> list:
    data = metadata._itunes_get(metadata.ITUNES_SEARCH_URL, {"term": q, "entity": "album", "limit": limit})
    out = []
    for r in (data or {}).get("results", []):
        if r.get("wrapperType") != "collection":
            continue
        name = r.get("collectionName") or ""
        single = metadata.SINGLE_SUFFIX_RE.search(name)
        out.append({
            "title": metadata.SINGLE_SUFFIX_RE.sub("", name),
            "artist": r.get("artistName") or "",
            "year": (r.get("releaseDate") or "")[:4],
            "type": single.group(1).title() if single else "Album",
            "explicit": r.get("collectionExplicitness") == "explicit" if r.get("collectionExplicitness") else None,
            "artwork": _big_itunes_art(r.get("artworkUrl100")),
            "track_count": r.get("trackCount"),
            "itunes_id": r.get("collectionId"),
        })
    return out


# ---------------------------------------------------------------------------
# Merging: one row per song/album, YouTube Music order first
# ---------------------------------------------------------------------------

def same_song(a: dict, b: dict, tolerance: int = 3) -> bool:
    if artist_match(a["artist"], [b["artist"]]) < 0.8:
        return False
    if text_score(base_title(a["title"]).lower(), base_title(b["title"]).lower()) < 0.85:
        return False
    if a.get("duration") and b.get("duration") and abs(a["duration"] - b["duration"]) > tolerance:
        return False
    if a.get("explicit") is not None and b.get("explicit") is not None and a["explicit"] != b["explicit"]:
        return False
    return True


def merge_songs(ytm_songs: list, itunes_songs: list, limit: int = 25) -> list:
    """YouTube Music rows (which carry the audio) enriched with the matching
    iTunes row's album/year/artwork/genre, then iTunes-only rows."""
    used = set()
    merged = []
    for y in ytm_songs:
        row = {**y, "itunes_id": None, "genre": ""}
        for i, it in enumerate(itunes_songs):
            if i in used or not same_song(y, it):
                continue
            used.add(i)
            row.update({
                "album": it["album"] or y["album"],
                "year": it["year"] or y["year"],
                "artwork": it["artwork"] or y["artwork"],
                "itunes_id": it["itunes_id"],
                "genre": it["genre"],
                "explicit": y["explicit"] if y["explicit"] is not None else it["explicit"],
            })
            break
        merged.append(row)
    for i, it in enumerate(itunes_songs):
        if i not in used:
            merged.append({**it, "video_id": None, "ytm_album_id": None})

    # The same song+album twice (both catalogs list the single and the album
    # cut, sometimes twice) is noise; different albums stay -- that's a choice.
    # A different length is a different version (radio edit, extended) and stays.
    seen: dict = {}
    out = []
    for r in merged:
        key = (main_artist(r["artist"]).lower(), base_title(r["title"]).lower(),
               metadata.canonicalize_album(r["album"]).lower(), bool(r.get("explicit")))
        lengths = seen.setdefault(key, [])
        if any(d is None or r.get("duration") is None or abs(d - r["duration"]) <= 3 for d in lengths):
            continue
        lengths.append(r.get("duration"))
        r["key"] = f"{r.get('video_id') or ''}|{r.get('itunes_id') or ''}"
        out.append(r)
    return out[:limit]


def merge_albums(ytm_albums: list, itunes_albums: list, limit: int = 15) -> list:
    used = set()
    merged = []

    def same(a, b):
        return (artist_match(a["artist"], [b["artist"]]) >= 0.8
                and metadata.canonicalize_album(a["title"]).lower() == metadata.canonicalize_album(b["title"]).lower()
                and (not a["year"] or not b["year"] or a["year"] == b["year"])
                and (a.get("explicit") is None or b.get("explicit") is None or a["explicit"] == b["explicit"]))

    for y in ytm_albums:
        row = {**y, "itunes_id": None}
        for i, it in enumerate(itunes_albums):
            if i not in used and same(y, it):
                used.add(i)
                row.update({"itunes_id": it["itunes_id"], "track_count": it["track_count"],
                            "artwork": it["artwork"] or y["artwork"], "year": y["year"] or it["year"]})
                break
        merged.append(row)
    for i, it in enumerate(itunes_albums):
        if i not in used:
            merged.append({**it, "ytm_browse_id": None})
    for r in merged:
        r["key"] = f"{r.get('ytm_browse_id') or ''}|{r.get('itunes_id') or ''}"
    return merged[:limit]


def search(q: str, trace: list | None = None, songs_only: bool = False) -> dict:
    """{"songs": [...], "albums": [...]} from YouTube Music and iTunes,
    queried in parallel. One source failing just leaves its rows out."""
    log = trace if trace is not None else []

    def safe(name, fn, *args):
        try:
            result = fn(*args)
            log.append(f"{name}: {len(result)} result(s)")
            return result
        except Exception as exc:  # network, YouTube Music layout change...
            log.append(f"{name}: ERROR {type(exc).__name__}: {str(exc)[:200]}")
            return []

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_ys = pool.submit(safe, "youtube music songs", _ytm_songs, q, 20)
        f_is = pool.submit(safe, "itunes songs", _itunes_songs, q, 25)
        f_ya = None if songs_only else pool.submit(safe, "youtube music albums", _ytm_albums, q, 10)
        f_ia = None if songs_only else pool.submit(safe, "itunes albums", _itunes_albums, q, 12)
        songs = merge_songs(f_ys.result(), f_is.result())
        albums = [] if songs_only else merge_albums(f_ya.result(), f_ia.result())
    return {"songs": songs, "albums": albums}


# ---------------------------------------------------------------------------
# Album tracklists
# ---------------------------------------------------------------------------

def album_tracks(ytm_browse_id: str | None = None, itunes_id: int | None = None) -> dict | None:
    """{"title", "artist", "year", "artwork", "tracks": [{title, artist,
    duration, video_id, explicit, track_number}]} for a picked album, or
    None if neither catalog could load it. YouTube Music's tracklist is
    preferred: its tracks come with their album-audio videoIds."""
    if ytm_browse_id:
        try:
            a = ytm().get_album(ytm_browse_id)
        except Exception:
            a = None
        if a and a.get("tracks"):
            album_artist = ", ".join(x.get("name", "") for x in a.get("artists") or [] if x.get("name"))
            tracks = []
            for n, t in enumerate(a["tracks"], 1):
                if t.get("isAvailable") is False and not t.get("videoId"):
                    continue
                artist = ", ".join(x.get("name", "") for x in t.get("artists") or [] if x.get("name")) or album_artist
                tracks.append({
                    "title": t.get("title") or "",
                    "artist": artist,
                    "duration": t.get("duration_seconds"),
                    "video_id": t.get("videoId"),
                    "explicit": t.get("isExplicit"),
                    "track_number": t.get("trackNumber") or n,
                })
            return {
                "title": a.get("title") or "",
                "artist": album_artist,
                "year": a.get("year") or "",
                "artwork": _big_ytm_thumb(a.get("thumbnails")),
                "tracks": tracks,
            }

    if itunes_id:
        data = metadata._itunes_get(metadata.ITUNES_LOOKUP_URL, {"id": itunes_id, "entity": "song"})
        results = (data or {}).get("results", [])
        coll = next((r for r in results if r.get("wrapperType") == "collection"), None)
        songs = sorted((r for r in results if r.get("wrapperType") == "track"),
                       key=lambda r: (r.get("discNumber") or 1, r.get("trackNumber") or 999))
        if coll and songs:
            return {
                "title": metadata.SINGLE_SUFFIX_RE.sub("", coll.get("collectionName") or ""),
                "artist": coll.get("artistName") or "",
                "year": (coll.get("releaseDate") or "")[:4],
                "artwork": _big_itunes_art(coll.get("artworkUrl100")),
                "tracks": [{
                    "title": r.get("trackName") or "",
                    "artist": r.get("artistName") or coll.get("artistName") or "",
                    "duration": metadata.ms_to_seconds(r.get("trackTimeMillis")),
                    "video_id": None,
                    "explicit": r.get("trackExplicitness") == "explicit" if r.get("trackExplicitness") else None,
                    "track_number": r.get("trackNumber"),
                } for r in songs],
            }
    return None
