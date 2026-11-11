"""Web API + mobile UI for tuberipper. Submit a URL, review/edit parsed
tracks, then kick off download+tag+lyrics processing in the background."""

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import catalog, deezer, downloader, library_index, library_merge, library_scan, metadata, musicbrainz, navidrome, notify, playlists, sourcing, tagger, lyrics, title_parser, config
from . import abs as audiobookshelf, activity, auth, compress, discover, audio_analysis, library_delete, libhealth, meta, mixes, player_api, podcasts, radio, recommend, secrets_setup, stats
from .jobs import store, build_track
from . import textmatch
from .textmatch import base_title, text_score

app = FastAPI(title="tuberipper")

# No login: this is a personal tool reachable only on the LAN and the
# tailnet (VPN), never exposed publicly. Permissive CORS because the phone UI
# probes both the local and Tailscale addresses from whichever one it's
# currently loaded on.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
    expose_headers=["X-TR-Shell"],
)
# Added after CORS so it wraps it: text answers are gzipped, audio/covers/Range pass through (compress.py).
app.add_middleware(compress.Gzip)
app.add_middleware(activity.CountAudio)
app.add_middleware(meta.ShellHeader)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# All routes live on this router so they can be mounted both unprefixed
# (direct LAN/Tailscale-IP access, unchanged) and under /tuberipper (for
# sharing this app behind a reverse proxy with a path prefix via a
# path-based mapping).
router = APIRouter()


@router.get("/api/health")
def health():
    # Reports whether the library mount is actually usable -- /api/library
    # swallows OSError and returns [], so an unreadable/missing mount
    # otherwise just looks like an empty library.
    root = config.DEFAULT_OUTPUT_DIR
    library = {"root": root, "exists": os.path.isdir(root), "entries": None, "error": None}
    try:
        library["entries"] = len(os.listdir(root))
    except OSError as e:
        library["error"] = f"{type(e).__name__}: {e}"
    return {"ok": True, "library": library, "uid": os.getuid() if hasattr(os, "getuid") else None}


@router.get("/api/search")
def search(q: str, songs_only: bool = False):
    """The main search: songs and albums from the music catalogs (YouTube
    Music + iTunes), each with its real length -- see catalog.py. songs_only
    skips the album lookups (a playlist import never uses them)."""
    q = q.strip()
    if not q:
        raise HTTPException(400, "Search query is required.")
    result = catalog.search(q, songs_only=True) if songs_only else catalog.search(q)
    for s in result.get("songs", []):
        s["in_library"] = library_index.has_song(LIBRARY_ROOT, s.get("artist", ""), s.get("title", ""))
    for a in result.get("albums", []):
        a["in_library"] = library_index.has_album(LIBRARY_ROOT, a.get("artist", ""), a.get("title", ""))
    return result


_suggest_cache: dict = {}


@router.get("/api/suggest")
def suggest(q: str):
    """Search suggestions while typing ("teenage dirtbag (clean)"), from
    YouTube Music. Empty on any failure -- they're only a convenience."""
    q = q.strip()
    if len(q) < 2:
        return []
    key = q.lower()
    if key not in _suggest_cache:
        try:
            items = catalog.ytm().get_search_suggestions(q)
        except Exception:
            items = []
        if len(_suggest_cache) > 500:
            _suggest_cache.clear()
        _suggest_cache[key] = [i for i in items if isinstance(i, str)][:6]
    return _suggest_cache[key]


@router.get("/api/search/youtube")
def search_youtube(q: str):
    """Plain YouTube videos and playlists -- the YouTube tab, for things the
    catalogs don't have (covers, live sets, remixes)."""
    q = q.strip()
    if not q:
        raise HTTPException(400, "Search query is required.")
    try:
        return downloader.search(q)
    except Exception as exc:
        raise HTTPException(502, f"Search failed: {exc}")


@router.get("/api/debug/search")
def debug_search(q: str = "lumineers ho hey"):
    """Step-by-step view of what the catalogs and YouTube return to this
    server for a search, for diagnosing "No results" on a particular network."""
    import yt_dlp
    catalog_trace, youtube_trace = [], []
    try:
        result = catalog.search(q, trace=catalog_trace)
        catalog_summary = {k: [f"{r['artist']} - {r['title']}" for r in v[:3]] for k, v in result.items()}
    except Exception as exc:
        catalog_summary = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        result = downloader.search(q, trace=youtube_trace)
        summary = {k: [r["title"] for r in v[:3]] for k, v in result.items()}
    except Exception as exc:
        summary = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "query": q, "yt_dlp_version": yt_dlp.version.__version__,
        "catalog_steps": catalog_trace, "catalog_first_results": catalog_summary,
        "youtube_steps": youtube_trace, "youtube_first_results": summary,
    }


class CreateJobRequest(BaseModel):
    url: str


class TrackUpdate(BaseModel):
    artist: str | None = None
    title: str | None = None
    accepted: bool | None = None
    use_original: bool | None = None  # download the user's own video instead of the found album audio


class CatalogSong(BaseModel):
    title: str
    artist: str
    album: str = ""
    duration: float | None = None
    explicit: bool | None = None
    video_id: str | None = None  # YouTube Music album-audio upload, when the row came from there
    artwork: str | None = None  # shown on the song page until the lookup's own cover arrives
    year: str | None = None


class CatalogAlbum(BaseModel):
    title: str = ""
    artist: str = ""
    ytm_browse_id: str | None = None
    itunes_id: int | None = None


class QuickAdd(BaseModel):
    """Add straight away (search "⋯" menu): no song page, no confirming."""
    playlist_mode: str = "none"  # none | new | existing
    playlist_name: str | None = None
    playlist_id: str | None = None
    notify: bool = True  # False: no per-job Discord message (bulk imports send their own summary)


class CatalogJobRequest(BaseModel):
    song: CatalogSong | None = None
    album: CatalogAlbum | None = None
    add: QuickAdd | None = None


class DefaultArtistRequest(BaseModel):
    artist: str


class PickAlbumRequest(BaseModel):
    collection_id: str | int  # "mb:<release-group>:<release>" or "it:<iTunes id>"


class StartJobRequest(BaseModel):
    # Asked up front for playlists, so the confirm step doesn't have to.
    playlist_mode: str | None = None  # "none" | "new" | "existing"
    playlist_name: str | None = None
    playlist_id: str | None = None


class SourceChoice(BaseModel):
    video_id: str


class RelookupRequest(BaseModel):
    artist: str
    title: str


class ConfirmMatchRequest(BaseModel):
    action: str  # "accept" | "retry" | "cancel"
    album_hint: str | None = None
    # accept only: "none" | "new" (create playlist_name) | "existing" (add to playlist_id)
    playlist_mode: str = "none"
    playlist_name: str | None = None
    notify: bool = True  # False: no Discord message for this job (bulk imports send a summary)
    playlist_id: str | None = None


def friendly_error(exc: Exception) -> str:
    """Turns yt-dlp/network exceptions into one plain sentence for the UI."""
    text = str(exc)
    low = text.lower()
    if "confirm you" in low and "bot" in low:
        return "YouTube is blocking downloads from this server for now (bot check). Try again in a while."
    if "private video" in low:
        return "This video is private."
    if "age" in low and ("restrict" in low or "confirm your age" in low):
        return "This video is age-restricted, so YouTube won't let it be downloaded without signing in."
    if "video unavailable" in low or "not available" in low or "has been removed" in low:
        return "This video isn't available anymore."
    if "unsupported url" in low or "is not a valid url" in low:
        return "That doesn't look like a YouTube link."
    if "permission denied" in low or "read-only file system" in low:
        return "Couldn't write to the music library (permission denied). Check the permissions of the folder mounted as the library."
    if "no space left" in low:
        return "The music library's disk is full."
    if "timed out" in low or "connection" in low or "network" in low:
        return "Network problem talking to YouTube. Try again."
    first_line = text.strip().splitlines()[0] if text.strip() else type(exc).__name__
    return first_line.replace("ERROR: ", "")[:200]


def _claim(job, expected: str, new_status: str):
    """Atomically moves a job from `expected` to `new_status`, so a double
    tap (two requests racing) can't start the same phase twice."""
    with job.lock:
        if job.status != expected:
            raise HTTPException(409, f"This download already moved on (status: {job.status}). Refresh to see where it is.")
        job.status = new_status


@router.post("/api/jobs")
def create_job(req: CreateJobRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "URL is required.")

    job = store.create(url, config.DEFAULT_OUTPUT_DIR)

    try:
        listing = downloader.get_playlist(url)
        videos = listing["videos"]
    except Exception as exc:
        job.status = "error"
        job.error = friendly_error(exc)
        return job.to_dict()

    if not videos:
        job.status = "error"
        job.error = "No videos found at that URL."
        return job.to_dict()

    with job.lock:
        parsed = title_parser.parse_batch([v["title"] for v in videos])
        for i, (video, (artist, title, confidence)) in enumerate(zip(videos, parsed)):
            job.tracks.append(build_track(i, video["id"], video["url"], video["title"], artist, title, confidence,
                                          duration=video.get("duration")))
        job.is_playlist = listing["is_playlist"] and len(videos) > 1
        job.playlist_title = listing["title"] if job.is_playlist else ""
        job.status = "ready"

    return job.to_dict()


