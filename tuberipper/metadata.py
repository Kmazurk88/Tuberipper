"""Metadata lookup: MusicBrainz (musicbrainz.py) decides which song/album a
track is; iTunes is the fallback for anything MusicBrainz doesn't know and the
source of genre and backup cover art."""

import re
import threading
import time
from collections import Counter, defaultdict
from difflib import SequenceMatcher

import requests

from . import deezer, musicbrainz
from .textmatch import BRACKETS_RE, VERSION_RE, base_title, is_alt_version  # noqa: F401 (re-exported)

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"

# Album name markers that indicate a variant/reissue rather than the
# original release (acoustic collections, "Track by Track" commentary
# editions, karaoke versions, etc). Used to break ties in favor of the
# main album when multiple releases contain a matching track. This list
# can never be exhaustive (labels invent new edition names constantly) —
# resolve_batch()'s majority-album consensus is the real fix for splitting;
# this is just a secondary nudge.
VARIANT_MARKERS = [
    "track by track", "acoustic", "karaoke", "instrumental", "commentary",
    "sped up", "slowed", "bonus track", "deluxe", "expanded edition",
    "special edition", "anniversary edition", "explicit", "clean",
]

ARTIST_MATCH_THRESHOLD = 0.6

# Matches a trailing "(Deluxe Edition)", "[Remastered 2011]", etc, so an
# edition/reissue suffix can be stripped off to compare albums by their
# underlying identity (see canonicalize_album).
EDITION_SUFFIX_RE = re.compile(
    r"\s*[\(\[]\s*("
    r"deluxe|super\s*deluxe|expanded|remaster(ed)?|anniversary|"
    r"special\s*edition|bonus\s*track"
    r")[^\)\]]*[\)\]]\s*$",
    re.IGNORECASE,
)



def ms_to_seconds(ms) -> int | None:
    """Track length in whole seconds from a millisecond value, or None."""
    try:
        return round(int(ms) / 1000) or None
    except (TypeError, ValueError):
        return None


