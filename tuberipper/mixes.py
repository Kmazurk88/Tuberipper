"""Made for you: Daily Mixes and Discover Weekly.

Daily Mix: songs from your own library, grouped by kind of music (Rock, Hip-Hop & Rap, ...),
leaning toward the artists you play and the songs you've liked, reshuffled each day, with a
few new songs from your recommendations slipped in. No AI: genres come from your files' tags.

Discover Weekly: about 30 songs you don't have yet, picked from the Home recommendations
and kept for the whole week (a new batch the first time you open the app in a new week)."""

import datetime
import json
import os
import random
import re
import threading

from fastapi import APIRouter

from . import config, diskcache, library_index, navidrome, recommend
from .discover import _norm
from .radio import slim

router = APIRouter()

FAMILIES = [
    ("Hip-Hop & Rap", r"hip|rap|trap"),
    ("Metal & Hard Rock", r"metal|hard rock|punk|grunge"),
    ("Rock", r"rock|alternative|indie|blues"),
    ("Pop", r"pop|dance|disco"),
    ("R&B & Soul", r"r&b|rnb|soul|funk|motown"),
    ("Country & Folk", r"country|folk|americana|bluegrass"),
    ("Electronic", r"electro|house|techno|edm|trance"),
    ("Jazz", r"jazz|swing|bebop"),
    ("Classical", r"classical|orchestr|opera"),
    ("Holiday", r"christmas|holiday"),
    ("Soundtracks", r"soundtrack|score|film|musical"),
    ("Oldies", r"oldies|british invasion|doo|50s|60s"),
]
SKIP_GENRE = re.compile(r"podcast|comedy|spoken|audiobook|news|talk|sleep|noise|ambient|nature", re.I)
MIX_SIZE, MIX_COUNT, MIN_POOL, MAX_PER_ARTIST, NEW_PER_MIX = 25, 4, 12, 3, 5
WEEKLY_SIZE, WEEKLY_MIN = 30, 8

_lock = threading.Lock()
_day = {"key": None, "data": None}


def _cache_file() -> str:
    root = os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(config.DEFAULT_OUTPUT_DIR, ".tuberipper")
    return os.path.join(root, "discover-weekly.json")


def _family(genre: str):
    if SKIP_GENRE.search(genre or ""):
        return None
    g = genre.lower()
    return next((name for name, rx in FAMILIES if re.search(rx, g)), None)


def _music_folder():
    folders = recommend._list(navidrome._call("getMusicFolders").get("musicFolders", {}).get("musicFolder"))
    return next((f["id"] for f in folders if "podcast" not in f.get("name", "").lower()), None)


def _rec_songs() -> list:
    """The songs the Home shelves already found (never starts a build of its own)."""
    data = recommend._state.get("data") or diskcache.get("rec:home") or {}
    return [dict(s, shelf=sh["id"]) for sh in data.get("shelves", []) for s in sh["songs"]]