ALBUM_WARM_TRACKS = 4


def _watch_url(video_id: str | None) -> str:
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""


# Declared before "/api/jobs/{job_id}" so "catalog" isn't taken as a job id.
@router.post("/api/jobs/catalog")
def create_catalog_job(req: CatalogJobRequest):
    """A job from a song or album picked in the catalog search. Artist and
    title are already clean (no title parsing). A single song starts
    looking up right away; an album stops at the review screen so songs can
    be unticked."""
    if req.song:
        s = req.song
        job = store.create(_watch_url(s.video_id) or f"catalog:{s.artist} - {s.title}", config.DEFAULT_OUTPUT_DIR)
        with job.lock:
            job.tracks.append(build_track(
                0, s.video_id or "", _watch_url(s.video_id), f"{s.artist} — {s.title}", s.artist.strip(), s.title.strip(),
                "catalog", duration=s.duration if s.video_id else None, target_duration=s.duration, explicit=s.explicit,
            ))
            # The album on the row the user tapped. Not used to steer the
            # lookup (catalogs often list a hit under a compilation/deluxe
            # edition); only to skip the album picker when the lookup lands
            # on that same album anyway.
            job.tracks[0]["catalog"] = {"album": s.album, "artwork": s.artwork or "", "year": s.year or ""}
            job.expected_album = s.album
            job.status = "processing"
        if req.add:
            _set_playlist(job, req.add.playlist_mode, req.add.playlist_name, req.add.playlist_id)
            job.auto_accept = True
            job.notify = req.add.notify
        threading.Thread(target=_match_job, args=(job.id,), daemon=True).start()
        warm_preview(s.video_id)  # usually the audio the lookup settles on
        return job.to_dict()

    if req.album:
        a = req.album
        album = catalog.album_tracks(a.ytm_browse_id, a.itunes_id)
        if not album or not album["tracks"]:
            raise HTTPException(502, "Couldn't load that album's songs. Try again.")
        job = store.create(f"catalog-album:{a.artist} - {a.title}", config.DEFAULT_OUTPUT_DIR)
        with job.lock:
            for i, t in enumerate(album["tracks"]):
                job.tracks.append(build_track(
                    i, t["video_id"] or "", _watch_url(t["video_id"]), f"{t['track_number'] or i + 1}. {t['title']}",
                    t["artist"].strip(), t["title"].strip(), "catalog",
                    duration=t["duration"] if t["video_id"] else None, target_duration=t["duration"], explicit=t["explicit"],
                ))
                job.tracks[-1]["catalog"] = {"album": album["title"] or a.title, "artwork": album.get("artwork") or "",
                                             "year": album.get("year") or "", "album_artist": album.get("artist") or a.artist}
            # The user picked this album explicitly: steer the lookup to it.
            job.album_hint = album["title"] or a.title
            job.status = "ready"
        if req.add:
            _set_playlist(job, req.add.playlist_mode, req.add.playlist_name, req.add.playlist_id)
            job.auto_accept = True
            job.notify = req.add.notify
            _claim(job, "ready", "processing")
            threading.Thread(target=_match_job, args=(job.id,), daemon=True).start()
        # The album page shows a play button per song: resolve the first
        # few streams now so those start quickly. Not all of them: each is a
        # YouTube request from the IP that also downloads, and a long queue
        # would delay the next song page's preview.
        for t in album["tracks"][:ALBUM_WARM_TRACKS]:
            warm_preview(t["video_id"])
        return job.to_dict()

    raise HTTPException(400, "Pick a song or an album.")


# Declared before "/api/jobs/{job_id}" so "active" isn't taken as a job id.
@router.get("/api/jobs/active")
def get_active_job():
    """The download currently running in the background, so the UI can show
    it again after the user navigated away or reopened the app."""
    job = store.active()
    return job.to_dict() if job else None


@router.get("/api/busy")
def busy():
    """For deploy.sh: a restart now would kill a download (job) or cut off someone's music (streams)."""
    return {"job": store.active() is not None, "streams": activity.streams()}


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    return job.to_dict()


@router.patch("/api/jobs/{job_id}/tracks/{index}")
def update_track(job_id: str, index: int, req: TrackUpdate):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")

    with job.lock:
        track = next((t for t in job.tracks if t["index"] == index), None)
        if not track:
            raise HTTPException(404, "Track not found.")
        if req.artist is not None:
            track["artist"] = req.artist.strip()
        if req.title is not None:
            track["title"] = req.title.strip()
        if req.accepted is not None:
            track["accepted"] = req.accepted
        if req.use_original is not None:
            if req.use_original and not track["url"]:
                raise HTTPException(400, "This song has no video of its own to use.")
            track["use_original"] = req.use_original

    return track


@router.post("/api/jobs/{job_id}/apply-default-artist")
def apply_default_artist(job_id: str, req: DefaultArtistRequest):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")

    artist = req.artist.strip()
    with job.lock:
        for track in job.tracks:
            if not track["artist"]:
                track["artist"] = artist

    return job.to_dict()


def _match_job(job_id: str, album_hint: str | None = None):
    """Phase 1: look up metadata for the whole accepted batch and pause for
    user confirmation before downloading/tagging anything. The caller has
    already moved the job to "processing" (see _claim)."""
    job = store.get(job_id)
    if not job:
        return
    try:
        _match_job_inner(job, album_hint)
    except Exception as exc:
        traceback.print_exc()
        with job.lock:
            job.error = "Looking up the album failed: " + friendly_error(exc)
            job.status = "error"


def _original_video(track) -> dict | None:
    if not track.get("url"):
        return None
    return {"id": track["id"], "url": track["url"], "duration": track.get("duration"), "title": track.get("raw_title", "")}


def _target_duration(track, match=None):
    """The official length the downloaded audio should have: the catalog
    row's length when the user picked from the catalog, else the match's."""
    return track.get("target_duration") or ((match if match is not None else track.get("match")) or {}).get("duration")


def _find_source(track, match) -> dict | None:
    """Which upload to download for this track (see sourcing.py)."""
    target = _target_duration(track, match)
    if track.get("confidence") == "catalog" and track.get("id"):
        # Picked from YouTube Music: that upload already is the album audio.
        return sourcing.make_source("album_audio", track["id"], track["url"], track["title"], track["artist"],
                                    track.get("duration"), target, True if target else None)
    has_match = bool(match and (match.get("album") or match.get("source")))
    artist = (match.get("artist") if has_match else "") or track["artist"]
    title = (match.get("title") if has_match else "") or track["title"]
    try:
        return sourcing.find_audio_source(artist, title, target, _original_video(track), track.get("explicit"))
    except Exception:
        traceback.print_exc()
        orig = _original_video(track)
        if not orig:
            return None
        ok = (abs(orig["duration"] - target) <= sourcing.TOLERANCE) if (orig["duration"] and target) else None
        return sourcing.make_source("your_video", orig["id"], orig["url"], orig["title"], "", orig["duration"], target, ok)


def _existing_path(job, track):
    """The library file this song already is, if any (shown on the song page)."""
    m = track.get("match") or {}
    if not m.get("album"):
        return None
    try:
        return tagger.find_existing_track(m, job.out_dir)
    except Exception:
        return None


def _lyrics_preview(text, kind):
    if not text:
        return {"kind": kind or "none", "lines": []}
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(\[[0-9:.]+\]\s*)+", "", line).strip()
        if line and line != "♪":
            lines.append(line)
        if len(lines) == 4:
            break
    return {"kind": kind, "lines": lines}


def _match_quality(track) -> str:
    """"none": no album found; "weak": what was found doesn't look like the
    song that was asked for (wrong title or artist -- usually a garbled
    video title); "good" otherwise. Weak/none ones are shown for fixing."""
    m = track.get("match") or {}
    if not m.get("album"):
        return "none"
    t = text_score(base_title(track["title"]).lower(), base_title(m.get("title", "")).lower())
    a = catalog.artist_match(track["artist"], [m.get("artist", "")]) if track["artist"] else 1.0
    return "good" if t >= 0.8 and a >= 0.6 else "weak"


# Cover art bytes by URL, shared by every job: playlists repeat albums.
_art_cache: dict = {}
_art_lock = threading.Lock()


def _artwork(match):
    url = match.get("artwork_url") or ""
    with _art_lock:
        if url and url in _art_cache:
            return _art_cache[url]
    data = metadata.artwork_bytes(match)
    if url and data:
        with _art_lock:
            if len(_art_cache) > 200:
                _art_cache.clear()
            _art_cache[url] = data
    return data


