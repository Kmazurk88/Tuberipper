"""yt-dlp wrappers for listing playlist entries and extracting audio."""

import os
import re
import subprocess
import traceback
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from urllib.parse import urlparse, parse_qs, quote


def _strip_radio_mix(url: str) -> str:
    """YouTube appends list=RD...&start_radio=1 to links copied while a
    Radio/autoplay mix is active. That's not a real playlist the user chose,
    so treat the URL as a single video by dropping those params."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    list_id = query.get("list", [""])[0]
    if list_id.startswith("RD"):
        video_id = query.get("v", [""])[0]
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    return url


def get_playlist(url: str) -> dict:
    """Lists a URL's videos without downloading anything. Returns
    {title, is_playlist, videos: [{id, title, url, duration}]} -- title is the
    playlist's own name for a playlist, the video's title otherwise.
    duration (seconds, may be None) is compared against the song's official
    length to tell a music video from the album audio (see sourcing.py)."""
    url = _strip_radio_mix(url)
    ydl_opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    is_playlist = bool(info.get("entries"))
    entries = info["entries"] if is_playlist else [info]

    videos = []
    for entry in entries:
        if entry is None:
            continue
        video_id = entry.get("id")
        webpage_url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
        videos.append({
            "id": video_id,
            "title": entry.get("title") or "Unknown Title",
            "url": webpage_url,
            "duration": entry.get("duration"),
        })
    return {"title": info.get("title") or "", "is_playlist": is_playlist, "videos": videos}


def get_video_list(url: str):
    """Returns a list of dicts: {id, title, url} without downloading anything."""
    return get_playlist(url)["videos"]


def _thumb(entry: dict) -> str:
    thumbs = entry.get("thumbnails") or []
    return thumbs[-1].get("url", "") if thumbs else ""


# YouTube search-result type filters (the "sp" URL parameter).
FILTER_VIDEOS = "EgIQAQ%253D%253D"
FILTER_PLAYLISTS = "EgIQAw%253D%253D"

# Playlist titles/channels that mark a full album rather than someone's mix.
ALBUM_HINT_RE = re.compile(r"\b(full\s+album|album|deluxe|\bep\b)\b", re.IGNORECASE)


def _youtube_search(query: str, sp: str, limit: int) -> list:
    ydl_opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "playlist_items": f"1-{limit}",
    }
    url = f"https://www.youtube.com/results?search_query={quote(query)}" + (f"&sp={sp}" if sp else "")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return [e for e in (info.get("entries") or []) if e]


def _search_songs(query: str, max_results: int) -> list:
    results = []
    for entry in _youtube_search(query, FILTER_VIDEOS, max_results * 2):
        url = entry.get("url", "")
        if "/watch?v=" not in url or not entry.get("id") or len(results) >= max_results:
            continue
        results.append({
            "id": entry["id"],
            "title": entry.get("title") or "Unknown",
            "url": url,
            "kind": "video",
            "duration": entry.get("duration"),
            "track_count": 1,
            "channel": entry.get("channel") or entry.get("uploader") or "",
            "thumbnail": f"https://i.ytimg.com/vi/{entry['id']}/mqdefault.jpg",
        })

    # YouTube's regular search returns nothing to logged-out clients for some
    # explicit-sounding queries (e.g. "Sexy Bitch" alone -> 0 results, while
    # "sexy bitch david guetta" works). YouTube Music doesn't filter those, so
    # fall back to its song/video results when the main search comes up short.
    if len(results) < 3:
        seen = {r["id"] for r in results}
        for r in _search_music(query, max_results):
            if r["id"] not in seen and len(results) < max_results:
                results.append(r)
                seen.add(r["id"])
    return results


def _search_playlists(query: str, max_results: int) -> list:
    results = []
    for entry in _youtube_search(query, FILTER_PLAYLISTS, max_results * 2):
        url = entry.get("url", "")
        list_id = entry.get("id", "") or ""
        # Skip auto-generated Mix/Radio lists -- not a real playlist anyone made.
        if "/playlist?list=" not in url or list_id.startswith("RD") or len(results) >= max_results:
            continue
        title = entry.get("title") or "Unknown"
        channel = entry.get("channel") or entry.get("uploader") or ""
        results.append({
            "id": list_id,
            "title": title,
            "url": url,
            "kind": "playlist",
            "is_album": list_id.startswith("OLAK5uy_") or bool(ALBUM_HINT_RE.search(title)),
            "duration": None,
            "track_count": entry.get("playlist_count"),
            "channel": channel,
            "thumbnail": _thumb(entry),
        })

    # Each playlist's song count needs its own listing request; do them in
    # parallel instead of one after another.
    need_count = [r for r in results if r["track_count"] is None]
    if need_count:
        def count(r):
            try:
                return len(get_video_list(r["url"]))
            except Exception:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            for r, n in zip(need_count, pool.map(count, need_count)):
                r["track_count"] = n
    # Empty/broken lists aren't worth showing.
    return [r for r in results if r["track_count"] != 0]


def _search_unfiltered(query: str, max_songs: int, max_playlists: int) -> dict:
    """The plain YouTube results page (no type filter), split into songs and
    playlists. Used when the filtered searches come back empty -- some
    networks get nothing back for the filtered URLs while this still works."""
    songs, playlists = [], []
    for entry in _youtube_search(query, "", (max_songs + max_playlists) * 3):
        url = entry.get("url", "")
        if "/watch?v=" in url and entry.get("id") and len(songs) < max_songs:
            songs.append({
                "id": entry["id"], "title": entry.get("title") or "Unknown", "url": url,
                "kind": "video", "duration": entry.get("duration"), "track_count": 1,
                "channel": entry.get("channel") or entry.get("uploader") or "",
                "thumbnail": f"https://i.ytimg.com/vi/{entry['id']}/mqdefault.jpg",
            })
        elif "/playlist?list=" in url and not (entry.get("id") or "").startswith("RD") and len(playlists) < max_playlists:
            title = entry.get("title") or "Unknown"
            playlists.append({
                "id": entry.get("id"), "title": title, "url": url, "kind": "playlist",
                "is_album": (entry.get("id") or "").startswith("OLAK5uy_") or bool(ALBUM_HINT_RE.search(title)),
                "duration": None, "track_count": entry.get("playlist_count"),
                "channel": entry.get("channel") or entry.get("uploader") or "", "thumbnail": _thumb(entry),
            })
    return {"songs": songs, "playlists": playlists}


def search(query: str, max_songs: int = 15, max_playlists: int = 8, trace: list | None = None) -> dict:
    """Searches YouTube for songs (videos) and for albums/playlists, in
    parallel. Returns {"songs": [...], "playlists": [...]}, each entry
    {id, title, url, kind, duration, track_count, channel, thumbnail}
    (playlists also carry is_album). If one half fails, the other is still
    returned; if both come back empty, the unfiltered results page is tried.
    `trace`, if given, collects a step-by-step log (for /api/debug/search)."""
    log = trace if trace is not None else []
    errors = []

    def step(name, fn, *args):
        try:
            result = fn(*args)
            n = len(result) if isinstance(result, list) else {k: len(v) for k, v in result.items()}
            log.append(f"{name}: {n} result(s)")
            return result
        except Exception as exc:
            traceback.print_exc()
            log.append(f"{name}: ERROR {type(exc).__name__}: {str(exc)[:300]}")
            errors.append(exc)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        songs_f = pool.submit(step, "songs (video filter)", _search_songs, query, max_songs)
        lists_f = pool.submit(step, "playlists (playlist filter)", _search_playlists, query, max_playlists)
        songs, playlists = songs_f.result() or [], lists_f.result() or []

    if not songs and not playlists:
        fallback = step("fallback (unfiltered results page)", _search_unfiltered, query, max_songs, max_playlists)
        if fallback:
            songs, playlists = fallback["songs"], fallback["playlists"]

    if not songs and not playlists and errors:
        raise errors[0]
    return {"songs": songs, "playlists": playlists}


def _search_music(query: str, max_results: int) -> list:
    ydl_opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "playlist_items": f"1-{max_results * 2}",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://music.youtube.com/search?q={quote(query)}", download=False)
    except Exception:
        return []

    results = []
    for entry in info.get("entries", []) or []:
        # Artist/album/playlist rows are /browse/ links; only songs and videos
        # are watch links.
        if not entry or "/watch?v=" not in (entry.get("url") or "") or not entry.get("id"):
            continue
        video_id = entry["id"]
        results.append({
            "id": video_id,
            "title": entry.get("title") or "Unknown",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "kind": "video",
            "duration": entry.get("duration"),
            "track_count": 1,
            "channel": entry.get("channel") or entry.get("uploader") or "YouTube Music",
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        })
    return results


def download_audio(video: dict, out_dir: str) -> str:
    """Downloads the audio of a single video and returns its path: .opus (YouTube's Opus stream, the best
    sounding one it offers) or else .m4a (its AAC stream). The stream is kept as it is, never re-encoded;
    only something that is neither (rare) is converted to AAC."""
    os.makedirs(out_dir, exist_ok=True)
    out_template = os.path.join(out_dir, f"{video['id']}.%(ext)s")

    ydl_opts = {
        "format": "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": out_template,
        # "best" = keep the codec, only move it into its own kind of file (webm -> .opus)
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}],
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video["url"]])

    base = os.path.join(out_dir, video["id"])
    for ext in (".opus", ".m4a"):
        if os.path.exists(base + ext):
            return base + ext
    others = [n for n in os.listdir(out_dir) if n.startswith(video["id"] + ".") and not n.endswith(".part")]
    if not others:
        raise FileNotFoundError(f"yt-dlp left no audio file for {video['id']}")
    src = os.path.join(out_dir, others[0])
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-c:a", "aac", "-b:a", "192k", base + ".m4a"],
                   check=True, capture_output=True, timeout=600)
    os.remove(src)
    return base + ".m4a"


_SILENCE_RE = re.compile(r"silence_start:\s*([0-9.]+)")


def trim_to_song(path: str, official: float, actual: float) -> float | None:
    """YouTube Music's album uploads of gapless albums can run on into the
    next song (Daft Punk "Give Life Back to Music" ends at 4:34, then the
    upload carries 4 s of silence and the start of "The Game of Love").
    When the audio is clearly longer than the official length, cut it at
    the silence where the song really ends -- stream copy, no re-encode.
    Only cuts when such a silence exists within a few seconds of the
    official length; otherwise the file is left alone. Returns the new
    length, or None if nothing was cut."""
    if not official or not actual or actual - official <= 3:
        return None
    start = max(0.0, official - 4)
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.2f}", "-t", "10", "-i", path,
             "-af", "silencedetect=noise=-50dB:d=0.5", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    starts = [start + float(m.group(1)) for m in _SILENCE_RE.finditer(proc.stderr)]
    ends = [s for s in starts if official - 4 <= s <= official + 4]
    if not ends:
        return None
    # The silence nearest the official end -- not a pause just before it.
    cut = min(ends, key=lambda s: abs(s - official)) + 0.5
    if actual - cut < 1:
        return None
    tmp = path + ".trim" + os.path.splitext(path)[1]
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-t", f"{cut:.3f}", "-c", "copy", "-map_metadata", "0", tmp],
                       check=True, capture_output=True, timeout=120)
        os.replace(tmp, path)
    except (OSError, subprocess.SubprocessError):
        if os.path.exists(tmp):
            os.remove(tmp)
        return None
    return cut


def audio_stream(video_id: str) -> dict:
    """A directly playable audio stream URL for previews. Prefers AAC (m4a):
    iPhone Safari can't play YouTube's Opus/WebM."""
    ydl_opts = {"format": "bestaudio[ext=m4a]/bestaudio", "quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    ext = info.get("ext") or "m4a"
    return {
        "url": info["url"],
        "mime": "audio/mp4" if ext in ("m4a", "mp4") else f"audio/{ext}",
        "user_agent": (info.get("http_headers") or {}).get("User-Agent"),
        "duration": info.get("duration"),
        # The default (TV) client's URLs answer 403 until this unix time
        # (~15 s after extraction); yt-dlp itself sleeps until then.
        "available_at": info.get("available_at"),
    }
