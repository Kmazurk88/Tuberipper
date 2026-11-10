"""MusicBrainz lookups: the primary source for *which* song and album a track
is, its track/disc number, original year and album artist.

Why MusicBrainz over iTunes for this: iTunes is a store, so it only knows
what it currently sells in one country -- "Sexy Bitch" by David Guetta only
exists there as remixes, so the original album could never be picked.
MusicBrainz lists every release and labels it (Album / EP / Single, with
Compilation / Live / Remix as secondary types), which is exactly what's
needed to prefer the original studio album over remix singles and
"Now That's Music" compilations.

Costs: MusicBrainz allows ~1 request/second per client, so requests are
serialized and spaced out here. One recording search per track (cached)
gives everything the album picker needs; picking an album costs one more
request for its tracklist.
"""

import re
import threading
import time

import requests

from . import diskcache
from .textmatch import base_title, is_alt_version, text_score

API_ROOT = "https://musicbrainz.org/ws/2"
HEADERS = {"User-Agent": "tuberipper/1.0 ( self-hosted )"}
COVER_ART_RG_URL = "https://coverartarchive.org/release-group/{rgid}/front-500"
VARIOUS_ARTISTS_ID = "89ad4ac3-39f7-470e-963a-56509c546377"

ARTIST_THRESHOLD = 0.6
TITLE_THRESHOLD = 0.8
SKIP_STATUSES = {"Bootleg", "Pseudo-Release", "Withdrawn", "Cancelled"}

_lock = threading.Lock()
_last_call = 0.0
_search_cache: dict = {}
_release_cache: dict = {}


def _get(path: str, params: dict) -> dict | None:
    """Rate-limited GET returning parsed JSON, or None on failure. Retries
    MusicBrainz's 503 "slow down" responses with backoff. Successful
    answers are kept on disk (see diskcache), so repeats skip the queue."""
    params = {**params, "fmt": "json"}
    key = "mb:" + path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    hit = diskcache.get(key)
    if hit is not None:
        return hit
    data = _fetch(path, params)
    # An empty search ("not on MusicBrainz yet") isn't kept: a new release
    # can be added any day, and caching it would hide it for a month.
    empty = data is not None and path in ("recording", "release-group") and not (
        data.get("recordings") or data.get("release-groups"))
    if data is not None and not empty:
        diskcache.put(key, data)
    return data


def _fetch(path: str, params: dict) -> dict | None:
    global _last_call
    for delay in (0, 2, 5, 10):
        if delay:
            time.sleep(delay)
        with _lock:
            wait = 1.1 - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.monotonic()
            try:
                resp = requests.get(f"{API_ROOT}/{path}", params=params, headers=HEADERS, timeout=20)
            except requests.RequestException:
                continue
        if resp.status_code in (429, 503):
            continue
        if not resp.ok:
            return None
        try:
            return resp.json()
        except ValueError:
            continue
    return None


def _esc(text: str) -> str:
    """Escapes text for use inside a quoted Lucene phrase."""
    return (text or "").replace("\\", " ").replace('"', " ").strip()


def credit_name(credits: list) -> str:
    """"Artist feat. Other" using each artist's main name -- not how one
    release happened to credit them ("Lady GaGa", "Jason Derülo"), which made
    one artist show up as two in the player."""
    return "".join(
        ((c.get("artist") or {}).get("name") or c.get("name") or "") + (c.get("joinphrase") or "")
        for c in credits or []
    ).strip()


def _main_credit(credits: list) -> tuple:
    if not credits:
        return "", ""
    first = credits[0]
    artist = first.get("artist") or {}
    return artist.get("name") or first.get("name") or "", artist.get("id") or ""


def artist_matches(wanted: str, credits: list) -> bool:
    if not wanted:
        return True
    wanted_l = wanted.lower()
    for name in (credit_name(credits), _main_credit(credits)[0]):
        name_l = name.lower()
        if name_l and (wanted_l in name_l or name_l in wanted_l or text_score(wanted_l, name_l) >= ARTIST_THRESHOLD):
            return True
    return False