def _planned_audio_seconds(track):
    """Length of what will be downloaded for this track, as known before downloading."""
    if track.get("use_original"):
        return track.get("duration")
    src = track.get("source") or {}
    return src.get("duration") or track.get("duration")


def _prefetch_key(track, match):
    audio = _planned_audio_seconds(track)
    return (match.get("artist"), match.get("title"), match.get("album"),
            round(audio) if audio else None, _target_duration(track, match))


def _prefetch_one(job, track):
    """Fetches genre, cover art and lyrics during the lookup, so the
    download step only has to download and tag. Keyed on what they depend
    on: if the album or audio changes later, the download step fetches
    fresh instead of using stale data."""
    with job.lock:
        match = track.get("match")
        if not match or not match.get("album"):
            return
        key = _prefetch_key(track, match)
        if (job.prefetch.get(track["index"]) or {}).get("key") == key:
            return
        audio = _planned_audio_seconds(track)
        official = _target_duration(track, match)
    try:
        match = metadata.fill_genre(match)  # no-op when Deezer already gave one
        with job.lock:
            if track.get("match") and track["match"].get("album") == match.get("album"):
                track["match"] = {**track["match"], "genre": match.get("genre", "")}
        _artwork(match)
        text, kind = lyrics.fetch_lrc(match["artist"], match["title"], match.get("album", ""),
                                      official_duration=official, audio_duration=audio)
        with job.lock:
            job.prefetch[track["index"]] = {"key": key, "lyrics": (text, kind), "audio": audio}
            track["lyrics_preview"] = _lyrics_preview(text, kind)
    except Exception:
        traceback.print_exc()  # a bonus; the download step fetches it again


def _prefetch_tracks(job, tracks, announce=True):
    if announce:
        with job.lock:
            job.phase = "Getting cover art and lyrics"
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda t: _prefetch_one(job, t), tracks))
    finally:
        if announce:
            with job.lock:
                job.phase = ""


def _source_tracks(job, tracks, provisional=False):
    """Finds the audio for each track (a few at a time -- each is one or two
    YouTube Music searches). provisional: the match came from Deezer, whose
    length is only used when the catalog confirms it."""
    def one(track):
        with job.lock:
            track["status"] = "finding_audio"
            match = track.get("match")
            tentative = provisional and not track.get("target_duration")
            if tentative and match:
                match = {**match, "duration": None}
        src = _find_source(track, match)
        with job.lock:
            track["source"] = src
            track["source_tentative"] = tentative
            track["status"] = "audio_found"
            single = len(job.tracks) == 1
        if single and src:  # song page: its player is next
            warm_preview(src.get("video_id"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, tracks))


def _match_job_inner(job, album_hint):
    """Two passes. Deezer first (~1 s a song): every row fills in, the audio
    is found and the job is ready to confirm. Then MusicBrainz (~1 request/s)
    checks which album each song really belongs to -- the original album,
    not the compilation or deluxe reissue Deezer's search likes -- and
    corrects the matches in place (see _refine_with_musicbrainz). The user
    can add the song before that finishes; downloads wait for it before
    tagging."""
    album_hint = album_hint or job.album_hint
    with job.lock:
        to_process = [t for t in job.tracks if t["accepted"]]
        for track in job.tracks:
            track["status"] = "queued_lookup" if track["accepted"] else "skipped_not_accepted"
            track["user_fixed"] = False
        job.refined.clear()
        job.refining = True
        job.lookup_gen += 1
        gen = job.lookup_gen
        job.user_picked_album = False
        job.phase = "Looking up the songs"
    is_album = (job.url or "").startswith("catalog-album:")

    try:
        quick = _deezer_album_matches(job, to_process, album_hint) if is_album else None
        if quick is None:
            quick = _deezer_matches(job, to_process)
        quick = [_alt_catalog_match(t) or q or _catalog_match(t) for t, q in zip(to_process, quick)]
        with job.lock:
            for track, match in zip(to_process, quick):
                track["match"] = match
                track["on_album"] = None
                track["quality"] = _match_quality(track)
                track["existing"] = _existing_path(job, track)
                track["status"] = "looked_up"
            job.phase = "Finding the album audio"

        # Find each song's audio. A catalog pick knows its official length,
        # so this is final. Otherwise Deezer's length isn't trusted (its
        # search often lands on a live take or 12" mix): the audio found now
        # is provisional and MusicBrainz's length decides (see below).
        _source_tracks(job, to_process, provisional=True)
        with job.lock:
            for track in to_process:
                track["status"] = "awaiting_confirmation"
            job.phase = "Checking the album"
            job.status = "awaiting_confirmation"
        threading.Thread(target=_prefetch_tracks, args=(job, to_process, False), daemon=True).start()
        # Quick add from search: download what the lookup is sure about now
        # (tagging still waits for MusicBrainz, see _process_track); if any
        # song is unsure, decide after MusicBrainz has had its say.
        if job.auto_accept and _quick_add_decide(job, to_process, final=False):
            job.auto_accept = False

        _refine_with_musicbrainz(job, to_process, quick, album_hint, is_album, gen)
        if job.auto_accept:
            job.auto_accept = False
            _quick_add_decide(job, to_process, final=True)
    finally:
        with job.lock:
            if job.lookup_gen == gen:
                job.refining = False
                if job.phase.startswith(("Looking up the songs", "Finding the album audio", "Checking the album")):
                    job.phase = ""
                job.refined.set()


def _alt_catalog_match(track) -> dict | None:
    """A live / acoustic / remix pick from search: the search row itself is the truth. Deezer and MusicBrainz would answer with the
    studio song (same title, another album), and the library would then "already have it" and skip the download."""
    if track.get("confidence") == "catalog" and textmatch.is_alt_version(track.get("title") or ""):
        return _catalog_match(track)
    return None


def _catalog_match(track) -> dict | None:
    """The search row the user tapped, as a match -- for songs no metadata
    service knows (covers, small artists): its album, year and cover are
    still right."""
    cat = track.get("catalog") or {}
    if track.get("confidence") != "catalog" or not cat.get("album"):
        return None
    return {"artist": track["artist"], "album_artist": cat.get("album_artist") or track["artist"], "title": track["title"],
            "album": cat["album"], "year": (cat.get("year") or "")[:4], "genre": "", "artwork_url": cat.get("artwork") or "",
            "track_number": None, "disc_number": None, "duration": track.get("target_duration"), "source": "catalog"}


def _quick_add_decide(job, tracks, final: bool) -> bool:
    """Starts a quick add's download if the lookup is sure enough. Not
    final (Deezer only): only when every song is sure. Final (after
    MusicBrainz): sure songs go ahead, unsure ones are left out -- a lone
    unsure song waits for the user (job.needs_check). Returns True when
    decided."""
    with job.lock:
        if job.status != "awaiting_confirmation":
            return True
        unsure = [t for t in tracks if t.get("quality") != "good" or not (t.get("source") or t.get("use_original"))]
        if unsure and not final:
            return False
        sure = [t for t in tracks if t not in unsure]
        if len(tracks) > 1:
            for t in unsure:
                t["accepted"] = False  # skipped; the rest of the album goes ahead
        job.needs_check = len(unsure)
    if sure and (len(tracks) > 1 or not unsure):
        try:
            _claim(job, "awaiting_confirmation", "processing")
        except HTTPException:
            return True  # the user acted on it meanwhile (added or cancelled it)
        threading.Thread(target=_finish_job, args=(job.id,), daemon=True).start()
    return True


def _deezer_matches(job, tracks):
    """Deezer's quick answer per song, a few at a time."""
    def one(track):
        with job.lock:
            track["status"] = "looking_up"
        try:
            return deezer.match_song(track["artist"], track["title"], track.get("target_duration"), track.get("explicit"))
        except Exception:
            traceback.print_exc()
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(one, tracks))


def _deezer_album_matches(job, tracks, album_hint):
    """An album picked in search: one Deezer album lookup covers every
    song (no per-song searches). None if Deezer doesn't have it."""
    artist = ((tracks[0].get("catalog") or {}).get("album_artist") if tracks else "") or (tracks[0]["artist"] if tracks else "")
    try:
        info = deezer.find_album(artist, album_hint or "")
    except Exception:
        traceback.print_exc()
        info = None
    if not info:
        return None
    return [deezer.match_on_album(info, t["artist"], t["title"], t["index"] + 1) for t in tracks]