def _text_score(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _is_variant_album(album: str) -> bool:
    lowered = album.lower()
    return any(marker in lowered for marker in VARIANT_MARKERS)


# Matches a trailing "(feat. X)"/"(ft. X)"/"[feat. X]" credit on a track
# name. Used only to normalize the *ranking* comparison (see _rank) -- a
# user-supplied title like "Memories" scored directly against the iTunes
# track name "Memories (feat. Kid Cudi)" loses enough similarity to that
# suffix that a completely unrelated, shorter-named track can coincidentally
# outrank the real match (confirmed: "Remember" by a different artist pair
# outscored every real "Memories (feat. Kid Cudi)" result for that reason).
FEATURE_SUFFIX_RE = re.compile(
    r"\s*[\(\[]\s*(feat\.?|ft\.?)\s[^\)\]]*[\)\]]\s*$",
    re.IGNORECASE,
)


def _normalize_track_title(name: str) -> str:
    return FEATURE_SUFFIX_RE.sub("", name or "").strip()


def canonicalize_album(album: str) -> str:
    """Strips a trailing edition/reissue suffix so 'The Lumineers' and 'The
    Lumineers (Deluxe Edition)' are recognized as the same underlying album
    for grouping purposes, even though iTunes/MusicBrainz index them as
    separate releases."""
    if not album:
        return album
    stripped = EDITION_SUFFIX_RE.sub("", album).strip()
    return stripped or album


def _rank(result: dict, title: str, album_hint: str = ""):
    # Score against the feat.-stripped track name, not the raw one -- see
    # FEATURE_SUFFIX_RE's docstring for why scoring the raw name lets an
    # unrelated track win by coincidence when the real match has a "(feat.
    # X)" credit the user's own title doesn't include.
    track_name = result.get("trackName", "")
    title_score = _text_score(base_title(title), base_title(track_name))
    variant_penalty = 0.35 if _is_variant_album(result.get("collectionName", "")) else 0.0
    # The original recording beats remixes/live/karaoke of it, and an album
    # beats a one-song single release, unless the user asked for that.
    if is_alt_version(track_name, title):
        variant_penalty += 0.5
    if (result.get("collectionArtistName") or "").lower() == "various artists":
        variant_penalty += 0.15
    if SINGLE_SUFFIX_RE.search(result.get("collectionName", "")):
        variant_penalty += 0.1
    hint_bonus = 0.5 * _text_score(album_hint, result.get("collectionName", "")) if album_hint else 0.0
    return title_score - variant_penalty + hint_bonus


_song_search_cache: dict = {}
_itunes_lock = threading.Lock()
_last_itunes_call = 0.0


def _itunes_get(url: str, params: dict):
    """GET an iTunes API URL and return the parsed JSON, or None. iTunes
    answers bursts of requests with 403/429 (roughly 20 a minute), which used
    to look exactly like "no results"; space calls out a little and retry
    with backoff instead."""
    global _last_itunes_call
    for delay in (0, 2, 5):
        if delay:
            time.sleep(delay)
        with _itunes_lock:
            wait = 0.35 - (time.monotonic() - _last_itunes_call)
            if wait > 0:
                time.sleep(wait)
            _last_itunes_call = time.monotonic()
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code in (403, 429, 503):
                continue
            resp.raise_for_status()
            return resp.json()
        except ValueError:
            continue  # rate-limit pages sometimes come back as 200 + HTML
        except requests.RequestException:
            return None
    return None


def _itunes_song_search(artist: str, title: str) -> list:
    """Raw iTunes song search results. Successful responses are cached so
    album_candidates() can reuse what resolve_batch() already fetched instead
    of re-querying; failures aren't cached, so a network blip doesn't stick."""
    term = f"{artist} {title}".strip() if artist else title
    if not term:
        return []
    if term in _song_search_cache:
        return _song_search_cache[term]
    data = _itunes_get(ITUNES_SEARCH_URL, {"term": term, "entity": "song", "limit": 50})
    if data is None:
        return []
    results = data.get("results", [])
    if len(_song_search_cache) > 1000:
        _song_search_cache.clear()
    _song_search_cache[term] = results
    return results


def _lookup_itunes(artist: str, title: str, album_hint: str = ""):
    results = list(_itunes_song_search(artist, title))
    if not results:
        return None

    # Trust a user-supplied artist over title-similarity: narrow to results
    # from that artist first, so an exact-title soundalike/cover by someone
    # else can't outrank the real match.
    if artist:
        artist_matches = [r for r in results if _text_score(artist, r.get("artistName", "")) >= ARTIST_MATCH_THRESHOLD]
        if artist_matches:
            results = artist_matches

    best = max(results, key=lambda r: _rank(r, title, album_hint))
    # Narrowing to "matching" artists can leave only other songs by a
    # similar name ("Kenneth Acoustic" -> Kenny Chesney "Don't Blink").
    if title and _text_score(base_title(title), base_title(best.get("trackName", ""))) < TITLE_MATCH_THRESHOLD:
        return None

    artwork_url = best.get("artworkUrl100", "")
    if artwork_url:
        artwork_url = artwork_url.replace("100x100bb", "600x600bb")

    release_date = best.get("releaseDate", "")
    year = release_date[:4] if release_date else ""

    return {
        "artist": best.get("artistName", artist),
        "album_artist": best.get("collectionArtistName") or best.get("artistName", artist),
        "title": best.get("trackName", title),
        "album": best.get("collectionName", ""),
        "year": year,
        "genre": best.get("primaryGenreName", ""),
        "artwork_url": artwork_url,
        "track_number": best.get("trackNumber"),
        "duration": ms_to_seconds(best.get("trackTimeMillis")),
    }


def _lookup_fallback(artist: str, title: str, album_hint: str = ""):
    """iTunes-only single lookup (the MusicBrainz path lives in
    resolve_batch); used for tracks MusicBrainz has nothing for."""
    return _lookup_itunes(artist, title, album_hint)


def _blank_match(artist: str, title: str):
    return {
        "artist": artist,
        "title": title,
        "album": "",
        "year": "",
        "genre": "",
        "artwork_url": "",
        "track_number": None,
        "duration": None,
    }


def _itunes_resolve_batch(tracks: list, album_hint: str | None = None):
    """Looks up metadata for a batch of {'artist', 'title'} dicts (e.g. an
    entire playlist), then repairs same-artist tracks that disagree with the
    rest of the batch on album name. This is the fix for splintering: when
    most tracks in a batch resolve to one album but a few land on a
    differently-named edition/reissue of the same underlying album (a
    Deluxe/Expanded/Remastered reissue, a bonus-track-only cut, etc), those
    outliers are forced onto the majority's album identity (artist, album,
    year, genre, artwork) instead of being left to splinter into a second
    album folder. This is a straight overwrite, not a re-query: a bonus
    track that only exists on the Deluxe edition has no non-Deluxe search
    result to fall back to, so the old approach of re-querying with an
    album_hint and requiring a high similarity score could never fix those
    -- they'd just keep their own edition's album name forever.

    Grouping/repair only ever collapses album names that are the *same
    album under a recognized edition suffix* (see canonicalize_album) -- two
    genuinely different albums by the same artist in one batch are left
    alone.

    If the caller supplies `album_hint` (the user rejecting an automatic
    match and asking to try again with a specific album name), every track
    in the batch is looked up biased toward that album instead of running
    the automatic majority-consensus repair.

    Returns a list of match dicts in the same order as `tracks`."""
    if album_hint:
        return [
            _lookup_itunes(t["artist"], t["title"], album_hint=album_hint)
            or _lookup_fallback(t["artist"], t["title"])
            or _blank_match(t["artist"], t["title"])
            for t in tracks
        ]

    matches = [_lookup_fallback(t["artist"], t["title"]) or _blank_match(t["artist"], t["title"]) for t in tracks]

    groups = defaultdict(list)
    for i, track in enumerate(tracks):
        key = (track["artist"] or matches[i].get("artist") or "").strip().lower()
        if key:
            groups[key].append(i)

    for indices in groups.values():
        if len(indices) < 2:
            continue
        albums = [matches[i]["album"] for i in indices if matches[i].get("album")]
        if not albums:
            continue

        raw_counts = Counter(albums)
        if len(raw_counts) == 1:
            continue  # every track already agrees on the exact same album name

        # Group by canonical (edition-suffix-stripped) form to find which
        # cluster of raw names represents "the same underlying album" --
        # canonicalizing both sides is what lets a 9x "The Lumineers" /
        # 2x "The Lumineers (Deluxe Edition)" split be recognized as one
        # album in the first place, so this majority must be computed on
        # the canonical form even though the unanimity check above wasn't.
        canonical_counts = Counter(canonicalize_album(a) for a in albums)
        majority_canonical, canonical_majority_count = canonical_counts.most_common(1)[0]
        if canonical_majority_count < 2:
            continue  # no real majority to enforce

        # The display name to force onto outliers is the most common raw
        # album name among tracks that already agree on the canonical form
        # (so the batch keeps the plain "The Lumineers" name, not the
        # edition one, as long as more tracks used the plain name).
        raw_names_in_majority = [a for a in albums if canonicalize_album(a) == majority_canonical]
        majority_album = Counter(raw_names_in_majority).most_common(1)[0][0]
        majority_match = next(
            matches[i] for i in indices if matches[i].get("album") == majority_album
        )
        majority_track_numbers = {
            matches[i]["track_number"] for i in indices
            if matches[i].get("album") == majority_album and matches[i].get("track_number")
        }

        for i in indices:
            if matches[i].get("album") == majority_album:
                continue  # already exactly the chosen display name

            if canonicalize_album(matches[i].get("album", "")) == majority_canonical:
                # Same album, different edition -- force this track onto the
                # majority's identity outright. No data loss risk: this
                # edition has no other version of the song to lose.
                # A bonus-track-only edition (e.g. "Deluxe") usually numbers
                # its tracklist as the base album's tracks in the same
                # order, plus extras appended after -- so its own track
                # number is normally still correct and worth keeping. Only
                # dropped to unnumbered if it would actually collide with a
                # number the majority album already uses.
                own_track_number = matches[i].get("track_number")
                track_number = own_track_number if own_track_number not in majority_track_numbers else None
                matches[i] = {
                    **matches[i],
                    "artist": majority_match["artist"],
                    "album_artist": majority_match.get("album_artist", majority_match["artist"]),
                    "album": majority_match["album"],
                    "year": majority_match["year"],
                    "genre": majority_match["genre"],
                    "artwork_url": majority_match["artwork_url"],
                    "track_number": track_number,
                }
                continue

            # A genuinely different-looking album name -- not an edition
            # variant of the majority album, so canonicalizing can't merge
            # it (e.g. iTunes cataloging a hit song as its own "Ho Hey -
            # Single" release, completely separate in name from "The
            # Lumineers"). Retry the lookup biased toward the majority
            # album: if the song is *also* on that album, not just
            # released separately, the hint surfaces that version instead
            # of the single/compilation one. Only accepted if the result
            # actually lands on the majority album (not just "close" by
            # text similarity) -- a genuinely different album by the same
            # artist (two different real albums in one batch) won't have a
            # matching version to find here, so this can't misfire onto it.
            track = tracks[i]
            hinted = _lookup_itunes(track["artist"] or matches[i]["artist"], track["title"], album_hint=majority_album)
            if hinted and canonicalize_album(hinted.get("album", "")) == majority_canonical:
                matches[i] = hinted

    return matches


SINGLE_SUFFIX_RE = re.compile(r"\s+-\s+(single|ep)\s*$", re.IGNORECASE)
TITLE_MATCH_THRESHOLD = 0.6


def _album_search(term: str, limit: int = 15) -> list:
    data = _itunes_get(ITUNES_SEARCH_URL, {"term": term, "entity": "album", "limit": limit})
    return (data or {}).get("results", [])


_discography_cache: dict = {}


def _artist_albums(artist: str) -> list:
    """Every release iTunes has for an artist (searching the artist field
    only). Plain album searches rank by popularity, so a typed album name
    like "One Love" didn't even surface David Guetta's One Love albums."""
    key = artist.strip().lower()
    if key not in _discography_cache:
        data = _itunes_get(ITUNES_SEARCH_URL, {"term": artist, "entity": "album", "attribute": "artistTerm", "limit": 200})
        if data is None:
            return []
        _discography_cache[key] = [
            c for c in data.get("results", [])
            if _text_score(artist, c.get("artistName", "")) >= ARTIST_MATCH_THRESHOLD
            or artist.lower() in c.get("artistName", "").lower()
        ]
    return _discography_cache[key]


def _candidate_from_collection(c: dict) -> dict:
    artwork = (c.get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
    return {
        "collection_id": f"it:{c.get('collectionId')}",
        "album": c.get("collectionName", ""),
        "artist": c.get("artistName", ""),
        "year": (c.get("releaseDate") or "")[:4],
        "artwork_url": artwork,
        "track_count": c.get("trackCount"),
        "coverage": 0,
    }


def _itunes_album_candidates(tracks: list, matches: list, album_hint: str | None = None, limit: int = 10) -> list:
    """Albums the batch could plausibly belong to, for the user to pick from
    instead of trusting the automatic guess. Built from every iTunes release
    that contains one of the batch's songs (scored by how many of the
    batch's tracks it contains, preferring releases with the original
    version over remix/karaoke ones), plus -- when the user typed an album
    name -- the artist's releases whose name matches it.

    Only offered when the batch is a single artist -- for a mixed-artist
    playlist, "which album is this" has no single answer. Returns [] then,
    or when iTunes has nothing (MusicBrainz-only matches)."""
    artists = [(m.get("artist") or t["artist"] or "").strip() for t, m in zip(tracks, matches)]
    named = [a for a in artists if a]
    if not named:
        return []
    main_artist = Counter(a.lower() for a in named).most_common(1)[0][0]
    main_artist = re.sub(r"\s*(&|,|feat\.?|ft\.?|featuring|with)\s.*$", "", main_artist).strip() or main_artist
    same_artist = sum(1 for a in named if main_artist in a.lower() or _text_score(main_artist, a) >= ARTIST_MATCH_THRESHOLD)
    if same_artist < 0.8 * len(tracks):
        return []

    def by_artist(name: str) -> bool:
        name = (name or "").lower()
        return main_artist in name or _text_score(main_artist, name) >= ARTIST_MATCH_THRESHOLD

    by_id: dict = {}
    for i, t in enumerate(tracks):
        wanted = base_title(t["title"] or matches[i].get("title", ""))
        for r in _itunes_song_search(t["artist"], t["title"]):
            cid = r.get("collectionId")
            if not cid or not by_artist(r.get("artistName", "")):
                continue
            if _text_score(wanted, base_title(r.get("trackName", ""))) < TITLE_MATCH_THRESHOLD:
                continue
            entry = by_id.setdefault(cid, {**_candidate_from_collection(r), "_covered": set(), "_original": set()})
            entry["_covered"].add(i)
            if not is_alt_version(r.get("trackName", ""), t["title"]):
                entry["_original"].add(i)

    hint_ids = set()
    if album_hint:
        hint_base = canonicalize_album(album_hint).lower()
        for c in _artist_albums(main_artist) + _album_search(f"{main_artist} {album_hint}"):
            cid = c.get("collectionId")
            name = canonicalize_album(c.get("collectionName", "")).lower()
            if cid and by_artist(c.get("artistName", "")) and (
                hint_base in name or _text_score(hint_base, name) >= 0.75
            ):
                hint_ids.add(cid)
                by_id.setdefault(cid, {**_candidate_from_collection(c), "_covered": set(), "_original": set()})
        # Releases already found through the songs count too.
        for cid, entry in by_id.items():
            name = canonicalize_album(entry["album"]).lower()
            if hint_base in name or _text_score(hint_base, name) >= 0.75:
                hint_ids.add(cid)
    else:
        # A few of the artist's real albums too, in case none of the
        # releases above is right (cheap: one cached request per artist).
        for c in _artist_albums(main_artist):
            if (c.get("trackCount") or 0) >= 6 and c.get("collectionId") not in by_id:
                by_id[c["collectionId"]] = {**_candidate_from_collection(c), "_covered": set(), "_original": set()}


    candidates = []
    for cid, entry in by_id.items():
        entry["coverage"] = len(entry.pop("_covered"))
        entry["original"] = len(entry.pop("_original"))
        entry["hint_match"] = cid in hint_ids
        candidates.append(entry)

    candidates.sort(key=lambda c: (
        not c["hint_match"],                   # what the user typed comes first
        -c["original"],                        # has the original version of the songs
        -c["coverage"],                        # ...or at least some version of them
        bool(SINGLE_SUFFIX_RE.search(c["album"])),
        _is_variant_album(c["album"]),
        c["year"] or "9999",
    ))
    return candidates[:limit]


def _itunes_match_to_album(tracks: list, matches: list, collection_id: int):
    """Re-matches every track against one specific iTunes album (the one the
    user picked). Each track is paired with the album's song whose title is
    closest; tracks with no close-enough song on the album keep their
    previous match. Returns (new_matches, on_album) or None if the album
    can't be fetched."""
    data = _itunes_get(ITUNES_LOOKUP_URL, {"id": collection_id, "entity": "song"})
    if data is None:
        return None

    results = data.get("results", [])
    collection = next((r for r in results if r.get("wrapperType") == "collection"), None)
    songs = [r for r in results if r.get("wrapperType") == "track"]
    if not collection or not songs:
        return None

    info = _candidate_from_collection(collection)
    new_matches, on_album = [], []
    for t, m in zip(tracks, matches):
        titles = {base_title(x) for x in (t["title"], m.get("title", "")) if x}

        def title_score(song):
            name = base_title(song.get("trackName", ""))
            return max((_text_score(x, name) for x in titles), default=0.0)

        def score(song):
            # Same title: the original version beats a remix of it.
            return title_score(song) - (0.3 if is_alt_version(song.get("trackName", ""), t["title"]) else 0.0)

        best = max(songs, key=score)
        if title_score(best) < TITLE_MATCH_THRESHOLD:
            new_matches.append(m)
            on_album.append(False)
            continue
        new_matches.append({
            "artist": best.get("artistName") or info["artist"],
            "album_artist": info["artist"],
            "title": best.get("trackName", t["title"]),
            "album": info["album"],
            "year": info["year"],
            "genre": best.get("primaryGenreName") or collection.get("primaryGenreName", ""),
            "artwork_url": info["artwork_url"],
            "track_number": best.get("trackNumber"),
            "duration": ms_to_seconds(best.get("trackTimeMillis")) or m.get("duration"),
        })
        on_album.append(True)
    return new_matches, on_album


def fetch_artwork(artwork_url: str):
    if not artwork_url:
        return None
    try:
        resp = requests.get(artwork_url, timeout=10)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException:
        return None


def lookup_album_tracklist(artist: str, album: str):
    """Returns the official ordered track titles for `album`, via iTunes:
    first finds the album's collectionId (search, entity=album), then looks
    up every track in that collection (lookup, entity=song) and sorts by
    trackNumber. Returns None if the album can't be found. Used by
    library_merge to renumber tracks against a real tracklist instead of
    guessing from whatever numbers the source files happened to carry."""
    term = f"{artist} {album}".strip() if artist else album
    if not term:
        return None

    try:
        resp = requests.get(
            ITUNES_SEARCH_URL,
            params={"term": term, "entity": "album", "limit": 5},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    results = resp.json().get("results", [])
    if not results:
        return None

    best = max(results, key=lambda r: _text_score(album, r.get("collectionName", "")))
    collection_id = best.get("collectionId")
    if not collection_id:
        return None

    try:
        resp2 = requests.get(
            ITUNES_LOOKUP_URL,
            params={"id": collection_id, "entity": "song"},
            timeout=10,
        )
        resp2.raise_for_status()
    except requests.RequestException:
        return None

    tracks = [r for r in resp2.json().get("results", []) if r.get("wrapperType") == "track"]
    if not tracks:
        return None

    tracks.sort(key=lambda r: r.get("trackNumber") or 9999)
    return [t["trackName"] for t in tracks if t.get("trackName")]


# ---------------------------------------------------------------------------
# MusicBrainz-first matching (public API used by the server). iTunes stays
# as the fallback for anything MusicBrainz doesn't know, and as the source
# of genre and backup cover art.
# ---------------------------------------------------------------------------

def _hint_matches(hint: str, title: str) -> bool:
    if not hint:
        return False
    h = canonicalize_album(hint).lower()
    t = canonicalize_album(title).lower()
    return h in t or _text_score(h, t) >= 0.75


def _mb_groups(all_options: list, album_hint: str | None) -> dict:
    """Groups every (recording, release) option of the batch by release
    group (= "the album", across its editions/countries)."""
    groups: dict = {}
    for i, options in enumerate(all_options):
        for o in options:
            g = groups.setdefault(o["rg_id"], {
                "rg_id": o["rg_id"], "title": o["rg_title"], "primary": o["rg_primary"],
                "secondary": o["rg_secondary"], "artist": o["release_artist"],
                "artist_id": o["release_artist_id"], "date": "", "covered": set(),
                "original": set(), "releases": {}, "opts": {},
            })
            if o["release_date"] and (not g["date"] or o["release_date"] < g["date"]):
                g["date"] = o["release_date"]
            g["covered"].add(i)
            if not o["alt"]:
                g["original"].add(i)
            rel = g["releases"].setdefault(o["release_id"], {"covered": set(), "date": o["release_date"] or "9999", "track_count": o["track_count"]})
            rel["covered"].add(i)
            # Per (track, release): the original recording beats a remix of it.
            cur = g["opts"].get((i, o["release_id"]))
            if cur is None or (o["alt"], -o["score"]) < (cur["alt"], -cur["score"]):
                g["opts"][(i, o["release_id"])] = o
    for g in groups.values():
        true_date = musicbrainz.known_rg_date(g["rg_id"])
        if true_date:
            g["date"] = true_date
            g["true_date"] = True
        g["rank"] = musicbrainz.type_rank(g["primary"], g["secondary"], g["artist_id"], g["title"])
        g["kind"] = musicbrainz.type_label(g["primary"], g["secondary"], g["artist_id"])
        g["hint"] = _hint_matches(album_hint or "", g["title"])
        # The edition that holds the most of the batch (then the earliest).
        g["best_release"] = min(g["releases"], key=lambda r: (-len(g["releases"][r]["covered"]), g["releases"][r]["date"])) if g["releases"] else None
    return groups


def _mb_option(g: dict, i: int) -> dict:
    """Track i's option inside group g: the plain (non-remix) version from
    any edition first, preferring the group's main edition, then earliest.
    (A deluxe edition carrying only an "Extended Mix" must not make the
    whole album count as remix-only.)"""
    mine = [o for (idx, _), o in g["opts"].items() if idx == i]
    return min(mine, key=lambda o: (o["alt"], o["release_id"] != g["best_release"], o["release_date"] or "9999"))


# "Song - Radio Edit", "Song (Radio Edit)", "Album (Radio Edit)": the plain
# song under another label -- don't carry the label into the saved tags.
_PLAIN_LABEL_RE = re.compile(
    r"\s*(?:-\s*|[\(\[])(radio edit|radio version|single version|single edit|album version|original mix)[\)\]]?\s*$",
    re.IGNORECASE,
)


def _clean_label(name: str) -> str:
    return _PLAIN_LABEL_RE.sub("", name or "").strip() or (name or "")


def _mb_match(o: dict, g: dict) -> dict:
    return {
        "artist": o["rec_artist"],
        "album_artist": o["release_artist"],
        "title": _clean_label(o["rec_title"]),
        "album": _clean_label(g["title"]),
        "year": (g["date"] or o["first_release_date"])[:4],
        "genre": "",
        "genre_hint": o["genre"],
        "artwork_url": musicbrainz.cover_url(g["rg_id"]),
        "track_number": o["track_number"],
        "disc_number": o["disc_number"],
        "duration": ms_to_seconds(o.get("length")),
        "mb_recording_id": o["rec_id"],
        "mb_release_id": o["release_id"],
        "mb_release_group_id": g["rg_id"],
        "mb_artist_id": o["rec_artist_id"],
        "mb_album_artist_id": o["release_artist_id"],
        "source": "musicbrainz",
    }


def _song_year(all_options: list, i: int) -> str:
    """Year the song first came out, to tell an original album from a later
    collection or a re-recording. Only studio albums, EPs, singles and
    soundtracks count -- a live single of a different "Circles" (2014) or a
    compilation date shouldn't move it."""
    dates = []
    for o in all_options[i]:
        if o["alt"]:
            continue
        rank = musicbrainz.type_rank(o["rg_primary"], o["rg_secondary"], o["release_artist_id"], o["rg_title"])
        if rank > 2:
            continue
        dates += [d for d in (o["release_date"], o["first_release_date"]) if d]
    if not dates:
        dates = [o["release_date"] for o in all_options[i] if o["release_date"]]
    return min(dates)[:4] if dates else ""


def _effective_rank(g: dict, song_year: str, i: int | None = None) -> float:
    """type_rank, except an album that came out 4+ years after the song is
    a later collection/reissue or re-recording (Britney's "Glory" including
    "Womanizer", Earth, Wind & Fire's 2013 album re-recording "September"):
    rank it after the original single."""
    year = (g["date"] or "")[:4]
    if g["rank"] in (0, 0.5) and song_year and year and int(year) - int(song_year) >= 4:
        return 2.5
    if i is not None and i in g.get("predates", ()):
        return 2.5
    return g["rank"]


def _per_track_key(g: dict, i: int, song_year: str = ""):
    # For one song on its own: what the user typed, then the original
    # recording, then studio album > EP > single > later collections >
    # live/remix > compilation, then the earliest release, then the album
    # most of the batch shares.
    # ...then the album with the oldest recording of the song (the original,
    # not a later re-recording), then the plain album over its deluxe/
    # anniversary editions, then the earliest, then the most shared.
    own_year = (_mb_option(g, i).get("first_release_date") or "9999")[:4]
    if g.get("true_date") and g["date"][:4] < own_year:
        # The album is older than the recording found on it (a remaster
        # matched, e.g. Love Tracks 1978 via its 2019 remaster): the album
        # date is the better measure of "original".
        own_year = g["date"][:4]
    edition = bool(EDITION_WORDS_RE.search(g["title"]))
    return (not g["hint"], _mb_option(g, i)["alt"], _effective_rank(g, song_year, i), own_year, edition,
            g["date"] or "9999", -len(g["covered"]))


EDITION_WORDS_RE = re.compile(
    r"\b(deluxe|anniversary|remaster(ed)?|expanded|edition|reissue|legacy|bonus|super)\b", re.IGNORECASE
)


# Each check is one MusicBrainz request (~1 s, rate limited). 8 per song made
# a 25-song playlist sit on "Picking albums" for minutes; the oldest
# candidates are checked first, so 3 catches the real original.
MAX_DATE_CHECKS = 3


def _verify_dates(covering: list, i: int, song_year: str) -> None:
    """Fetches the real first-release date of the best few album-type
    candidates whose dates look like a later edition (cached; a few extra
    requests per song at most), so originals aren't mistaken for reissues."""
    checked = 0
    # Two album/EP candidates in the song's own year ("The Fame" via its
    # 2009 reissue with "The Fame Monster" as a bonus disc, and "The Fame
    # Monster" itself): the album's true date tells the reissue apart.
    contested = sum(1 for g in covering if g["rank"] <= 1 and not _mb_option(g, i)["alt"]) > 1
    # Check albums holding the oldest recordings first: the original album
    # can look like a reissue (only its 2019 edition matched), so it would
    # never make a "current top 4".
    def check_order(g):
        own = (_mb_option(g, i).get("first_release_date") or "9999")[:4]
        return (g["rank"] > 1, _mb_option(g, i)["alt"], bool(EDITION_WORDS_RE.search(g["title"])), own, g["date"] or "9999")
    for g in sorted(covering, key=check_order):
        if checked >= MAX_DATE_CHECKS or g["rank"] > 1:
            continue
        if g.get("true_date"):
            continue
        seen = (g["date"] or "")[:4]
        if seen and song_year and (seen < song_year or (seen == song_year and not contested)):
            continue  # already as old as the song itself
        checked += 1
        true_date = musicbrainz.rg_first_date(g["rg_id"])
        if true_date:
            g["date"] = true_date
            g["true_date"] = True


def _original_edition(match: dict, g: dict) -> dict:
    """If the edition used is later than the album itself (e.g. a 2011
    deluxe box of a 1991 album), take track/disc numbers from the original
    edition instead -- a bonus disc puts the song at "track 20"."""
    used_year = (next((o["release_date"] for o in g["opts"].values() if o["release_id"] == match["mb_release_id"]), "") or "")[:4]
    album_year = (g["date"] or "")[:4]
    if not (used_year and album_year and used_year > album_year):
        return match
    release_id = musicbrainz.pick_release(g["rg_id"])
    album = musicbrainz.release_tracklist(release_id) if release_id else None
    tracks = (album or {}).get("tracks", [])
    hit = next((x for x in tracks if x["recording_id"] == match["mb_recording_id"]), None)
    if hit is None:
        # Remastered editions often use a different recording id; fall back
        # to the same title on the original tracklist (not a remix of it).
        want = base_title(match["title"]).lower()
        hit = next((x for x in tracks if base_title(x["title"]).lower() == want and not is_alt_version(x["title"], match["title"])), None)
    if hit:
        match = {**match, "track_number": hit["track_number"], "disc_number": hit["disc_number"],
                 "mb_release_id": release_id, "mb_recording_id": hit["recording_id"] or match["mb_recording_id"]}
    return match


def _mark_predating(covering: list, all_options: list, i: int) -> None:
    """Albums whose verified first release is older than the song's own
    recording only carry it on a later reissue (The Fame, 2008, holds Bad
    Romance, 2009, via its Fame Monster edition). Marked so they rank as
    reissues -- only when an album/EP from the song's own time exists to
    take its place."""
    rec_dates = [o["first_release_date"][:4] for o in all_options[i] if not o["alt"] and o["first_release_date"]]
    if not rec_dates:
        return
    rec_year = min(rec_dates)
    older = [g for g in covering if g.get("true_date") and g["rank"] <= 1 and (g["date"] or "")[:4] < rec_year]
    current = [g for g in covering if g["rank"] <= 1 and (g["date"] or "9999")[:4] >= rec_year and not _mb_option(g, i)["alt"]]
    if older and current:
        for g in older:
            g.setdefault("predates", set()).add(i)


def _itunes_own_album(track: dict):
    """The iTunes match for a song, if it lands on the artist's own release
    (not a various-artists compilation or a remix/karaoke collection)."""
    m = _lookup_itunes(track["artist"], track["title"])
    if not m or not m.get("album"):
        return None
    if (m.get("album_artist") or "").lower() == "various artists" or _is_variant_album(m["album"]):
        return None
    if musicbrainz.JUNK_TITLE_RE.search(m["album"]):
        return None
    if _text_score(base_title(track["title"]), base_title(m["title"])) < TITLE_MATCH_THRESHOLD:
        return None
    if is_alt_version(m["title"], track["title"]) or is_alt_version(m["album"], track["title"]):
        return None
    if _text_score(track["artist"], m["artist"]) < ARTIST_MATCH_THRESHOLD:
        return None
    return {**m, "title": _clean_label(m["title"]), "source": "itunes"}


def resolve_batch(tracks: list, album_hint: str | None = None, on_progress=None, on_step=None) -> list:
    """Looks up metadata for a batch of {'artist', 'title'} dicts. Each
    track goes to its original studio album when MusicBrainz knows one
    (instead of a remix single or a compilation); tracks MusicBrainz has
    nothing for fall back to iTunes. Returns match dicts in order.
    on_progress(i, done) is called before (done=False) and after (done=True)
    looking up track i, so a long playlist can show where it is; on_step(text)
    describes the album-picking pass that follows."""
    step = on_step or (lambda text: None)
    all_options = []
    for i, t in enumerate(tracks):
        if on_progress:
            on_progress(i, False)
        options = musicbrainz.track_options(t["artist"], t["title"])
        if not options and t["artist"] and t["title"]:
            # "Replay - Iyaz": title and artist the wrong way round.
            options = musicbrainz.track_options(t["title"], t["artist"])
        all_options.append(options)
        if on_progress:
            on_progress(i, True)
    groups = _mb_groups(all_options, album_hint)

    matches = []
    for i in range(len(tracks)):
        covering = [g for g in groups.values() if i in g["covered"]]
        if not covering:
            matches.append(None)
            continue
        step(f"Picking albums {i + 1} of {len(tracks)}")
        song_year = _song_year(all_options, i)
        _verify_dates(covering, i, song_year)
        verified = [g["date"][:4] for g in covering if g.get("true_date") and g["date"] and g["rank"] <= 2]
        if verified:
            song_year = min([song_year] + verified) if song_year else min(verified)
        _mark_predating(covering, all_options, i)
        g = min(covering, key=lambda g: _per_track_key(g, i, song_year))
        if (_effective_rank(g, song_year, i) >= 4 or _mb_option(g, i)["alt"]) and not g["hint"]:
            # MusicBrainz only has this song on compilations ("Bild Sommer
            # 2003") or as a live/remix version (BTS "Spring Day" is filed
            # under its Korean title "봄날"; only a live album matches the
            # English one); iTunes often knows the artist's own album.
            own = _itunes_own_album(tracks[i])
            if own:
                matches.append(own)
                continue
        match = _original_edition(_mb_match(_mb_option(g, i), g), g)
        if not match["track_number"] and match["mb_release_id"]:
            # Search results sometimes omit the track position; the
            # release's own tracklist has it (one cached request per album).
            album = musicbrainz.release_tracklist(match["mb_release_id"])
            hit = next((x for x in (album or {}).get("tracks", []) if x["recording_id"] == match["mb_recording_id"]), None)
            if hit:
                match["track_number"], match["disc_number"] = hit["track_number"], hit["disc_number"]
        matches.append(match)

    missing = [i for i, m in enumerate(matches) if m is None]
    if missing:
        step(f"Asking iTunes about {len(missing)} song{'' if len(missing) == 1 else 's'} MusicBrainz doesn't know")
        fallback = _itunes_resolve_batch([tracks[i] for i in missing], album_hint=album_hint)
        for i, m in zip(missing, fallback):
            matches[i] = m
    return matches


def lookup(artist: str, title: str, album_hint: str = ""):
    """Single-track version of resolve_batch. Returns None when nothing
    matched at all."""
    m = resolve_batch([{"artist": artist, "title": title}], album_hint=album_hint or None)[0]
    return m if (m.get("album") or m.get("source")) else None


def album_candidates(tracks: list, matches: list, album_hint: str | None = None, limit: int = 10) -> list:
    """Albums to offer in the "Which album?" picker, best first. From
    MusicBrainz release groups the batch's songs appear on (studio albums
    first, compilations last), plus -- when the user typed an album name --
    that artist's releases with that name. Falls back to iTunes when
    MusicBrainz knows none of the songs. Only for single-artist batches."""
    artists = [(m.get("album_artist") or m.get("artist") or t["artist"] or "").strip() for t, m in zip(tracks, matches)]
    named = [a for a in artists if a]
    if not named:
        return []
    main_artist = Counter(a.lower() for a in named).most_common(1)[0][0]
    main_artist = re.sub(r"\s*(&|,|feat\.?|ft\.?|featuring|with|x)\s.*$", "", main_artist).strip() or main_artist
    if sum(1 for a in named if main_artist in a.lower() or _text_score(main_artist, a) >= ARTIST_MATCH_THRESHOLD) < 0.8 * len(tracks):
        return []

    all_options = [musicbrainz.track_options(t["artist"], t["title"]) for t in tracks]
    groups = _mb_groups(all_options, album_hint)

    if album_hint:
        for rg in musicbrainz.search_release_groups(main_artist, album_hint):
            if rg["rg_id"] in groups or not _hint_matches(album_hint, rg["rg_title"]):
                continue
            groups[rg["rg_id"]] = {
                "rg_id": rg["rg_id"], "title": rg["rg_title"], "primary": rg["rg_primary"],
                "secondary": rg["rg_secondary"], "artist": rg["release_artist"],
                "artist_id": rg["release_artist_id"], "date": rg["date"], "covered": set(),
                "original": set(), "releases": {}, "opts": {}, "best_release": rg["release_id"],
                "rank": musicbrainz.type_rank(rg["rg_primary"], rg["rg_secondary"], rg["release_artist_id"], rg["rg_title"]),
                "kind": musicbrainz.type_label(rg["rg_primary"], rg["rg_secondary"], rg["release_artist_id"]),
                "hint": True,
            }

    if not groups:
        return _itunes_album_candidates(tracks, matches, album_hint=album_hint, limit=limit)

    years = [_song_year(all_options, i) for i in range(len(tracks))]
    song_year = min((y for y in years if y), default="")
    ordered = sorted(groups.values(), key=lambda g: (
        not g["hint"], -len(g["original"]), _effective_rank(g, song_year, min(g["covered"]) if g["covered"] else None),
        -len(g["covered"]), g["date"] or "9999",
    ))
    candidates = []
    for g in ordered[:limit]:
        rel = g["releases"].get(g["best_release"]) if g["best_release"] else None
        candidates.append({
            "collection_id": f"mb:{g['rg_id']}:{g['best_release'] or ''}",
            "album": g["title"],
            "artist": g["artist"],
            "year": (g["date"] or "")[:4],
            "artwork_url": musicbrainz.cover_url(g["rg_id"]),
            "track_count": rel["track_count"] if rel else None,
            "coverage": len(g["covered"]),
            "original": len(g["original"]),
            "hint_match": g["hint"],
            "kind": g["kind"],
        })
    return candidates


def match_to_album(tracks: list, matches: list, collection_id):
    """Re-matches every track against the album the user picked. Returns
    (new_matches, on_album) or None if the album can't be loaded."""
    cid = str(collection_id)
    if not cid.startswith("mb:"):
        return _itunes_match_to_album(tracks, matches, int(cid.removeprefix("it:")))

    _, rg_id, release_id = (cid.split(":", 2) + [""])[:3]
    release_id = release_id or musicbrainz.pick_release(rg_id)
    album = musicbrainz.release_tracklist(release_id) if release_id else None
    if not album or not album["tracks"]:
        return None

    new_matches, on_album = [], []
    for t, m in zip(tracks, matches):
        m = m or {}
        best = next((x for x in album["tracks"] if m.get("mb_recording_id") and x["recording_id"] == m["mb_recording_id"]), None)
        if best is None:
            titles = {base_title(x) for x in (t["title"], m.get("title", "")) if x}

            def title_score(x):
                return max((_text_score(w, base_title(x["title"])) for w in titles), default=0.0)

            best = max(album["tracks"], key=lambda x: title_score(x) - (0.3 if is_alt_version(x["title"], t["title"]) else 0.0))
            if title_score(best) < TITLE_MATCH_THRESHOLD:
                new_matches.append(m)
                on_album.append(False)
                continue
        new_matches.append({
            "artist": best["artist"] or m.get("artist", ""),
            "album_artist": album["album_artist"],
            "title": best["title"],
            "album": album["title"],
            "year": (album["date"] or "")[:4],
            "genre": m.get("genre", ""),
            "genre_hint": m.get("genre_hint", ""),
            "artwork_url": musicbrainz.cover_url(album["rg_id"] or rg_id),
            "track_number": best["track_number"],
            "disc_number": best["disc_number"],
            "duration": ms_to_seconds(best.get("length")) or m.get("duration"),
            "mb_recording_id": best["recording_id"],
            "mb_release_id": release_id,
            "mb_release_group_id": album["rg_id"] or rg_id,
            "mb_artist_id": best["artist_id"],
            "mb_album_artist_id": album["album_artist_id"],
            "source": "musicbrainz",
        })
        on_album.append(True)
    return new_matches, on_album


def with_deezer(mb: dict | None, quick: dict | None, artist: str = "") -> dict | None:
    """The final match for a song: MusicBrainz decides which album (the
    original, not a compilation or reissue -- see resolve_batch), shown with
    Deezer's cover and genre for that album when Deezer has it. (That
    includes resolve_batch's iTunes answers: the artist's own album when
    MusicBrainz only has compilations/live versions.) Songs nobody else
    knows keep Deezer's own answer."""
    if not mb or not mb.get("album"):
        return quick or mb
    if quick and deezer.same_album(quick.get("album", ""), mb.get("album", "")):
        info = {"genre": quick.get("genre"), "cover": quick.get("artwork_url"), "id": quick.get("deezer_album_id")}
    else:
        try:
            found = deezer.find_album(mb.get("album_artist") or mb.get("artist") or artist, mb.get("album", ""))
        except Exception:
            found = None
        info = {"genre": found["genre"], "cover": found["cover"], "id": found["id"]} if found else {}
    out = dict(mb)
    if mb.get("genre") and mb.get("source") != "musicbrainz":
        info.pop("genre", None)  # iTunes's own genre for its album is fine
    if info.get("genre"):
        out["genre"] = info["genre"]
    if info.get("cover"):
        out["artwork_fallback"] = mb.get("artwork_url", "")
        out["artwork_url"] = info["cover"]
    if info.get("id"):
        out["deezer_album_id"] = info["id"]
    return out


def fill_genre(match: dict) -> dict:
    """MusicBrainz genres are community tags ("electro house"); iTunes's
    store genres ("Dance") read better in a player, so prefer those."""
    if match.get("genre"):
        return match
    artist = re.sub(r"\s*(feat\.?|ft\.?|featuring)\s.*$", "", match.get("artist", ""), flags=re.IGNORECASE)
    title = base_title(match.get("title", ""))
    genre = ""
    # iTunes search sometimes misses a song outright (ROSALÍA "DESPECHÁ"
    # returns only covers) while artist + album finds the album's tracks.
    album = canonicalize_album(match.get("album", ""))
    for term in [title] + ([album] if album and album.lower() != title.lower() else []):
        for r in _itunes_song_search(artist, term):
            if _text_score(artist, r.get("artistName", "")) >= ARTIST_MATCH_THRESHOLD and r.get("primaryGenreName"):
                genre = r["primaryGenreName"]
                break
        if genre:
            break
    return {**match, "genre": genre or match.get("genre_hint", "")}


def artwork_bytes(match: dict):
    """Cover art for a match: Cover Art Archive first, then the same album's
    iTunes artwork when the archive has none."""
    data = fetch_artwork(match.get("artwork_url", ""))
    if not data and match.get("artwork_fallback"):
        data = fetch_artwork(match["artwork_fallback"])
    if data or match.get("source") != "musicbrainz":
        return data
    artist = re.sub(r"\s*(feat\.?|ft\.?|featuring)\s.*$", "", match.get("album_artist") or match.get("artist", ""), flags=re.IGNORECASE)
    for r in _itunes_song_search(artist, base_title(match.get("title", ""))):
        if canonicalize_album(r.get("collectionName", "")).lower() == canonicalize_album(match.get("album", "")).lower() and r.get("artworkUrl100"):
            return fetch_artwork(r["artworkUrl100"].replace("100x100bb", "600x600bb"))
    return None
