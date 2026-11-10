"""Picks which YouTube upload to download for a song, by length.

The problem this solves: the video a user pasted or tapped is often the
official music video, whose audio has an intro/outro/skit -- so it's
30-90s longer than the album track and synced lyrics (timed to the album
track) land far too early. Once the song is known (catalog pick or
MusicBrainz/iTunes match), its official length is known too, so:

1. YouTube Music "songs" for artist + title -- these are YouTube's
   auto-generated album-audio uploads ("Artist - Topic"). Must be within
   TOLERANCE seconds of the official length.
2. The user's own video, if its length already matches.
3. YouTube Music "videos" (lyric videos, audio uploads) with a matching
   length.
4. The user's own video anyway, flagged as a length mismatch.

Returned as a "source" dict stored on the track and shown on the confirm
screen (see server._match_job_inner).
"""

import threading

from . import catalog
from .textmatch import BRACKETS_RE, base_title, is_alt_version, text_score
from .title_parser import parse as parse_video_title

# Seconds the chosen audio may differ from the official length. Catalogs
# disagree with each other by 1-2s on the same recording; a music video
# with an intro is off by 10s+.
TOLERANCE = 4

_cache: dict = {}
_cache_lock = threading.Lock()


def _search(query: str, filter_: str) -> list:
    key = (query.lower(), filter_)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    results = catalog.ytm().search(query, filter=filter_, limit=10) or []
    with _cache_lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = results
    return results


def score_result(r: dict, artist: str, title: str, target: int | None, explicit: bool | None = None) -> float | None:
    """How well a YouTube Music result fits the wanted song; None = reject.
    Rejects: wrong title, wrong artist, a remix/live/sped-up version the user
    didn't ask for, or (when the official length is known) a length more
    than TOLERANCE seconds off."""
    if not r.get("videoId"):
        return None
    name = r.get("title") or ""
    want = base_title(title).lower()
    t_score = text_score(want, base_title(name).lower())
    names = [a.get("name", "") for a in r.get("artists") or []]
    a_score = catalog.artist_match(artist, names) if artist else 0.7
    # Third-party lyric/audio uploads: YouTube Music lists the uploading
    # channel ("7clouds") as the artist and puts the real one in the title,
    # "Eminem - Lose Yourself (Lyrics)". Read both from the title instead,
    # scored a little below an upload credited to the artist itself.
    min_artist = 0.6
    if t_score < 0.7 or a_score < min_artist:
        v_artist, v_title, conf = parse_video_title(name)
        # Anything still in brackets after the usual noise ("(Lyrics)",
        # "(Official Audio)") is stripped is an edit of some kind
        # ("(Drumless Track)") -- a random channel's upload can be anything.
        extra = {b.strip().lower() for b in BRACKETS_RE.findall(v_title)} - \
                {b.strip().lower() for b in BRACKETS_RE.findall(title)}
        if conf == "high" and v_artist and not extra:
            vt = text_score(want, base_title(v_title).lower())
            va = (catalog.artist_match(artist, [v_artist]) if artist else 0.7) * 0.9
            if vt >= 0.7 and va >= 0.6 * 0.9:
                t_score, a_score, min_artist = vt, va, 0.6 * 0.9
    if t_score < 0.7 or a_score < min_artist:
        return None
    if is_alt_version(name, title):
        return None
    dur = r.get("duration_seconds")
    diff = abs(dur - target) if (target and dur) else None
    if target and (diff is None or diff > TOLERANCE):
        return None
    score = t_score + 0.5 * a_score - 0.05 * (diff or 0)
    if explicit is not None and r.get("isExplicit") is not None and r["isExplicit"] != explicit:
        score -= 0.3
    return score


def make_source(kind: str, video_id: str, url: str, title: str, artist: str, duration, target, ok) -> dict:
    return {
        "kind": kind,  # album_audio | matched_video | your_video
        "video_id": video_id,
        "url": url or f"https://www.youtube.com/watch?v={video_id}",
        "title": title,
        "artist": artist,
        "duration": duration,
        "target": target,
        "diff": (duration - target) if (duration and target) else None,
        "ok": ok,  # True = length matches, False = mismatch, None = official length unknown
    }


def _best(results: list, artist: str, title: str, target, explicit):
    scored = [(score_result(r, artist, title, target, explicit), r) for r in results]
    scored = [(s, r) for s, r in scored if s is not None]
    return max(scored, key=lambda x: x[0])[1] if scored else None