def search_recordings(artist: str, title: str) -> list:
    """Raw recordings matching artist + title (cached). Tries an exact
    phrase first, then a looser word search (censored or oddly punctuated
    titles like "Sexy B*tch" don't match as a phrase)."""
    wanted = base_title(title)
    key = (artist.strip().lower(), wanted.lower())
    if key in _search_cache:
        return _search_cache[key]

    # Search by the main artist only: "Jennifer Lopez, Pitbull" or "X feat. Y"
    # as a phrase misses MusicBrainz's own spelling of the credit.
    main = re.split(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b|\bx\b|\bvs\.?)\s*", artist or "", flags=re.IGNORECASE)[0].strip()
    artist_clause = f' AND artist:"{_esc(main)}"' if main else ""
    # 100 is the API maximum; hits have dozens of remix/edit recordings and
    # the album version can sit past the first 50.
    data = _get("recording", {"query": f'recording:"{_esc(wanted)}"{artist_clause}', "limit": 100})
    recordings = (data or {}).get("recordings", [])

    if not any(text_score(wanted, base_title(r.get("title", ""))) >= TITLE_THRESHOLD for r in recordings):
        words = [w for w in re.findall(r"[a-z0-9]+", wanted.lower()) if len(w) > 1]
        if words:
            loose = "recording:(" + " ".join(f"{w}~" for w in words) + ")" + artist_clause
            data2 = _get("recording", {"query": loose, "limit": 100})
            recordings = recordings + (data2 or {}).get("recordings", [])

    # The search ranks individual recordings, and a mega-hit has hundreds
    # (live versions, bootlegs, compilation edits), so the original album
    # recording often isn't in the top 100 at all ("Smells Like Teen
    # Spirit" never showed Nevermind). Ask again for recordings on
    # official albums/soundtracks only and merge them in.
    # It's the first query plus filters, so when the first one already
    # returned every hit (count <= limit) it can't add anything: skip it
    # (one less ~1 s rate-limited request per song).
    if data is not None and (data.get("count") or 0) <= 100:
        data3 = {"recordings": []}
    else:
        album_query = (
            f'recording:"{_esc(wanted)}"{artist_clause} AND primarytype:album AND status:official'
            " AND NOT secondarytype:compilation AND NOT secondarytype:live"
        )
        data3 = _get("recording", {"query": album_query, "limit": 100})
    by_id = {r.get("id"): r for r in recordings}
    for r in (data3 or {}).get("recordings", []):
        if r.get("id") in by_id:
            seen = {x.get("id") for x in by_id[r["id"]].get("releases") or []}
            by_id[r["id"]].setdefault("releases", []).extend(x for x in r.get("releases") or [] if x.get("id") not in seen)
        else:
            by_id[r.get("id")] = r
    recordings = list(by_id.values())

    if data is not None and data3 is not None:  # don't cache a (partial) failure
        _search_cache[key] = recordings
    return recordings


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# Release titles that give away a compilation/karaoke/remix collection even
# when MusicBrainz didn't tag it as one ("The Smash! Hits Chart Karaoke
# Album", "Pop Party 7", "Complete Clubland", "Just Dance (The Remixes)").
JUNK_TITLE_RE = re.compile(
    r"\b(karaoke|tribute|lullab(y|ies)|in the style of|covers|remixes|remixed|the remixes|"
    r"hits|greatest|best of|now that'?s music|clubland|pop party|party (hits|anthems|mix)|"
    r"workout|ringtones?|instrumentals|renditions|essentials|anthology|sessions?|"
    r"live (at|from|in|on))\b",
    re.IGNORECASE,
)


def type_rank(primary: str, secondary: list, release_artist_id: str, title: str = "") -> int:
    """Lower is better: studio album, EP, single, then live/remix/soundtrack
    albums, then compilations."""
    secondary = [s.lower() for s in secondary or []]
    if JUNK_TITLE_RE.search(title or ""):
        return 4
    if secondary == ["soundtrack"] and (primary or "").lower() == "album":
        # Songs that premiered on a soundtrack (8 Mile, Saturday Night
        # Fever) -- even though soundtracks are credited to Various Artists.
        return 0.5
    if "compilation" in secondary or release_artist_id == VARIOUS_ARTISTS_ID:
        return 4
    if secondary:  # live, remix, dj-mix, demo...
        return 3
    return {"album": 0, "ep": 1, "single": 2}.get((primary or "").lower(), 3)


def type_label(primary: str, secondary: list, release_artist_id: str) -> str:
    secondary = list(secondary or [])
    if release_artist_id == VARIOUS_ARTISTS_ID and "Compilation" not in secondary:
        secondary.append("Compilation")
    if secondary:
        return secondary[0]
    return primary or "Release"


def track_options(artist: str, title: str) -> list:
    """Every (recording, release) pairing that plausibly is this song, as
    flat dicts. A recording on 40 releases yields 40 options; the caller
    decides which release wins."""
    wanted = base_title(title)
    options = []
    for rec in search_recordings(artist, title):
        rec_title = rec.get("title", "")
        if text_score(wanted, base_title(rec_title)) < TITLE_THRESHOLD:
            continue
        credits = rec.get("artist-credit") or []
        if not artist_matches(artist, credits):
            continue
        alt = is_alt_version(f"{rec_title} {rec.get('disambiguation') or ''}", title)
        main_name, main_id = _main_credit(credits)
        tags = sorted(rec.get("tags") or [], key=lambda t: -(t.get("count") or 0))
        for rel in rec.get("releases") or []:
            if rel.get("status") in SKIP_STATUSES:
                continue
            rg = rel.get("release-group") or {}
            rel_credits = rel.get("artist-credit") or credits
            rel_artist_name, rel_artist_id = _main_credit(rel_credits)
            # A release by someone else entirely (e.g. the featured
            # artist's own album) isn't this artist's album.
            if rel_artist_id != VARIOUS_ARTISTS_ID and not (
                rel_artist_id == main_id or artist_matches(main_name, rel_credits)
            ):
                continue
            media = (rel.get("media") or [{}])[0]
            track = (media.get("track") or [{}])[0]
            options.append({
                "rec_id": rec.get("id"),
                "rec_title": rec_title,
                "rec_artist": credit_name(credits),
                "rec_artist_id": main_id,
                "alt": alt,
                "score": rec.get("score") or 0,
                "genre": tags[0]["name"].title() if tags else "",
                "release_id": rel.get("id"),
                "release_date": rel.get("date") or "",
                "release_artist": credit_name(rel_credits),
                "release_artist_id": rel_artist_id,
                "rg_id": rg.get("id"),
                "rg_title": rg.get("title") or rel.get("title") or "",
                "rg_primary": rg.get("primary-type") or "",
                "rg_secondary": rg.get("secondary-types") or [],
                "track_number": _int(track.get("number")),
                "disc_number": _int(media.get("position")),
                "track_count": rel.get("track-count"),
                "first_release_date": rec.get("first-release-date") or "",
                # Official length in ms -- what the downloaded audio should
                # match (a music video with an intro runs long).
                "length": rec.get("length"),
            })
    return options


