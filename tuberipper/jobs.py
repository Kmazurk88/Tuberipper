"""In-memory job store for the web UI. One job = one submitted URL
(single video or playlist), holding a list of tracks moving through
the review -> download -> tag -> lyrics pipeline."""

import threading
import time
import uuid

JOB_TTL_SECONDS = 24 * 3600


class Job:
    def __init__(self, job_id: str, url: str, out_dir: str):
        self.id = job_id
        self.url = url
        self.out_dir = out_dir
        self.status = "collecting"  # collecting -> ready -> processing -> awaiting_confirmation -> processing -> done/cancelled/error
        self.error = None
        self.tracks = []  # list of dicts, see build_track()
        self.album_candidates = []  # see metadata.album_candidates()
        self.guess_album_id = None  # candidate matching the automatic guess
        self.selected_album_id = None  # candidate the user picked
        self.album_hint = None  # album the user picked from the catalog search, biases the lookup
        self.expected_album = ""  # album shown on the catalog song the user tapped (auto-picks it if the lookup agrees)
        self.is_playlist = False
        self.playlist_title = ""
        self.playlist_mode = "none"  # none | new | existing
        self.playlist_name = ""
        self.playlist_id = None  # Navidrome playlist id, for "existing"
        self.playlist_path = None  # .m3u8 fallback, once written
        self.playlist_status = None  # progress / result text for the UI
        self.playlist_error = None
        self.phase = ""  # what the lookup is doing right now when per-song statuses don't say ("Picking albums 3 of 25")
        # Data fetched during the lookup so the download step doesn't ask
        # the same services again: {track index: {"key": ..., "lyrics": (text, kind), "audio": seconds}}.
        self.prefetch = {}
        # MusicBrainz confirming/correcting the albums Deezer answered with.
        # The job is already awaiting_confirmation meanwhile (the user can
        # add the song right away); downloads wait for this before tagging.
        self.refining = False
        self.refined = threading.Event()
        self.refined.set()
        self.user_picked_album = False
        self.lookup_gen = 0
        self.auto_accept = False
        self.notify = True  # post the Discord summary when the job finishes (bulk imports turn it off)
        self.needs_check = 0  # quick add: songs left out because the match was unsure  # quick add from search: download as soon as Deezer answers  # bumped per lookup run; an older run's results are dropped  # the user chose an album: MusicBrainz mustn't override it
        self.lock = threading.Lock()
        self.created_at = time.time()

    def to_dict(self):
        with self.lock:
            return {
                "id": self.id,
                "url": self.url,
                "status": self.status,
                "error": self.error,
                "tracks": [dict(t) for t in self.tracks],
                "album_candidates": list(self.album_candidates),
                "guess_album_id": self.guess_album_id,
                "selected_album_id": self.selected_album_id,
                "album_hint": self.album_hint,
                "is_playlist": self.is_playlist,
                "playlist_title": self.playlist_title,
                "playlist_path": self.playlist_path,
                "playlist_status": self.playlist_status,
                "playlist_error": self.playlist_error,
                "playlist_mode": self.playlist_mode,
                "playlist_name": self.playlist_name,
                "playlist_id": self.playlist_id,
                "phase": self.phase,
                "refining": self.refining,
                "needs_check": self.needs_check,
            }


def build_track(index: int, id_: str, url: str, raw_title: str, artist: str, title: str, confidence: str,
                duration: float | None = None, target_duration: float | None = None, explicit: bool | None = None):
    """id_/url/duration describe the user's own video (the pasted/tapped one);
    both are empty for a catalog pick that has no YouTube upload yet.
    target_duration is the official length when already known (catalog
    pick); otherwise it comes from the metadata match."""
    return {
        "index": index,
        "id": id_,
        "url": url,
        "raw_title": raw_title,
        "artist": artist,
        "title": title,
        "confidence": confidence,
        "accepted": True,
        "status": "pending_review",  # pending_review -> looking_up -> finding_audio -> awaiting_confirmation -> downloading -> tagging -> fetching_lyrics -> done/skipped_exists/skipped_cancelled/failed
        "detail": "",
        "match": None,
        "on_album": None,  # after the user picks an album: whether this track is on it
        "duration": duration,
        "target_duration": target_duration,
        "explicit": explicit,
        "source": None,  # sourcing.find_audio_source() result: what will actually be downloaded
        "use_original": False,  # user override: download their own video instead of `source`
        "lyrics": None,  # "synced" | "plain" | "none", once fetched
        "catalog": None,  # the search row tapped: {"album", "artwork", "year"}
        "existing": None,  # library file this song already is, if any
        "lyrics_preview": None,  # {"kind", "lines": first few lines} once found
        "alternatives": None,  # other uploads, once the song page asked for them
        "quality": None,  # after the lookup: "good" | "weak" (matched a different-looking song) | "none" (no album found)
        "user_fixed": False,
        "source_tentative": False,  # audio picked before MusicBrainz confirmed the song's length  # looked up again by hand: MusicBrainz's refinement leaves it alone
    }


class JobStore:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def create(self, url: str, out_dir: str) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(job_id, url, out_dir)
        with self._lock:
            # Jobs live in memory only; drop finished ones after a day so a
            # long-running container doesn't accumulate them forever.
            cutoff = time.time() - JOB_TTL_SECONDS
            for old_id in [k for k, j in self._jobs.items()
                           if j.created_at < cutoff and j.status in ("done", "cancelled", "error", "ready", "awaiting_confirmation")]:
                del self._jobs[old_id]
            self._jobs[job_id] = job
        return job

    def active(self):
        """The most recent job that's still running in the background, if any."""
        with self._lock:
            running = [j for j in self._jobs.values() if j.status == "processing"]
        return max(running, key=lambda j: j.created_at, default=None)

    def get(self, job_id: str):
        with self._lock:
            return self._jobs.get(job_id)


store = JobStore()