def build_mixes(day: datetime.date) -> list:
    folder = _music_folder()
    extra = [("musicFolderId", str(folder))] if folder is not None else []
    genres = recommend._list(navidrome._call("getGenres").get("genres", {}).get("genre"))
    by_family: dict = {}
    for g in genres:
        fam = _family(g.get("value", ""))
        if fam and g.get("songCount", 0) > 0:
            by_family.setdefault(fam, []).append(g["value"])
    seeds = {_norm(a) for a in recommend.top_artists()}
    try:
        starred = {s["id"] for s in recommend._list(navidrome._call("getStarred2").get("starred2", {}).get("song"))}
    except navidrome.NavidromeError:
        starred = set()
    rec = _rec_songs()

    mixes = []
    for fam, names in by_family.items():
        pool = {}
        for name in names:
            for s in recommend._list(navidrome._call("getSongsByGenre", [("genre", name), ("count", "500")] + extra).get("songsByGenre", {}).get("song")):
                pool[s["id"]] = s
        if len(pool) < MIN_POOL:
            continue
        rng = random.Random(f"{day.isoformat()}|{fam}")
        scored = []
        for s in pool.values():
            artist = _norm(s.get("artist", ""))
            scored.append((1 + 3 * (s["id"] in starred) + 2 * (artist in seeds) + rng.random() * 2.5, s))
        weight = sum(sc for sc, _ in scored)
        picked, per = [], {}
        for _sc, s in sorted(scored, key=lambda x: -x[0]):
            a = _norm(s.get("artist", ""))
            if per.get(a, 0) >= MAX_PER_ARTIST:
                continue
            per[a] = per.get(a, 0) + 1
            picked.append(s)
            if len(picked) >= MIX_SIZE:
                break
        rng.shuffle(picked)
        artists = {}
        for s in picked:
            artists[s.get("artist", "")] = artists.get(s.get("artist", ""), 0) + 1
        top = [a for a, _ in sorted(artists.items(), key=lambda kv: -kv[1])[:3]]
        songs = [dict(slim(s), kind="lib") for s in picked]
        mine = {_norm(a) for a in artists}
        fresh = [s for s in rec if _norm(s.get("because", "")) in mine and not _owned(s)][:NEW_PER_MIX]
        for i, s in enumerate(fresh):                    # a new song every few songs
            songs.insert(min(len(songs), 3 + i * 5), dict(_online(s), kind="online"))
        mixes.append({"weight": weight, "family": fam, "sub": ", ".join(top) + (" and more" if len(artists) > 3 else ""), "songs": songs,
                      "covers": [s["coverArt"] for s in picked if s.get("coverArt")][:4]})
    mixes.sort(key=lambda m: -m["weight"])
    out = []
    for n, m in enumerate(mixes[:MIX_COUNT], 1):
        m.pop("weight")
        out.append(dict(m, id=f"mix{n}", title=f"Daily Mix {n}"))
    return out


def _owned(s: dict) -> bool:
    return library_index.has_song(config.DEFAULT_OUTPUT_DIR, s.get("artist", ""), s.get("title", ""))


def _online(s: dict) -> dict:
    keep = ("title", "artist", "album", "duration", "video_id", "artwork", "year", "explicit")
    return {k: s.get(k) for k in keep}


def _week_key(day: datetime.date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def _load_weekly() -> dict | None:
    try:
        with open(_cache_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def weekly(day: datetime.date, force: bool = False) -> dict | None:
    stored = _load_weekly()
    key = _week_key(day)
    if stored and stored.get("week") == key and not force:
        return stored
    shelves: dict = {}
    for s in _rec_songs():
        shelves.setdefault(s["shelf"], []).append(s)
    if not shelves:
        return stored                                       # nothing to build from yet: keep last week's
    songs, seen = [], set()
    rng = random.Random(key)
    lists = list(shelves.values())
    for l in lists:
        rng.shuffle(l)
    for rank in range(WEEKLY_SIZE):                        # take turns between shelves for variety
        for l in lists:
            if rank < len(l):
                k = (_norm(l[rank].get("artist", "")), _norm(l[rank].get("title", "")))
                if k not in seen and not _owned(l[rank]):
                    seen.add(k)
                    songs.append(_online(l[rank]))
    if len(songs) < WEEKLY_MIN:
        return stored
    data = {"week": key, "title": "Discover Weekly", "songs": songs[:WEEKLY_SIZE]}
    try:
        os.makedirs(os.path.dirname(_cache_file()), exist_ok=True)
        tmp = _cache_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _cache_file())
    except OSError:
        pass
    return data


@router.get("/api/mixes")
def made_for_you(refresh: bool = False):
    today = datetime.date.today()
    with _lock:
        if refresh or _day["key"] != today or _day["data"] is None:
            try:
                _day.update(key=today, data=build_mixes(today))
            except navidrome.NavidromeError:
                return {"mixes": [], "weekly": None}
        mixes = _day["data"]
    wk = weekly(today, refresh)
    songs = [s for s in (wk or {}).get("songs", []) if not _owned(s)]      # songs you've added since drop out
    return {"mixes": mixes,
            "weekly": dict(wk, songs=songs) if wk and len(songs) >= 3 else None}