def _refine_with_musicbrainz(job, tracks, quick, album_hint, is_album, gen):
    """MusicBrainz decides the album; the matches shown so far are replaced
    where it disagrees (and the audio re-found if the length changed)."""
    lookup = [{"artist": t["artist"], "title": t["title"]} for t in tracks]

    def step(text):
        with job.lock:
            if job.lookup_gen == gen:
                job.phase = "Checking the album" + (f" ({text.split(' ', 2)[-1]})" if text.startswith("Picking albums") else "")

    mb_matches, candidates = None, []
    try:
        if is_album:
            mb_matches, candidates = _mb_album_matches(tracks, quick, album_hint)
        if mb_matches is None:
            mb_matches = metadata.resolve_batch(lookup, album_hint=album_hint, on_step=step)
            candidates = metadata.album_candidates(lookup, mb_matches, album_hint=album_hint)
    except Exception:
        traceback.print_exc()
        return  # Deezer's answer stands

    final = [metadata.with_deezer(mb, q, t["artist"]) for t, mb, q in zip(tracks, mb_matches, quick)]
    final = [m if (m and m.get("album")) else (_catalog_match(t) or m) for t, m in zip(tracks, final)]
    final = [_alt_catalog_match(t) or m for t, m in zip(tracks, final)]
    guess = Counter(m["album"] for m in final if m and m.get("album")).most_common(1)
    guess = guess[0][0] if guess else None
    guess_id = next((c["collection_id"] for c in candidates if c["album"] == guess), None)

    resource = []
    with job.lock:
        if job.lookup_gen != gen or job.status not in ("awaiting_confirmation", "processing") or job.user_picked_album:
            return
        for track, match in zip(tracks, final):
            if track.get("user_fixed") or not match:
                continue
            old = track.get("match") or {}
            old_quality = track.get("quality")
            track["match"] = match
            track["quality"] = _match_quality(track)
            if old_quality == "good" and track["quality"] != "good":
                # MusicBrainz/iTunes found something less like the song
                # than Deezer did: keep Deezer's answer.
                track["match"], track["quality"] = old, old_quality
                continue
            track["existing"] = _existing_path(job, track)
            if track["status"] in ("awaiting_confirmation", "queued_download") and (
                    track.get("source_tentative") or _length_changed(track, old, match)):
                resource.append((track, match))
        job.album_candidates = candidates
        job.guess_album_id = guess_id
        job.selected_album_id = None
    def resource_one(tm):
        track, match = tm
        src = _find_source(track, match)
        with job.lock:
            if track["status"] in ("awaiting_confirmation", "queued_download") and not track.get("use_original"):
                track["source"] = src
            track["source_tentative"] = False

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(resource_one, resource))
    # Lyrics/cover for whatever changed (keyed, so unchanged ones are skipped).
    _prefetch_tracks(job, tracks, False)


def _length_changed(track, old, new) -> bool:
    if track.get("target_duration"):
        return False  # a catalog pick's length is the song's own
    a, b = (old or {}).get("duration"), (new or {}).get("duration")
    src = track.get("source")
    if not src:
        return True
    return bool(b) and (not a or abs(a - b) > sourcing.TOLERANCE)


def _mb_album_matches(tracks, quick, album_hint):
    """An album picked in search: find it on MusicBrainz once (release
    group + its tracklist, ~3 requests) instead of searching every song."""
    artist = ((tracks[0].get("catalog") or {}).get("album_artist") if tracks else "") or (tracks[0]["artist"] if tracks else "")
    groups = musicbrainz.search_release_groups(artist, album_hint or "")
    groups = [g for g in groups if metadata._hint_matches(album_hint or "", g["rg_title"])]
    if not groups:
        return None, []
    groups.sort(key=lambda g: (musicbrainz.type_rank(g["rg_primary"], g["rg_secondary"], g["release_artist_id"], g["rg_title"]),
                               g["rg_title"].lower() != (album_hint or "").lower(), g["date"] or "9999"))
    best = groups[0]
    lookup = [{"artist": t["artist"], "title": t["title"]} for t in tracks]
    result = metadata.match_to_album(lookup, [q or {} for q in quick], f"mb:{best['rg_id']}:{best['release_id'] or ''}")
    if result is None:
        return None, []
    matches, on_album = result
    # Songs not on that MusicBrainz release (bonus tracks) keep Deezer's answer.
    matches = [m if on else (q or None) for m, on, q in zip(matches, on_album, quick)]
    candidates = [{
        "collection_id": f"mb:{g['rg_id']}:{g['release_id'] or ''}",
        "album": g["rg_title"], "artist": g["release_artist"], "year": (g["date"] or "")[:4],
        "artwork_url": musicbrainz.cover_url(g["rg_id"]), "track_count": None, "coverage": len(tracks),
        "original": len(tracks), "hint_match": True,
        "kind": musicbrainz.type_label(g["rg_primary"], g["rg_secondary"], g["release_artist_id"]),
    } for g in groups[:10]]
    return matches, candidates


def _finish_job(job_id: str):
    """Phase 2: download, tag, and fetch lyrics for every track that has a
    confirmed match. Only runs after the user accepts the match; the caller
    has already moved the job to "processing" (see _claim). Whatever
    happens, the job ends as "done" -- one bad track (or a failing playlist
    step) is reported on that track, never leaves the UI spinning."""
    job = store.get(job_id)
    if not job:
        return
    try:
        with job.lock:
            # Songs unticked after the lookup (album page) are left out.
            for t in job.tracks:
                if t["status"] == "awaiting_confirmation" and not t["accepted"]:
                    t["status"] = "skipped_not_accepted"
            to_process = [t for t in job.tracks if t["status"] == "awaiting_confirmation"]
            for t in to_process:
                t["status"] = "queued_download"
        # A few downloads at once; tagging and file moves are serialized
        # (_tag_lock) so two songs of one album don't race on its folder.
        with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
            list(pool.map(lambda t: _process_track(job, t), to_process))
        if job.playlist_mode != "none":
            try:
                _apply_playlist(job)
            except Exception as exc:
                traceback.print_exc()
                job.playlist_error = "Playlist not updated: " + friendly_error(exc)
    finally:
        with job.lock:
            job.status = "done"
        notify.job_finished(job)


DOWNLOAD_WORKERS = 3
_tag_lock = threading.Lock()


def _skip_if_existing(job, track) -> bool:
    match = track.get("match")
    if not match or not match.get("album"):
        return False
    with _tag_lock:
        existing = tagger.find_existing_track(match, job.out_dir)
    if existing:
        with job.lock:
            track["status"] = "skipped_exists"
            track["detail"] = existing
    return bool(existing)


AGE_RESTRICTED = "age-restricted"


def _download_with_fallback(job, track, video, work_dir):
    """Download the chosen upload. If YouTube says it is age-restricted (usually the music video), try the song's other
    uploads (the plain audio version is rarely restricted) before giving up."""
    try:
        return downloader.download_audio(video, work_dir)
    except Exception as exc:
        if AGE_RESTRICTED not in friendly_error(exc):
            raise
        first = exc
    match = track.get("match") or {}
    has_match = bool(match.get("album") or match.get("source"))
    artist = (match.get("artist") if has_match else "") or track["artist"]
    title = (match.get("title") if has_match else "") or track["title"]
    tried = {video.get("id")}
    try:
        alts = sourcing.audio_candidates(artist, title, _target_duration(track, match), _original_video(track), track.get("explicit"))
    except Exception:
        alts = []
    for alt in alts[:5]:
        if not alt.get("url") or alt.get("video_id") in tried:
            continue
        tried.add(alt.get("video_id"))
        try:
            path = downloader.download_audio({"id": alt["video_id"], "url": alt["url"]}, work_dir)
        except Exception as exc:
            if AGE_RESTRICTED in friendly_error(exc):
                continue
            raise
        with job.lock:
            track["source"] = alt
        return path
    raise first


