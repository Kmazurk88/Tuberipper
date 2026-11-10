"""Navidrome (Subsonic API) client: list playlists, create one, or add songs
to an existing one. Configured with three env vars on the container:

    NAVIDROME_URL       e.g. http://<server-ip>:4533
    NAVIDROME_USER
    NAVIDROME_PASSWORD

Navidrome only knows a song once it has scanned the file, so after a
download the caller runs wait_for_scan() first, then find_song_ids() to map
our tag metadata to Navidrome song ids (matched on title/album/artist --
Navidrome doesn't report real file paths to Subsonic clients by default)."""

import hashlib
import os
import secrets
import time

import requests

from . import tagger
from .metadata import _normalize_track_title

API_VERSION = "1.16.1"
CLIENT = "tuberipper"


class NavidromeError(Exception):
    pass


def _config():
    return (
        os.environ.get("NAVIDROME_URL", "").strip().rstrip("/"),
        os.environ.get("NAVIDROME_USER", "").strip(),
        os.environ.get("NAVIDROME_PASSWORD", "").strip(),
    )


def configured() -> bool:
    return all(_config())


def _call(endpoint: str, params: list | None = None, timeout: int = 15) -> dict:
    """params is a list of (key, value) pairs, since some endpoints take a
    key repeated (songId=1&songId=2)."""
    url, user, password = _config()
    if not (url and user and password):
        raise NavidromeError("Navidrome isn't set up (NAVIDROME_URL / NAVIDROME_USER / NAVIDROME_PASSWORD).")
    salt = secrets.token_hex(6)
    auth = [
        ("u", user),
        ("t", hashlib.md5((password + salt).encode()).hexdigest()),
        ("s", salt),
        ("v", API_VERSION),
        ("c", CLIENT),
        ("f", "json"),
    ]
    try:
        resp = requests.get(f"{url}/rest/{endpoint}", params=auth + (params or []), timeout=timeout)
        resp.raise_for_status()
        body = resp.json().get("subsonic-response", {})
    except (requests.RequestException, ValueError) as exc:
        raise NavidromeError(f"Couldn't reach Navidrome at {url}: {exc}")
    if body.get("status") != "ok":
        raise NavidromeError(body.get("error", {}).get("message") or "Navidrome returned an error.")
    return body


def list_playlists() -> list:
    body = _call("getPlaylists")
    items = body.get("playlists", {}).get("playlist", []) or []
    return sorted(
        ({"id": p["id"], "name": p.get("name", ""), "song_count": p.get("songCount", 0)} for p in items),
        key=lambda p: p["name"].casefold(),
    )


def playlist_song_ids(playlist_id: str) -> list:
    body = _call("getPlaylist", [("id", playlist_id)])
    return [e["id"] for e in body.get("playlist", {}).get("entry", []) or []]


def playlist_entries(playlist_id: str) -> list:
    """[{title, artist, album}] in playlist order."""
    body = _call("getPlaylist", [("id", playlist_id)])
    return [{"title": e.get("title", ""), "artist": e.get("artist", ""), "album": e.get("album", "")}
            for e in body.get("playlist", {}).get("entry", []) or []]


def create_playlist(name: str, song_ids: list) -> None:
    _call("createPlaylist", [("name", name)] + [("songId", sid) for sid in song_ids])


def add_to_playlist(playlist_id: str, song_ids: list) -> int:
    """Appends songs not already in the playlist. Returns how many were added."""
    existing = set(playlist_song_ids(playlist_id))
    new = [sid for sid in song_ids if sid not in existing]
    if new:
        _call("updatePlaylist", [("playlistId", playlist_id)] + [("songIdToAdd", sid) for sid in new])
    return len(new)


def start_scan() -> None:
    """Asks Navidrome to rescan, without waiting for it."""
    _call("startScan")


def wait_for_scan(max_wait: int = 600) -> None:
    """Asks Navidrome to scan for new files and waits until it's done."""
    _call("startScan")
    deadline = time.monotonic() + max_wait
    time.sleep(2)
    while time.monotonic() < deadline:
        status = _call("getScanStatus").get("scanStatus", {})
        if not status.get("scanning"):
            return
        time.sleep(3)
    raise NavidromeError("Navidrome is still scanning after 10 minutes; try adding the playlist again later.")


def _matches(song: dict, meta: dict) -> int:
    """0 = not this song; higher = better match."""
    fold = tagger._fold
    if fold(_normalize_track_title(song.get("title", ""))) != fold(_normalize_track_title(meta.get("title", ""))):
        return 0
    score = 1
    if meta.get("album") and fold(song.get("album", "")) == fold(meta["album"]):
        score += 2
    artist = fold(meta.get("artist", ""))
    if artist and (artist in fold(song.get("artist", "")) or fold(song.get("artist", "")) in artist):
        score += 1
    if meta.get("track_number") and song.get("track") == meta["track_number"]:
        score += 1
    return score


def find_song_id(meta: dict) -> str | None:
    query = _normalize_track_title(meta.get("title", ""))
    if not query:
        return None
    body = _call("search3", [("query", query), ("songCount", "50"), ("artistCount", "0"), ("albumCount", "0")])
    songs = body.get("searchResult3", {}).get("song", []) or []
    best = max(songs, key=lambda s: _matches(s, meta), default=None)
    # Require at least title + album, or title + artist -- a bare title
    # match could be a different song with the same name.
    return best["id"] if best and _matches(best, meta) >= 2 else None


def find_song_ids(metas: list, retries: int = 2) -> tuple:
    """Returns (ids in order, number not found). Retries the misses a couple
    of times, since a just-finished scan can lag a little behind search."""
    ids = [None] * len(metas)
    for attempt in range(retries + 1):
        for i, meta in enumerate(metas):
            if ids[i] is None:
                ids[i] = find_song_id(meta)
        if all(ids) or attempt == retries:
            break
        time.sleep(5)
    found = [i for i in ids if i]
    return found, len(ids) - len(found)