def search_release_groups(artist: str, album: str) -> list:
    """Release groups by `artist` whose title matches `album` (the user
    typed it after rejecting the suggestions)."""
    query = f'releasegroup:"{_esc(album)}"' + (f' AND artist:"{_esc(artist)}"' if artist else "")
    data = _get("release-group", {"query": query, "limit": 25})
    groups = []
    for rg in (data or {}).get("release-groups", []):
        credits = rg.get("artist-credit") or []
        if artist and not artist_matches(artist, credits):
            continue
        releases = [r for r in rg.get("releases") or [] if r.get("status") not in SKIP_STATUSES]
        groups.append({
            "rg_id": rg.get("id"),
            "rg_title": rg.get("title") or "",
            "rg_primary": rg.get("primary-type") or "",
            "rg_secondary": rg.get("secondary-types") or [],
            "release_artist": credit_name(credits),
            "release_artist_id": _main_credit(credits)[1],
            "date": rg.get("first-release-date") or "",
            "release_id": releases[0]["id"] if releases else None,
            "track_count": None,
        })
    return groups


def pick_release(rg_id: str) -> str | None:
    """A representative release for a release group when none is known:
    the earliest official one."""
    data = _get("release", {"release-group": rg_id, "status": "official", "limit": 100})
    releases = [r for r in (data or {}).get("releases", []) if r.get("status") not in SKIP_STATUSES]
    if not releases:
        return None
    releases.sort(key=lambda r: r.get("date") or "9999")
    return releases[0]["id"]


def release_tracklist(release_id: str) -> dict | None:
    """{title, album_artist, album_artist_id, rg_id, date, tracks: [{recording_id,
    title, artist, artist_id, track_number, disc_number, length (ms)}]} for a release."""
    if release_id in _release_cache:
        return _release_cache[release_id]
    data = _get(f"release/{release_id}", {"inc": "recordings+artist-credits+release-groups"})
    if not data:
        return None
    rg = data.get("release-group") or {}
    credits = data.get("artist-credit") or []
    tracks = []
    for medium in data.get("media") or []:
        for t in medium.get("tracks") or []:
            rec = t.get("recording") or {}
            t_credits = t.get("artist-credit") or rec.get("artist-credit") or credits
            tracks.append({
                "recording_id": rec.get("id"),
                "title": t.get("title") or rec.get("title") or "",
                "artist": credit_name(t_credits),
                "artist_id": _main_credit(t_credits)[1],
                "track_number": _int(t.get("number")) or t.get("position"),
                "disc_number": medium.get("position"),
                "length": t.get("length") or rec.get("length"),
            })
    result = {
        "title": rg.get("title") or data.get("title") or "",
        "album_artist": credit_name(credits),
        "album_artist_id": _main_credit(credits)[1],
        "rg_id": rg.get("id"),
        "rg_primary": rg.get("primary-type") or "",
        "rg_secondary": rg.get("secondary-types") or [],
        "date": rg.get("first-release-date") or data.get("date") or "",
        "tracks": tracks,
    }
    _release_cache[release_id] = result
    return result


_rg_date_cache: dict = {}


def rg_first_date(rg_id: str) -> str:
    """A release group's real first-release date. Search results only show
    the releases that matched, which for a classic album can be just a 2011
    anniversary edition -- making Nevermind look like a 2011 album."""
    if rg_id not in _rg_date_cache:
        data = _get(f"release-group/{rg_id}", {})
        if data is None:
            return ""
        _rg_date_cache[rg_id] = data.get("first-release-date") or ""
    return _rg_date_cache[rg_id]


def known_rg_date(rg_id: str) -> str:
    return _rg_date_cache.get(rg_id, "")


def cover_url(rg_id: str) -> str:
    return COVER_ART_RG_URL.format(rgid=rg_id) if rg_id else ""