def _process_track(job, track):
    # While MusicBrainz is still checking the album, download anyway and
    # decide where the file goes (and whether it's already there) after.
    refined = job.refined.is_set()
    if not refined and track.get("source_tentative") and not track.get("use_original"):
        # Which upload to fetch depends on MusicBrainz's length: wait for it.
        job.refined.wait(timeout=180)
        refined = True
    if refined and _skip_if_existing(job, track):
        return

    # Download into a private temp folder, not the library: a half-finished
    # or failed download must never show up in the library (or Navidrome).
    src = track.get("source")
    if track.get("use_original") and track.get("url"):
        video = {"id": track["id"], "url": track["url"]}
    elif src and src.get("url"):
        video = {"id": src.get("video_id") or track["id"] or "audio", "url": src["url"]}
    elif track.get("url"):
        video = {"id": track["id"], "url": track["url"]}
    else:
        with job.lock:
            track["status"] = "failed"
            track["detail"] = "Couldn't find this song on YouTube."
        return

    work_dir = tempfile.mkdtemp(prefix="tuberipper-")
    try:
        with job.lock:
            track["status"] = "downloading"
        filepath = _download_with_fallback(job, track, video, work_dir)
        if not refined:
            job.refined.wait(timeout=180)
            if _skip_if_existing(job, track):
                return
        match = track["match"] or metadata._blank_match(track["artist"], track["title"])
        if not track.get("use_original") and (src or {}).get("kind") == "album_audio":
            # Gapless albums: the album upload can run into the next song.
            downloader.trim_to_song(filepath, _target_duration(track, match), tagger.audio_length(filepath))

        with job.lock:
            track["status"] = "tagging"
            pre = job.prefetch.get(track["index"])
            if pre and pre["key"] != _prefetch_key(track, match):
                pre = None  # album or audio changed after the lookup
        if not match.get("genre"):
            match = metadata.fill_genre(match)
            with job.lock:
                track["match"] = match
        artwork_bytes = _artwork(match)
        if not artwork_bytes and (track.get("catalog") or {}).get("artwork"):
            artwork_bytes = metadata.fetch_artwork(track["catalog"]["artwork"])
        with _tag_lock:
            new_path = tagger.write_tags(filepath, match, artwork_bytes, library_root=job.out_dir)
            # Keep the whole album folder agreeing with this song, so Navidrome
            # doesn't split it into two albums (see tagger.unify_album_folder).
            try:
                tagger.unify_album_folder(os.path.dirname(new_path), reference_path=new_path)
            except Exception:
                traceback.print_exc()

        duration = tagger.audio_length(new_path)
        try:
            # Lyrics found during the lookup were chosen for the audio's
            # planned length; reuse them if the file really is that long.
            if pre and pre["audio"] and duration and abs(pre["audio"] - duration) <= 2:
                lrc_text, kind = pre["lyrics"]
            else:
                with job.lock:
                    track["status"] = "fetching_lyrics"
                lrc_text, kind = lyrics.fetch_lrc(match["artist"], match["title"], match.get("album", ""),
                                                  official_duration=_target_duration(track, match), audio_duration=duration)
            if lrc_text:
                with open(os.path.splitext(new_path)[0] + ".lrc", "w", encoding="utf-8") as f:
                    f.write(lrc_text)
            with job.lock:
                track["lyrics"] = kind or "none"
        except Exception:
            traceback.print_exc()  # lyrics are a bonus; the song itself is saved

        library_index.invalidate()
        with job.lock:
            track["status"] = "done"
            track["detail"] = new_path
    except Exception as exc:
        traceback.print_exc()
        with job.lock:
            track["status"] = "failed"
            track["detail"] = friendly_error(exc)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _apply_playlist(job):
    """Puts the job's tracks, in their original order, into a playlist --
    including tracks that were already in the library (skipped_exists),
    since they're still part of what the user asked for. Uses Navidrome's
    API when configured (a normal, editable Navidrome playlist); without it,
    "new" falls back to an .m3u8 file Navidrome imports on its next scan."""
    with job.lock:
        saved = [
            t for t in sorted(job.tracks, key=lambda t: t["index"])
            if t["status"] in ("done", "skipped_exists") and t["detail"] and t["match"]
        ]
        any_new_files = any(t["status"] == "done" for t in saved)
    if not saved:
        job.playlist_error = "No songs were saved, so nothing was added to a playlist."
        return

    if not navidrome.configured():
        if job.playlist_mode == "existing":
            job.playlist_error = "Navidrome isn't set up, so the songs couldn't be added to that playlist."
            return
        entries = [(t["detail"], t["match"].get("artist") or t["artist"], t["match"].get("title") or t["title"]) for t in saved]
        try:
            job.playlist_path = playlists.write_m3u(job.out_dir, job.playlist_name, entries)
            job.playlist_status = f"Playlist \"{job.playlist_name}\" saved — it'll appear in Navidrome after its next scan."
        except OSError as exc:
            job.playlist_error = f"Couldn't write the playlist: {exc}"
        return

    try:
        metas = [t["match"] for t in saved]
        scanned = False
        if any_new_files:
            job.playlist_status = "Waiting for Navidrome to scan the new songs…"
            navidrome.wait_for_scan()
            scanned = True
        job.playlist_status = "Adding to playlist…"
        ids, missing = navidrome.find_song_ids(metas, retries=2 if scanned else 0)
        if missing and not scanned:
            # Songs that were already on disk but Navidrome hasn't indexed
            # yet (e.g. downloaded earlier with the playlist option off).
            job.playlist_status = "Waiting for Navidrome to scan…"
            navidrome.wait_for_scan()
            job.playlist_status = "Adding to playlist…"
            ids, missing = navidrome.find_song_ids(metas)
        if not ids:
            job.playlist_error = "Navidrome couldn't find the downloaded songs, so the playlist wasn't changed."
            return
        miss_note = f" ({missing} couldn't be found in Navidrome)" if missing else ""
        if job.playlist_mode == "new":
            navidrome.create_playlist(job.playlist_name, ids)
            job.playlist_status = f"Created playlist \"{job.playlist_name}\" with {len(ids)} song{'' if len(ids) == 1 else 's'}{miss_note}."
        else:
            added = navidrome.add_to_playlist(job.playlist_id, ids)
            already = len(ids) - added
            if added:
                job.playlist_status = (
                    f"Added {added} song{'' if added == 1 else 's'} to \"{job.playlist_name}\""
                    + (f", {already} already in it" if already else "") + f"{miss_note}."
                )
            else:
                job.playlist_status = f"Already in \"{job.playlist_name}\"{miss_note}."
    except navidrome.NavidromeError as exc:
        job.playlist_error = f"Playlist not updated: {exc}"


def _set_playlist(job, mode, name, playlist_id):
    if mode not in ("none", "new", "existing"):
        raise HTTPException(400, "playlist_mode must be none, new or existing")
    if mode == "existing" and not playlist_id:
        raise HTTPException(400, "Pick a playlist to add to.")
    with job.lock:
        job.playlist_mode = mode
        job.playlist_id = playlist_id if mode == "existing" else None
        job.playlist_name = (name or "").strip() or job.playlist_title or "YouTube playlist"


@router.post("/api/jobs/{job_id}/start")
def start_job(job_id: str, req: StartJobRequest | None = None):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    if req and req.playlist_mode is not None:
        _set_playlist(job, req.playlist_mode, req.playlist_name, req.playlist_id)
    _claim(job, "ready", "processing")
    threading.Thread(target=_match_job, args=(job_id,), daemon=True).start()
    return job.to_dict()


@router.get("/api/navidrome/playlists")
def navidrome_playlists():
    if not navidrome.configured():
        return {"configured": False, "playlists": [], "error": None}
    try:
        return {"configured": True, "playlists": navidrome.list_playlists(), "error": None}
    except navidrome.NavidromeError as exc:
        return {"configured": True, "playlists": [], "error": str(exc)}


@router.post("/api/jobs/{job_id}/pick-album")
def pick_album(job_id: str, req: PickAlbumRequest):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    if job.status != "awaiting_confirmation":
        raise HTTPException(400, f"Job is not awaiting confirmation (status: {job.status}).")

    with job.lock:
        to_match = [t for t in job.tracks if t["status"] == "awaiting_confirmation"]
        lookup_tracks = [{"artist": t["artist"], "title": t["title"]} for t in to_match]
        current = [t["match"] for t in to_match]

    result = metadata.match_to_album(lookup_tracks, current, req.collection_id)
    if result is None:
        raise HTTPException(502, "Couldn't load that album from iTunes. Try again.")
    new_matches, on_album = result

    # A different album can mean a different recording (remaster, radio
    # edit) with a different length: re-find the audio where the one found
    # earlier no longer fits.
    def needs_new_source(track, match):
        src = track.get("source")
        target = _target_duration(track, match)
        if not src or not target or track.get("target_duration"):
            return not src
        return src.get("duration") is None or abs(src["duration"] - target) > sourcing.TOLERANCE

    redo = [(t, m) for t, m in zip(to_match, new_matches) if needs_new_source(t, m)]
    if redo:
        with ThreadPoolExecutor(max_workers=4) as pool:
            new_sources = list(pool.map(lambda tm: _find_source(*tm), redo))
    else:
        new_sources = []

    with job.lock:
        for track, match, on in zip(to_match, new_matches, on_album):
            track["match"] = match
            track["on_album"] = on
            track["quality"] = _match_quality(track)
            track["existing"] = _existing_path(job, track)
        for (track, _), src in zip(redo, new_sources):
            track["source"] = src
        job.selected_album_id = req.collection_id
        job.user_picked_album = True

    # Lyrics/art for the new album, in the background (only what changed).
    threading.Thread(target=_prefetch_tracks, args=(job, to_match), daemon=True).start()
    return job.to_dict()


def _confirm_track(job_id: str, index: int):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    if job.status != "awaiting_confirmation":
        raise HTTPException(400, f"Job is not awaiting confirmation (status: {job.status}).")
    with job.lock:
        track = next((t for t in job.tracks if t["index"] == index and t["status"] == "awaiting_confirmation"), None)
    if not track:
        raise HTTPException(404, "Track not found.")
    return job, track


