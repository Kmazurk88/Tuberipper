"""Library-wide scan for tracks missing metadata or lyrics, and a repair
pass that fills them in via the same lookup pipeline used for fresh
downloads. Only ever triggered manually from the UI -- there is no
scheduled/automatic sweep.

Metadata repair covers .mp3 and .m4a (tagger.apply_tags, same as a fresh
download) -- other formats (flac/ogg/etc) get flagged but need a
format-specific tag writer this app doesn't have yet. Lyrics repair works
for every format, since a .lrc sidecar file doesn't depend on the audio
file's own tag format.
"""

import os
import re
import threading
import uuid

from mutagen import File as MutagenFile

from . import lyrics, metadata, tagger
from .tagger import AUDIO_EXTENSIONS


def _read_easy_tags(path: str) -> dict:
    """Best-effort tag read across formats (mp3/m4a/flac/etc) via mutagen's
    unified 'easy' interface. Returns {} if the file can't be read."""
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return {}
    if audio is None or audio.tags is None:
        return {}

    def first(key):
        values = audio.tags.get(key)
        return values[0] if values else ""

    return {
        "title": first("title"),
        "artist": first("artist"),
        "album": first("album"),
    }


def _guess_from_path(library_root: str, filepath: str) -> dict:
    """Derives an artist/album guess from the Artist/Album/Track folder
    structure, and a title guess from the filename (stripping a leading
    "<track> - " prefix, this app's own naming convention)."""
    rel = os.path.relpath(filepath, library_root)
    parts = rel.split(os.sep)
    artist = parts[0] if len(parts) >= 3 else ""
    album = parts[1] if len(parts) >= 3 else ""
    stem = os.path.splitext(os.path.basename(filepath))[0]
    stem = re.sub(r"^\d+\s*-\s*", "", stem).strip()
    return {"artist": artist, "album": album, "title": stem or os.path.basename(filepath)}


def scan(library_root: str) -> list[dict]:
    """Walks the whole library and returns one entry per audio file:
    {path (relative to library_root, forward slashes), artist, title, album,
    format, missing_metadata, missing_lyrics}. missing_metadata is true if
    title, artist, or album is blank; missing_lyrics is true if there's no
    sibling .lrc file. Read-only -- nothing is changed."""
    results = []
    for root, _dirs, files in os.walk(library_root):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue
            path = os.path.join(root, name)
            tags = _read_easy_tags(path)
            guess = _guess_from_path(library_root, path)
            artist = tags.get("artist") or guess["artist"]
            title = tags.get("title") or guess["title"]
            album = tags.get("album") or guess["album"]
            missing_metadata = not (tags.get("title") and tags.get("artist") and tags.get("album"))
            lrc_path = os.path.splitext(path)[0] + ".lrc"
            results.append({
                "path": os.path.relpath(path, library_root).replace(os.sep, "/"),
                "artist": artist,
                "title": title,
                "album": album,
                "format": ext.lstrip("."),
                "missing_metadata": missing_metadata,
                "missing_lyrics": not os.path.exists(lrc_path),
            })
    return results


def _repair_file(library_root: str, entry: dict) -> dict:
    """Attempts to fill in whatever `entry` is missing, in place. Mutates
    and returns `entry`."""
    path = os.path.join(library_root, entry["path"].replace("/", os.sep))
    artist, title, album = entry["artist"], entry["title"], entry["album"]

    if entry["missing_metadata"]:
        if not tagger.is_taggable(path):
            entry["metadata_repair"] = "unsupported_format"
        else:
            match = metadata.lookup(artist, title, album_hint=album)
            if match:
                match = metadata.fill_genre(match)
                tagger.apply_tags(path, match, metadata.artwork_bytes(match))
                artist, title, album = match["artist"], match["title"], match.get("album", "")
                entry["artist"], entry["title"], entry["album"] = artist, title, album
                entry["missing_metadata"] = False
            else:
                entry["metadata_repair"] = "no_match"

    if entry["missing_lyrics"]:
        duration = tagger.audio_length(path)
        # Existing file: only synced lyrics timed to this exact audio length.
        lrc_text, _kind = lyrics.fetch_lrc(artist, title, album, audio_duration=duration)
        if lrc_text:
            lrc_path = os.path.splitext(path)[0] + ".lrc"
            with open(lrc_path, "w", encoding="utf-8") as f:
                f.write(lrc_text)
            entry["missing_lyrics"] = False
        else:
            entry["lyrics_repair"] = "no_match"

    return entry


class ScanJob:
    def __init__(self, job_id: str, library_root: str):
        self.id = job_id
        self.library_root = library_root
        self.status = "scanning"  # scanning -> ready -> repairing -> done
        self.files = []  # from scan(); repair mutates entries in place
        self.progress = {"done": 0, "total": 0}
        self.lock = threading.Lock()

    def to_dict(self):
        with self.lock:
            return {
                "id": self.id,
                "status": self.status,
                "total_files": len(self.files),
                "missing_metadata_count": sum(1 for f in self.files if f["missing_metadata"]),
                "missing_lyrics_count": sum(1 for f in self.files if f["missing_lyrics"]),
                "flagged": [dict(f) for f in self.files if f["missing_metadata"] or f["missing_lyrics"]],
                "progress": dict(self.progress),
            }


class ScanStore:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def create(self, library_root: str) -> ScanJob:
        job_id = uuid.uuid4().hex[:12]
        job = ScanJob(job_id, library_root)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str):
        with self._lock:
            return self._jobs.get(job_id)


store = ScanStore()


def _run_scan(job_id: str):
    job = store.get(job_id)
    if not job:
        return
    files = scan(job.library_root)
    with job.lock:
        job.files = files
        job.status = "ready"


def start_scan(library_root: str) -> ScanJob:
    job = store.create(library_root)
    threading.Thread(target=_run_scan, args=(job.id,), daemon=True).start()
    return job


def _run_repair(job_id: str, paths: set[str]):
    job = store.get(job_id)
    if not job:
        return

    with job.lock:
        job.status = "repairing"
        targets = [f for f in job.files if f["path"] in paths]
        job.progress = {"done": 0, "total": len(targets)}

    for entry in targets:
        _repair_file(job.library_root, entry)
        with job.lock:
            job.progress["done"] += 1

    with job.lock:
        job.status = "done"


def start_repair(job_id: str, paths: list[str] | None = None):
    """Repairs every flagged file if `paths` is None, otherwise just the
    given subset. Returns the job, or None if job_id is unknown."""
    job = store.get(job_id)
    if not job:
        return None

    if paths is None:
        with job.lock:
            target_paths = {f["path"] for f in job.files if f["missing_metadata"] or f["missing_lyrics"]}
    else:
        target_paths = set(paths)

    threading.Thread(target=_run_repair, args=(job_id, target_paths), daemon=True).start()
    return job