def find_audio_source(artist: str, title: str, target: int | None,
                      original: dict | None = None, explicit: bool | None = None,
                      log: list | None = None) -> dict | None:
    """The upload to download for this song. `target` is the official length
    in seconds (None if unknown); `original` is the user's own video
    {"id", "url", "duration"} (None for a catalog pick without one).
    Returns a source dict (see make_source), or None if nothing usable exists."""
    log = log if log is not None else []
    query = f"{catalog.main_artist(artist)} {base_title(title)}".strip()

    def try_search(filter_):
        try:
            return _search(query, filter_)
        except Exception as exc:  # YouTube Music down / layout change: fall back
            log.append(f"youtube music {filter_} search failed: {type(exc).__name__}: {exc}")
            return []

    # 1. Album audio from YouTube Music.
    best = _best(try_search("songs"), artist, title, target, explicit)
    if best:
        names = ", ".join(a.get("name", "") for a in best.get("artists") or [])
        return make_source("album_audio", best["videoId"], None, best.get("title", ""), names,
                       best.get("duration_seconds"), target, True if target else None)

    orig_dur = (original or {}).get("duration")
    orig_ok = bool(target and orig_dur and abs(orig_dur - target) <= TOLERANCE)
    original_source = (
        make_source("your_video", original.get("id"), original.get("url"), original.get("title", ""), "",
                orig_dur, target, (orig_ok if (target and orig_dur) else None))
        if original and original.get("url") else None
    )

    # 2. The user's own video is already the right length.
    if original_source and orig_ok:
        return original_source

    # 3. A lyric video / audio upload of the right length.
    if target:
        best = _best(try_search("videos"), artist, title, target, explicit)
        if best:
            names = ", ".join(a.get("name", "") for a in best.get("artists") or [])
            return make_source("matched_video", best["videoId"], None, best.get("title", ""), names,
                           best.get("duration_seconds"), target, True)

    # 4. Whatever the user picked, flagged when its length is off.
    if original_source:
        return original_source

    # 5. No video of their own (a catalog pick): the closest-length upload
    # of the right song, flagged, so there's still something to add.
    if target:
        loose = []
        for filter_, kind in (("songs", "album_audio"), ("videos", "matched_video")):
            for r in try_search(filter_):
                if r.get("duration_seconds") and score_result(r, artist, title, None, explicit) is not None:
                    loose.append((abs(r["duration_seconds"] - target), kind, r))
        if loose:
            _, kind, best = min(loose, key=lambda x: x[0])
            names = ", ".join(a.get("name", "") for a in best.get("artists") or [])
            return make_source(kind, best["videoId"], None, best.get("title", ""), names,
                               best["duration_seconds"], target, False)
    return None


def audio_candidates(artist: str, title: str, target: int | None, original: dict | None = None,
                     explicit: bool | None = None, limit: int = 8) -> list:
    """Every plausible upload of this song, for the song page's "Change"
    list: right title and artist, not a remix/live/edited version, any
    length. Length-matching ones first (album audio before other uploads),
    then by how far off they are. The user's own video is included."""
    query = f"{catalog.main_artist(artist)} {base_title(title)}".strip()
    out, seen = [], set()
    for filter_, kind in (("songs", "album_audio"), ("videos", "matched_video")):
        try:
            results = _search(query, filter_)
        except Exception:
            results = []
        for r in results:
            if r.get("videoId") in seen or score_result(r, artist, title, None, explicit) is None:
                continue
            seen.add(r["videoId"])
            dur = r.get("duration_seconds")
            ok = (abs(dur - target) <= TOLERANCE) if (dur and target) else None
            names = ", ".join(a.get("name", "") for a in r.get("artists") or [])
            out.append(make_source(kind, r["videoId"], None, r.get("title", ""), names, dur, target, ok))
    if original and original.get("url") and original.get("id") not in seen:
        dur = original.get("duration")
        ok = (abs(dur - target) <= TOLERANCE) if (dur and target) else None
        out.append(make_source("your_video", original.get("id"), original.get("url"), original.get("title", ""), "",
                               dur, target, ok))
    rank = {"album_audio": 0, "your_video": 1, "matched_video": 2}
    out.sort(key=lambda s: (s["ok"] is not True, abs(s["diff"]) if s["diff"] is not None else 999, rank[s["kind"]]))
    return out[:limit]