@router.get("/api/jobs/{job_id}/tracks/{index}/sources")
def track_sources(job_id: str, index: int):
    """Other uploads of this song to choose from (song page "Change")."""
    job, track = _confirm_track(job_id, index)
    with job.lock:
        match = track.get("match") or {}
        has_match = bool(match.get("album") or match.get("source"))
        artist = (match.get("artist") if has_match else "") or track["artist"]
        title = (match.get("title") if has_match else "") or track["title"]
        target, original, explicit = _target_duration(track, match), _original_video(track), track.get("explicit")
    alts = sourcing.audio_candidates(artist, title, target, original, explicit)
    current = track.get("source")
    if current and current["video_id"] not in {a["video_id"] for a in alts}:
        alts.insert(0, current)
    with job.lock:
        track["alternatives"] = alts
    return alts


@router.post("/api/jobs/{job_id}/tracks/{index}/source")
def choose_source(job_id: str, index: int, req: SourceChoice):
    job, track = _confirm_track(job_id, index)
    with job.lock:
        pick = next((a for a in track.get("alternatives") or [] if a["video_id"] == req.video_id), None)
        if not pick:
            raise HTTPException(400, "Load the list of uploads first.")
        track["source"] = pick
        track["use_original"] = False
    warm_preview(pick["video_id"])
    _prefetch_one(job, track)  # lyrics depend on the audio's length
    return job.to_dict()


# Preview: the phone can't fetch YouTube's audio itself (the stream URL is
# tied to the IP that asked for it), so the server resolves it and passes
# the bytes through, Range requests included so seeking works.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_stream_cache: dict = {}
_stream_pending: dict = {}  # video_id -> Future of an extraction in flight
_stream_lock = threading.Lock()
_stream_pool = ThreadPoolExecutor(max_workers=3)


def _resolve_stream(video_id: str) -> dict:
    try:
        info = downloader.audio_stream(video_id)
        entry = {**info, "expires": time.time() + 3 * 3600}  # YouTube URLs last ~6 h
        with _stream_lock:
            if len(_stream_cache) > 100:
                _stream_cache.clear()
            _stream_cache[video_id] = entry
        return entry
    finally:
        with _stream_lock:
            _stream_pending.pop(video_id, None)


def _stream_future(video_id: str):
    """The cached stream, or the one extraction in flight for it (a warm-up
    and the player's request share it instead of extracting twice)."""
    with _stream_lock:
        hit = _stream_cache.get(video_id)
        if hit and hit["expires"] > time.time():
            return None, hit
        fut = _stream_pending.get(video_id)
        if fut is None:
            fut = _stream_pending[video_id] = _stream_pool.submit(_resolve_stream, video_id)
        return fut, None


def warm_preview(video_id: str | None):
    """Starts resolving a preview stream in the background, so by the time
    the user taps play the URL exists and its availability delay has
    (mostly) passed."""
    if video_id and _VIDEO_ID_RE.match(video_id):
        _stream_future(video_id)


recommend.set_warmer(warm_preview)


def _stream_info(video_id: str) -> dict:
    fut, hit = _stream_future(video_id)
    entry = hit or fut.result(timeout=90)
    wait = (entry.get("available_at") or 0) - time.time()
    if wait > 0:
        time.sleep(min(wait + 0.5, 30))
    return entry


# Declared before "/api/preview/{video_id}" so "status" isn't a video id.
@router.get("/api/preview-status/{video_id}")
def preview_status(video_id: str):
    """Where a preview stream is, for the player's countdown:
    "idle" (not requested yet), "resolving" (asking YouTube for the stream,
    a few seconds), "waiting" (YouTube releases it in `wait` seconds --
    its delay for non-browser clients), "ready", or "error"."""
    if not _VIDEO_ID_RE.match(video_id):
        raise HTTPException(400, "Bad video id.")
    with _stream_lock:
        hit = _stream_cache.get(video_id)
        pending = _stream_pending.get(video_id)
    if hit and hit["expires"] > time.time():
        wait = (hit.get("available_at") or 0) - time.time()
        return {"state": "waiting", "wait": round(wait, 1)} if wait > 0 else {"state": "ready", "wait": 0}
    if pending is not None:
        if pending.done() and pending.exception() is not None:
            return {"state": "error", "wait": None}
        return {"state": "resolving", "wait": None}
    return {"state": "idle", "wait": None}


# YouTube's m4a is a *fragmented* MP4 (moov + sidx + many moof/mdat pieces).
# iPhone Safari mis-measures those: "Teenage Dirtbag" (4:02) showed 8:03 and
# played silence after 4:02. So previews are remuxed (stream copy, no
# re-encode) into a plain MP4 first and served from disk, which also gives
# exact duration and seeking.
_PREVIEW_DIR = os.path.join(tempfile.gettempdir(), "tuberipper-previews")
_PREVIEW_KEEP = 40
_preview_locks: dict = {}


def _preview_file(video_id: str, info: dict) -> str | None:
    """A plain-MP4 copy of the preview audio on disk, or None if it can't be
    made (the caller then streams YouTube's own bytes)."""
    if info.get("mime") != "audio/mp4" or not shutil.which("ffmpeg"):
        return None
    os.makedirs(_PREVIEW_DIR, exist_ok=True)
    path = os.path.join(_PREVIEW_DIR, f"{video_id}.m4a")
    with _stream_lock:
        lock = _preview_locks.setdefault(video_id, threading.Lock())
    with lock:
        if os.path.exists(path):
            os.utime(path)
            return path
        src, tmp = path + ".src", path + ".part"
        try:
            up = requests.get(info["url"], headers={"User-Agent": info.get("user_agent") or "Mozilla/5.0", "Range": "bytes=0-"},
                              stream=True, timeout=20)
            if up.status_code in (403, 410):
                with _stream_lock:
                    _stream_cache.pop(video_id, None)
            if up.status_code not in (200, 206):
                up.close()
                return None
            with open(src, "wb") as f:
                for chunk in up.iter_content(256 * 1024):
                    f.write(chunk)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", tmp],
                           check=True, capture_output=True, timeout=120)
            os.replace(tmp, path)
        except (OSError, requests.RequestException, subprocess.SubprocessError):
            traceback.print_exc()
            return None
        finally:
            for leftover in (src, tmp):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
        try:
            files = sorted((os.path.join(_PREVIEW_DIR, n) for n in os.listdir(_PREVIEW_DIR) if n.endswith(".m4a")),
                           key=os.path.getmtime, reverse=True)
            for old in files[_PREVIEW_KEEP:]:
                os.remove(old)
        except OSError:
            pass
    return path


@router.get("/api/preview/{video_id}")
def preview(video_id: str, request: Request):
    if not _VIDEO_ID_RE.match(video_id):
        raise HTTPException(400, "Bad video id.")
    try:
        info = _stream_info(video_id)
    except Exception as exc:
        raise HTTPException(502, "Couldn't load this audio: " + friendly_error(exc))
    local = _preview_file(video_id, info)
    if local:
        return FileResponse(local, media_type="audio/mp4", headers={"Cache-Control": "no-store"})
    headers = {"User-Agent": info.get("user_agent") or "Mozilla/5.0"}
    rng = request.headers.get("range")
    # Always ask for a range: YouTube throttles plain (unranged) requests
    # to ~30 KB/s but serves ranged ones at full speed.
    headers["Range"] = rng or "bytes=0-"
    upstream = requests.get(info["url"], headers=headers, stream=True, timeout=20)
    if upstream.status_code in (403, 410):
        with _stream_lock:
            _stream_cache.pop(video_id, None)  # expired URL; the player's retry gets a fresh one
    if upstream.status_code not in (200, 206):
        upstream.close()
        raise HTTPException(502, f"YouTube refused the preview ({upstream.status_code}).")
    out = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    for h in ("Content-Length", "Content-Range") if rng else ("Content-Length",):
        if upstream.headers.get(h):
            out[h] = upstream.headers[h]
    status = upstream.status_code if rng else 200

    def body():
        try:
            for chunk in upstream.iter_content(64 * 1024):
                yield chunk
        finally:
            upstream.close()

    return StreamingResponse(body(), status_code=status, headers=out,
                             media_type=info.get("mime") or "audio/mp4")


@router.post("/api/jobs/{job_id}/tracks/{index}/lookup")
def relookup_track(job_id: str, index: int, req: RelookupRequest):
    """The user fixed a song's artist/title on the confirm screen: look up
    just that song again (album, audio, lyrics), leaving the rest alone."""
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    if job.status != "awaiting_confirmation":
        raise HTTPException(400, f"Job is not awaiting confirmation (status: {job.status}).")
    artist, title = req.artist.strip(), req.title.strip()
    if not title:
        raise HTTPException(400, "Enter the song title.")
    with job.lock:
        track = next((t for t in job.tracks if t["index"] == index and t["status"] == "awaiting_confirmation"), None)
        if not track:
            raise HTTPException(404, "Track not found.")
        track["artist"], track["title"] = artist, title
        track["user_fixed"] = True

    match = metadata.with_deezer(metadata.resolve_batch([{"artist": artist, "title": title}])[0],
                                 deezer.match_song(artist, title), artist)
    with job.lock:
        track["match"] = match
        track["on_album"] = None
        track["quality"] = _match_quality(track)
        track["existing"] = _existing_path(job, track)
        # A catalog pick's length belonged to the old song.
        if track.get("confidence") != "catalog":
            track["target_duration"] = None
    src = _find_source(track, match)
    with job.lock:
        track["source"] = src
        track["use_original"] = False
    if src and len(job.tracks) == 1:
        warm_preview(src.get("video_id"))
    _prefetch_one(job, track)
    return job.to_dict()


