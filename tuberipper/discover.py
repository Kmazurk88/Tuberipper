"""Artist / album / song information for the player's "Go to artist", "Go to
album" and "About this song" pages. Everything comes from real sources, never
a language model: YouTube Music (artist bio, popular songs, full discography,
related artists, official tracklists) and Wikipedia (a song's write-up, when
it has one). Answers are cached on disk, since they barely change."""

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests
from fastapi import APIRouter, HTTPException

from . import catalog, config, diskcache, library_index
from .textmatch import text_score

router = APIRouter()

WIKI_HEADERS = {"User-Agent": "tuberipper/1.0 (personal media server)"}
BIO_CREDIT = re.compile(r"\s*From Wikipedia \(.*$", re.S)


def pod_norm(s: str) -> str:
    """Episode titles: Navidrome shows "Title (episode description)" when a file has a subtitle tag, and the
    description can hold newlines and brackets of its own, so everything from the first " (" on is ignored."""
    return _norm(re.split(r"\s\(", s or "", maxsplit=1)[0])


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\(\[].*?[\)\]]", " ", s)
    s = re.sub(r"\s+-\s+(?:remaster(?:ed)?|\d{4}\b|deluxe|live|single version|.*\bversion\b|.*\bedit\b).*$", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s.replace("&", " and "))
    return re.sub(r"\s+", " ", s).strip()


def _thumb(thumbs, size=544) -> str:
    if not thumbs:
        return ""
    url = thumbs[-1].get("url", "")
    return re.sub(r"=w\d+-h\d+", f"=w{size}-h{size}", url)


def _names(artists) -> str:
    return ", ".join(a.get("name", "") for a in artists or [] if a.get("name"))


def _cached(key, fn):
    hit = diskcache.get(key)
    if hit is not None:
        return hit
    value = fn()
    diskcache.put(key, value)
    return value


def _flag_song(song: dict) -> dict:
    song["in_library"] = library_index.has_song(config.DEFAULT_OUTPUT_DIR, song.get("artist", ""), song.get("title", ""))
    return song


# ---------------------------------------------------------------------------
# Artist
# ---------------------------------------------------------------------------

def resolve_artist(name: str) -> str | None:
    """YouTube Music channel id for an artist name, or None if nothing
    matches closely enough (better no page than the wrong artist's)."""
    key = "disc:resolve:" + _norm(name)

    def find():
        # "Simon & Garfunkel" as credited, then just the first artist ("Eminem, Dido" -> "Eminem").
        for query in dict.fromkeys([name, catalog.main_artist(name)]):
            results = catalog.ytm().search(query, filter="artists", limit=5) or []
            best = max(results, key=lambda r: text_score(_norm(query), _norm(r.get("artist", ""))), default=None)
            if best and text_score(_norm(query), _norm(best.get("artist", ""))) >= 0.85:
                return best.get("browseId") or ""
        return ""

    return _cached(key, find) or None


def _song_payload(t: dict, artist_fallback: str = "", album: str = "") -> dict:
    alb = t.get("album")
    return {
        "title": t.get("title") or "",
        "artist": _names(t.get("artists")) or artist_fallback,
        "album": (alb.get("name") if isinstance(alb, dict) else alb) or album,
        "duration": t.get("duration_seconds"),
        "explicit": t.get("isExplicit"),
        "video_id": t.get("videoId"),
        "artwork": _thumb(t.get("thumbnails")),
        "year": t.get("year") or "",
    }


def _album_card(a: dict, kind: str, artist: str) -> dict:
    return {"title": a.get("title") or "", "year": a.get("year") or "", "type": a.get("type") or kind,
            "browse_id": a.get("browseId"), "artwork": _thumb(a.get("thumbnails")), "artist": artist}


def build_artist(browse_id: str) -> dict:
    y = catalog.ytm()
    a = y.get_artist(browse_id)
    name = a.get("name") or ""

    def popular():
        sec = a.get("songs") or {}
        try:
            if sec.get("browseId"):
                return (y.get_playlist(sec["browseId"], limit=10) or {}).get("tracks") or sec.get("results") or []
        except Exception:
            pass
        return sec.get("results") or []

    def section(key, kind):
        sec = a.get(key) or {}
        items = sec.get("results") or []
        if sec.get("browseId") and sec.get("params"):
            try:
                items = y.get_artist_albums(sec["browseId"], sec["params"]) or items
            except Exception:
                pass
        return [_album_card(x, kind, name) for x in items if x.get("browseId")]

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_pop, f_alb, f_sin = pool.submit(popular), pool.submit(section, "albums", "Album"), pool.submit(section, "singles", "Single")
        tracks, albums, singles = f_pop.result(), f_alb.result(), f_sin.result()

    desc = a.get("description") or ""
    bio_source = "Wikipedia" if "From Wikipedia" in desc else ("YouTube Music" if desc else "")
    return {
        "browse_id": browse_id,
        "name": name,
        "bio": BIO_CREDIT.sub("", desc).strip(),
        "bio_source": bio_source,
        "listeners": a.get("monthlyListeners") or "",
        "banner": (a.get("thumbnails") or [{}])[-1].get("url", ""),
        "thumb": _thumb(a.get("thumbnails"), 544),
        "popular": [_song_payload(t, name) for t in tracks if t.get("videoId")][:10],
        "albums": albums,
        "singles": singles,
        "related": [{"name": r.get("title") or "", "browse_id": r.get("browseId"), "thumb": _thumb(r.get("thumbnails"))}
                    for r in (a.get("related") or {}).get("results", []) if r.get("browseId")][:12],
    }