@router.post("/api/jobs/{job_id}/confirm-match")
def confirm_match(job_id: str, req: ConfirmMatchRequest):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    if job.status != "awaiting_confirmation":
        raise HTTPException(400, f"Job is not awaiting confirmation (status: {job.status}).")

    if req.action == "accept":
        if req.playlist_mode not in ("none", "new", "existing"):
            raise HTTPException(400, "playlist_mode must be none, new or existing")
        if req.playlist_mode == "existing" and not req.playlist_id:
            raise HTTPException(400, "Pick a playlist to add to.")
        _claim(job, "awaiting_confirmation", "processing")
        job.notify = req.notify
        _set_playlist(job, req.playlist_mode, req.playlist_name, req.playlist_id)
        threading.Thread(target=_finish_job, args=(job_id,), daemon=True).start()
    elif req.action == "retry":
        _claim(job, "awaiting_confirmation", "processing")
        threading.Thread(target=_match_job, args=(job_id, req.album_hint), daemon=True).start()
    elif req.action == "cancel":
        with job.lock:
            for track in job.tracks:
                if track["status"] == "awaiting_confirmation":
                    track["status"] = "skipped_cancelled"
            job.status = "cancelled"
    else:
        raise HTTPException(400, "action must be one of: accept, retry, cancel")

    return job.to_dict()


@router.post("/api/jobs/{job_id}/retry-failed")
def retry_failed(job_id: str):
    """Re-runs just the tracks that failed (typically a temporary YouTube
    block). The playlist step isn't repeated -- "New" would create a second
    playlist with the same name."""
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    _claim(job, "done", "processing")
    with job.lock:
        failed = [t for t in job.tracks if t["status"] == "failed"]
        for t in failed:
            t["status"] = "awaiting_confirmation"
            t["detail"] = ""
        job.playlist_mode = "none"
    if not failed:
        with job.lock:
            job.status = "done"
        raise HTTPException(400, "Nothing failed, so there's nothing to retry.")
    threading.Thread(target=_finish_job, args=(job_id,), daemon=True).start()
    return job.to_dict()


# ---------- Library browsing / deletion ----------
# Files are organized by tagger.build_target_path as
# <library_root>/<Artist>/<Album>/<Track> - <Title>.m4a (older files: .mp3)

LIBRARY_ROOT = config.DEFAULT_OUTPUT_DIR


def _safe_library_path(*parts: str) -> str:
    """Resolves parts under LIBRARY_ROOT, rejecting anything that would
    escape it (e.g. '..' segments)."""
    path = os.path.normpath(os.path.join(LIBRARY_ROOT, *parts))
    root = os.path.normpath(LIBRARY_ROOT)
    if os.path.commonpath([path, root]) != root:
        raise HTTPException(400, "Invalid library path.")
    return path


def _prune_empty_dirs(path: str):
    """After deleting a track/album, remove now-empty parent folders up to
    (but not including) the library root."""
    root = os.path.normpath(LIBRARY_ROOT)
    while path and os.path.normpath(path) != root and os.path.isdir(path):
        try:
            if os.listdir(path):
                break
            os.rmdir(path)
            path = os.path.dirname(path)
        except OSError:
            break


def _artist_dirs() -> list:
    """Top-level library folders that are artists -- not the Playlists
    folder (playlists.PLAYLISTS_DIR) or hidden folders."""
    try:
        return sorted(
            (e for e in os.listdir(LIBRARY_ROOT)
             if os.path.isdir(os.path.join(LIBRARY_ROOT, e))
             and e != playlists.PLAYLISTS_DIR and not e.startswith(".")),
            key=str.casefold,
        )
    except OSError:
        return []


@router.get("/api/library")
def list_artists():
    return _artist_dirs()


# Must be declared before "/api/library/{artist}" -- FastAPI matches path
# routes in declaration order, and "search" would otherwise be swallowed as
# an artist name by that route.
@router.get("/api/library/search")
def search_library(q: str):
    q = q.strip().lower()
    if not q:
        return []

    results = []
    artists = _artist_dirs()

    for artist in artists:
        if len(results) >= 200:
            break
        artist_dir = os.path.join(LIBRARY_ROOT, artist)
        if q in artist.lower():
            results.append({"type": "artist", "artist": artist})

        try:
            albums = sorted(e for e in os.listdir(artist_dir) if os.path.isdir(os.path.join(artist_dir, e)))
        except OSError:
            albums = []

        for album in albums:
            album_dir = os.path.join(artist_dir, album)
            if q in album.lower():
                results.append({"type": "album", "artist": artist, "album": album})

            try:
                tracks = sorted(e for e in os.listdir(album_dir) if e.lower().endswith(tagger.AUDIO_EXTENSIONS))
            except OSError:
                tracks = []
            for track in tracks:
                if q in track.lower():
                    results.append({"type": "track", "artist": artist, "album": album, "track": track})

    return results[:200]


@router.get("/api/library/recent")
def library_recent(limit: int = 12):
    return library_index.recent_albums(LIBRARY_ROOT, max(1, min(limit, 60)))


@router.get("/api/library/albums")
def library_all_albums():
    return library_index.all_albums(LIBRARY_ROOT)


@router.get("/api/library/songs")
def library_all_songs():
    return library_index.all_songs(LIBRARY_ROOT)


def _m3u_dir() -> str:
    return os.path.join(LIBRARY_ROOT, playlists.PLAYLISTS_DIR)


def _m3u_paths(path: str) -> list:
    """Library-relative song paths listed in an .m3u8 file."""
    base = os.path.dirname(path)
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                rel = os.path.relpath(os.path.normpath(os.path.join(base, line)), LIBRARY_ROOT).replace(os.sep, "/")
                if not rel.startswith(".."):
                    out.append(rel)
    except OSError:
        pass
    return out


@router.get("/api/library/playlists")
def library_playlists():
    """Navidrome's playlists (when connected) plus any .m3u8 files tuberipper
    wrote that Navidrome hasn't picked up yet."""
    out = []
    if navidrome.configured():
        try:
            out = list(navidrome.list_playlists())
        except navidrome.NavidromeError:
            out = []
    have = {p["name"].casefold() for p in out}
    try:
        files = sorted((f for f in os.listdir(_m3u_dir()) if f.lower().endswith(".m3u8")), key=str.casefold)
    except OSError:
        files = []
    for f in files:
        name = f[:-5]
        if name.casefold() not in have:
            out.append({"id": "m3u:" + f, "name": name, "song_count": len(_m3u_paths(os.path.join(_m3u_dir(), f)))})
    return out


@router.get("/api/library/playlist")
def library_playlist(id: str):
    """A playlist's songs, each with its library path when we have the file (so it can play)."""
    songs = []
    if id.startswith("m3u:"):
        path = os.path.join(_m3u_dir(), os.path.basename(id[4:]))
        for rel in _m3u_paths(path):
            s = library_index.song_for_path(LIBRARY_ROOT, rel)
            if s:
                songs.append(s)
    else:
        try:
            entries = navidrome.playlist_entries(id)
        except navidrome.NavidromeError as exc:
            raise HTTPException(502, str(exc))
        for e in entries:
            s = library_index.find_song(LIBRARY_ROOT, e["artist"], e["title"])
            songs.append(s or {"artist": e["artist"], "album": e["album"], "title": e["title"], "path": None,
                               "track_number": None, "disc_number": None, "has_lyrics": False})
    return songs


@router.get("/api/library/find")
def find_in_library(q: str):
    """Library matches for the main search (songs, albums, artists), from
    file and folder names -- instant, see library_index."""
    return library_index.search(LIBRARY_ROOT, q.strip())


_cover_cache: dict = {}


@router.get("/api/library/cover")
def library_cover(artist: str, album: str):
    """An album folder's cover (embedded in its songs), for search rows."""
    key = (artist, album)
    if key not in _cover_cache:
        d = _safe_library_path(artist, album)
        data = None
        try:
            for name in sorted(os.listdir(d)):
                if tagger.is_taggable(name):
                    data = (tagger.read_fields(os.path.join(d, name)) or {}).get("artwork")
                    if data:
                        break
        except OSError:
            pass
        if len(_cover_cache) > 300:
            _cover_cache.clear()
        _cover_cache[key] = data
    data = _cover_cache[key]
    if not data:
        raise HTTPException(404, "No cover.")
    mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return Response(content=data, media_type=mime, headers={"Cache-Control": "max-age=86400"})


@router.get("/api/library/file")
def library_file(path: str):
    """A library song itself, so search can play it (instantly -- no
    YouTube delay). Range requests work, so seeking does too."""
    full = _safe_library_path(*path.split("/"))
    if not os.path.isfile(full) or not full.lower().endswith(tagger.AUDIO_EXTENSIONS):
        raise HTTPException(404, "Song not found.")
    low = full.lower()
    mime = "audio/mp4" if low.endswith((".m4a", ".mp4", ".aac")) else "audio/ogg" if low.endswith((".opus", ".ogg")) else None
    return FileResponse(full, media_type=mime)


# Same reason as "search" above -- these must be declared before
# "/api/library/{artist}" and "/api/library/{artist}/{album}", or "scan"
# gets swallowed as an artist name (and a job id as an "album").
class ScanRepairRequest(BaseModel):
    paths: list[str] | None = None  # None = repair everything flagged


@router.post("/api/library/scan")
def start_library_scan():
    job = library_scan.start_scan(LIBRARY_ROOT)
    return job.to_dict()


@router.get("/api/library/scan/{job_id}")
def get_library_scan(job_id: str):
    job = library_scan.store.get(job_id)
    if not job:
        raise HTTPException(404, "Scan job not found.")
    return job.to_dict()


@router.post("/api/library/scan/{job_id}/repair")
def repair_library_scan(job_id: str, req: ScanRepairRequest):
    job = library_scan.start_repair(job_id, req.paths)
    if not job:
        raise HTTPException(404, "Scan job not found.")
    return job.to_dict()


@router.post("/api/library/fix-albums")
def fix_split_albums():
    """Makes the songs in every album folder agree on their album tags, so
    Navidrome shows each folder as one album, then asks Navidrome to rescan."""
    checked = fixed = files = 0
    for artist in _artist_dirs():
        artist_dir = os.path.join(LIBRARY_ROOT, artist)
        try:
            albums = [a for a in os.listdir(artist_dir) if os.path.isdir(os.path.join(artist_dir, a))]
        except OSError:
            continue
        for album in albums:
            checked += 1
            n = tagger.unify_album_folder(os.path.join(artist_dir, album))
            if n:
                fixed += 1
                files += n
    rescan = False
    if files and navidrome.configured():
        try:
            navidrome.start_scan()
            rescan = True
        except navidrome.NavidromeError:
            pass
    return {"albums_checked": checked, "albums_fixed": fixed, "files_updated": files, "navidrome_rescan": rescan}


@router.get("/api/library/{artist}")
def list_albums(artist: str):
    d = _safe_library_path(artist)
    if not os.path.isdir(d):
        raise HTTPException(404, "Artist not found.")
    return sorted(e for e in os.listdir(d) if os.path.isdir(os.path.join(d, e)))


@router.get("/api/library/{artist}/{album}")
def list_tracks(artist: str, album: str):
    d = _safe_library_path(artist, album)
    if not os.path.isdir(d):
        raise HTTPException(404, "Album not found.")
    return sorted(e for e in os.listdir(d) if e.lower().endswith(tagger.AUDIO_EXTENSIONS))


@router.delete("/api/library/{artist}")
def delete_artist(artist: str):
    d = _safe_library_path(artist)
    if not os.path.isdir(d):
        raise HTTPException(404, "Artist not found.")
    shutil.rmtree(d)
    return {"ok": True}


@router.delete("/api/library/{artist}/{album}")
def delete_album(artist: str, album: str):
    d = _safe_library_path(artist, album)
    if not os.path.isdir(d):
        raise HTTPException(404, "Album not found.")
    shutil.rmtree(d)
    _prune_empty_dirs(os.path.dirname(d))
    return {"ok": True}


@router.delete("/api/library/{artist}/{album}/{track}")
def delete_track(artist: str, album: str, track: str):
    path = _safe_library_path(artist, album, track)
    if not os.path.isfile(path):
        raise HTTPException(404, "Track not found.")
    os.remove(path)
    lrc_path = os.path.splitext(path)[0] + ".lrc"
    if os.path.exists(lrc_path):
        os.remove(lrc_path)
    _prune_empty_dirs(os.path.dirname(path))
    return {"ok": True}


class MergeRequest(BaseModel):
    path_a: list[str]  # [artist] or [artist, album] -- the keeper: wins every duplicate,
    # and its album/artist/year/genre/artwork become what every merged-in track is retagged to
    path_b: list[str]  # same shape as path_a -- the folder merged away
    dry_run: bool = True


@router.post("/api/library/merge")
def merge_library_folders(req: MergeRequest):
    if len(req.path_a) not in (1, 2) or len(req.path_a) != len(req.path_b):
        raise HTTPException(400, "Both folders must be the same kind (two artists, or two albums under the same artist).")

    dir_a = _safe_library_path(*req.path_a)
    dir_b = _safe_library_path(*req.path_b)

    if os.path.normpath(dir_a) == os.path.normpath(dir_b):
        raise HTTPException(400, "Can't merge a folder into itself.")
    if not os.path.isdir(dir_a) or not os.path.isdir(dir_b):
        raise HTTPException(404, "Both folders must exist.")

    result = library_merge.plan_merge(dir_a, dir_b, dry_run=req.dry_run)
    if not req.dry_run:
        album_dirs = [dir_a] if len(req.path_a) == 2 else [
            os.path.join(dir_a, d) for d in os.listdir(dir_a) if os.path.isdir(os.path.join(dir_a, d))
        ]
        for d in album_dirs:
            tagger.unify_album_folder(d)

    def relative(p):
        return os.path.relpath(p, LIBRARY_ROOT).replace(os.sep, "/")

    return {
        "moved": [[relative(a), relative(b)] for a, b in result["moved"]],
        "kept_over_duplicate": [[relative(a), relative(b)] for a, b in result["kept_over_duplicate"]],
        "ambiguous": [relative(p) for p in result["ambiguous"]],
        "retagged": [
            {
                "from": relative(os.path.join(dir_a, r["from"])),
                "to": relative(os.path.join(dir_a, r["to"])),
                "track_number": r["track_number"],
            }
            for r in result["retagged"]
        ],
    }


# iOS also probes these fixed root paths, independent of the <link> tags.
@router.get("/apple-touch-icon.png")
@router.get("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    return FileResponse(os.path.join(STATIC_DIR, "apple-touch-icon.png"))


@router.get("/favicon.ico")
def favicon():
    return FileResponse(os.path.join(STATIC_DIR, "favicon-32.png"), media_type="image/png")


@router.get("/")
def index():
    # The player app. The original TubeRipper screens stay at /classic.
    with open(os.path.join(STATIC_DIR, "app.html"), encoding="utf-8") as f:
        page = f.read().replace("?v=AUTO", "?v=" + meta.asset_version())
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


@router.get("/classic")
def classic():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


app.include_router(auth.router)
app.include_router(auth.router, prefix="/tuberipper")
app.include_router(discover.router)
app.include_router(discover.router, prefix="/tuberipper")
app.include_router(recommend.router)
app.include_router(recommend.router, prefix="/tuberipper")
app.include_router(radio.router)
app.include_router(radio.router, prefix="/tuberipper")
app.include_router(mixes.router)
app.include_router(mixes.router, prefix="/tuberipper")
app.include_router(stats.router)
app.include_router(stats.router, prefix="/tuberipper")
app.include_router(libhealth.router)
app.include_router(libhealth.router, prefix="/tuberipper")
app.include_router(podcasts.router)
app.include_router(podcasts.router, prefix="/tuberipper")
app.include_router(library_delete.router)
app.include_router(library_delete.router, prefix="/tuberipper")
app.include_router(audio_analysis.router)
app.include_router(audio_analysis.router, prefix="/tuberipper")
app.include_router(secrets_setup.router)
app.include_router(secrets_setup.router, prefix="/tuberipper")
app.include_router(audiobookshelf.router)
app.include_router(audiobookshelf.router, prefix="/tuberipper")
app.include_router(meta.router)
app.include_router(meta.router, prefix="/tuberipper")
app.include_router(player_api.router)
app.include_router(player_api.router, prefix="/tuberipper")
app.include_router(router)
app.include_router(router, prefix="/tuberipper")
class FreshStaticFiles(StaticFiles):
    """Static files. A versioned address (?v=..., the script and stylesheet; the page gets a new v whenever
    they change) is kept by the phone for good; everything else is re-checked (cheap 304s)."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        versioned = b"v=" in scope.get("query_string", b"") and resp.status_code == 200
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "no-cache"
        return resp


app.mount("/static", FreshStaticFiles(directory=STATIC_DIR), name="static")

# The same app behind a login, for the internet-facing port (see auth.py and run.py).
public_app = auth.PublicGate(app)