def _flag_artist(data: dict) -> dict:
    root = config.DEFAULT_OUTPUT_DIR
    out = dict(data)
    out["popular"] = [_flag_song(dict(s)) for s in data["popular"]]
    for key in ("albums", "singles"):
        out[key] = [dict(x, in_library=library_index.has_album(root, x.get("artist", ""), x.get("title", ""))) for x in data[key]]
    return out


@router.get("/api/discover/artist")
def artist_info(name: str = "", browse_id: str = ""):
    if not (name or browse_id):
        raise HTTPException(400, "name or browse_id is required.")
    try:
        bid = browse_id or resolve_artist(name)
        if not bid:
            raise HTTPException(404, "Couldn't find that artist online.")
        data = _cached("disc:artist:" + bid, lambda: build_artist(bid))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Couldn't load artist info: {type(exc).__name__}")
    return _flag_artist(data)


# ---------------------------------------------------------------------------
# Album
# ---------------------------------------------------------------------------

def resolve_album(artist: str, title: str) -> str | None:
    key = f"disc:resolve-album:{_norm(artist)}|{_norm(title)}"

    def find():
        results = catalog.ytm().search(f"{artist} {title}", filter="albums", limit=8) or []
        # The exact title first, so "Piano Man" doesn't land on "Piano Man (Legacy Edition)"
        # (extra bonus tracks would then look like missing songs); then ignoring edition suffixes.
        for same in (lambda t: t.casefold().strip(), _norm):
            for r in results:
                names = [x.get("name", "") for x in r.get("artists") or []]
                if same(r.get("title", "")) == same(title) and catalog.artist_match(artist, names) >= 0.85:
                    return r.get("browseId") or ""
        return ""

    return _cached(key, find) or None


def build_album(browse_id: str) -> dict:
    a = catalog.ytm().get_album(browse_id)
    artist = _names(a.get("artists"))
    art = _thumb(a.get("thumbnails"), 600)
    tracks = []
    for n, t in enumerate(a.get("tracks") or [], 1):
        if t.get("isAvailable") is False and not t.get("videoId"):
            continue
        s = _song_payload(t, artist, a.get("title") or "")
        s["artwork"] = s["artwork"] or art
        s["year"] = a.get("year") or ""
        s["track_number"] = t.get("trackNumber") or n
        tracks.append(s)
    return {
        "browse_id": browse_id, "title": a.get("title") or "", "artist": artist, "artist_id": (a.get("artists") or [{}])[0].get("id"),
        "year": a.get("year") or "", "type": a.get("type") or "Album", "artwork": art,
        "duration": a.get("duration_seconds") or 0, "tracks": tracks,
    }


@router.get("/api/discover/album")
def album_info(browse_id: str = "", artist: str = "", title: str = ""):
    try:
        bid = browse_id or (resolve_album(artist, title) if (artist and title) else None)
        if not bid:
            raise HTTPException(404, "Couldn't find that album online.")
        data = _cached("disc:album:" + bid, lambda: build_album(bid))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Couldn't load album info: {type(exc).__name__}")
    out = dict(data)
    out["tracks"] = [_flag_song(dict(t)) for t in data["tracks"]]
    return out


# ---------------------------------------------------------------------------
# Song write-up (Wikipedia, only when the song has its own article)
# ---------------------------------------------------------------------------

def _wiki_song(artist: str, title: str) -> dict:
    want, who = _norm(title), _norm(catalog.main_artist(artist))
    if not want:
        return {}
    r = requests.get("https://en.wikipedia.org/w/api.php", headers=WIKI_HEADERS, timeout=10, params={
        "action": "query", "list": "search", "srsearch": f'"{title}" {catalog.main_artist(artist)} song', "format": "json", "srlimit": 4})
    r.raise_for_status()
    for hit in r.json().get("query", {}).get("search", []):
        if _norm(re.sub(r"\((?:[^)]*song|[^)]*single)\)", "", hit["title"])) != want:
            continue
        s = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(hit["title"].replace(" ", "_"), safe=""),
                         headers=WIKI_HEADERS, timeout=10)
        if s.status_code != 200:
            continue
        d = s.json()
        desc, extract = (d.get("description") or "").lower(), d.get("extract") or ""
        if not re.search(r"\b(song|single|ballad)\b", desc):
            continue
        if who and who not in _norm(desc) and who not in _norm(extract[:600]):
            continue
        return {"text": extract, "description": d.get("description") or "", "source": "Wikipedia",
                "url": (d.get("content_urls") or {}).get("desktop", {}).get("page", "")}
    return {}


@router.get("/api/discover/song")
def song_info(artist: str, title: str):
    key = f"disc:song:{_norm(artist)}|{_norm(title)}"
    hit = diskcache.get(key)
    if hit is None:
        try:
            hit = _wiki_song(artist, title)
        except Exception:
            return {"blurb": None}  # don't cache a network failure
        diskcache.put(key, hit)
    return {"blurb": hit or None}
