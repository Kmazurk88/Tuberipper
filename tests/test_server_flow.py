"""End-to-end job flows through the API with every network call faked:
playlist link -> album audio swapped in; catalog song; catalog album."""

import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

import threading

from tuberipper import catalog, config, deezer, downloader, lyrics, metadata, musicbrainz, navidrome, server, sourcing

SYNCED = "[00:01.00] hello"


def make_audio(path, seconds):
    """A silent file of the given length; .m4a (AAC) like real downloads, or .mp3."""
    codec = ["-c:a", "aac", "-b:a", "32k"] if path.endswith(".m4a") else ["-q:a", "9"]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", str(seconds), *codec, path], check=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    lib = tmp_path / "music"
    lib.mkdir()
    monkeypatch.setattr(config, "DEFAULT_OUTPUT_DIR", str(lib))
    monkeypatch.setattr(server, "LIBRARY_ROOT", str(lib))
    monkeypatch.setattr(navidrome, "configured", lambda: False)
    sourcing._cache.clear()

    # Official lengths: "Song A" 200s, "Song B" 180s.
    official = {"Song A": 200, "Song B": 180}

    def resolve_batch(tracks, album_hint=None, on_progress=None, on_step=None):
        out = []
        for i, t in enumerate(tracks):
            if on_progress:
                on_progress(i, False)
                on_progress(i, True)
            out.append({"artist": "Artist", "album_artist": "Artist", "title": t["title"], "album": "Album X",
                        "year": "2020", "genre": "", "artwork_url": "", "track_number": i + 1, "disc_number": 1,
                        "duration": official.get(t["title"]), "source": "musicbrainz"})
        return out

    monkeypatch.setattr(metadata, "resolve_batch", resolve_batch)
    monkeypatch.setattr(metadata, "album_candidates", lambda tracks, matches, album_hint=None: [
        {"collection_id": "mb:rg1:rel1", "album": "Album X", "artist": "Artist", "year": "2020",
         "artwork_url": "", "track_count": 10, "coverage": len(tracks), "original": len(tracks), "hint_match": False, "kind": "Album"},
    ])
    monkeypatch.setattr(metadata, "fill_genre", lambda m: {**m, "genre": m.get("genre") or "Pop"})
    # Deezer knows nothing here unless a test says so: MusicBrainz (faked
    # above) decides everything.
    monkeypatch.setattr(deezer, "match_song", lambda *a, **k: None)
    monkeypatch.setattr(deezer, "find_album", lambda *a, **k: None)
    monkeypatch.setattr(musicbrainz, "search_release_groups", lambda artist, album: [])
    monkeypatch.setattr(metadata, "artwork_bytes", lambda m: None)

    # YouTube Music: album-audio uploads for Song A only.
    def ytm_search(query, filter_):
        if filter_ == "songs" and "Song A" in query:
            return [{"videoId": "topicA", "title": "Song A", "artists": [{"name": "Artist"}], "duration_seconds": 201}]
        return []
    monkeypatch.setattr(sourcing, "_search", ytm_search)

    lengths = {"mvA": 262, "topicA": 201, "vidB": 181, "catB": 180}
    downloaded = []

    def download_audio(video, out_dir):
        downloaded.append(video["url"])
        vid = video["url"].rsplit("=", 1)[-1]
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{vid}.m4a")
        make_audio(path, lengths[vid])
        return path

    monkeypatch.setattr(downloader, "download_audio", download_audio)
    warmed = []
    monkeypatch.setattr(server, "warm_preview", lambda vid: warmed.append(vid))

    class R:
        ok = True
        def __init__(self, d): self.d = d
        def json(self): return self.d
        def raise_for_status(self): pass

    def lrclib(url, params=None, **kw):
        if url == lyrics.LRCLIB_GET_URL:
            return R({"duration": params["duration"], "syncedLyrics": SYNCED, "plainLyrics": "hello"})
        return R([])
    import types
    monkeypatch.setattr(lyrics, "requests", types.SimpleNamespace(get=lrclib, RequestException=Exception))

    return {"client": TestClient(server.app), "lib": lib, "downloaded": downloaded, "warmed": warmed}


def wait_for(client, job_id, statuses, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        # awaiting_confirmation counts once MusicBrainz's check is done too.
        if job["status"] in statuses and not (job["status"] == "awaiting_confirmation" and job.get("refining")):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job stuck in {job['status']}")


def test_playlist_link_swaps_music_video_for_album_audio(env, monkeypatch):
    c = env["client"]
    monkeypatch.setattr(downloader, "get_playlist", lambda url: {"title": "My mix", "is_playlist": True, "videos": [
        {"id": "mvA", "title": "Artist - Song A (Official Music Video)", "url": "https://www.youtube.com/watch?v=mvA", "duration": 262},
        {"id": "vidB", "title": "Artist - Song B", "url": "https://www.youtube.com/watch?v=vidB", "duration": 181},
    ]})
    job = c.post("/api/jobs", json={"url": "https://www.youtube.com/playlist?list=PLx"}).json()
    assert job["status"] == "ready"
    c.post(f"/api/jobs/{job['id']}/start")
    job = wait_for(c, job["id"], {"awaiting_confirmation", "error"})
    a, b = job["tracks"]
    assert a["source"]["kind"] == "album_audio" and a["source"]["video_id"] == "topicA"
    assert b["source"]["kind"] == "your_video" and b["source"]["ok"] is True

    r = c.post(f"/api/jobs/{job['id']}/confirm-match", json={"action": "accept"})
    assert r.status_code == 200
    job = wait_for(c, job["id"], {"done"})
    assert [t["status"] for t in job["tracks"]] == ["done", "done"]
    # Downloads run in parallel, so any order.
    assert sorted(env["downloaded"]) == ["https://www.youtube.com/watch?v=topicA", "https://www.youtube.com/watch?v=vidB"]
    assert [t["lyrics"] for t in job["tracks"]] == ["synced", "synced"]
    assert os.path.exists(env["lib"] / "Artist" / "Album X" / "1 - Song A.lrc")


def test_use_my_video_keeps_the_music_video_with_plain_lyrics(env, monkeypatch):
    c = env["client"]
    monkeypatch.setattr(downloader, "get_playlist", lambda url: {"title": "", "is_playlist": False, "videos": [
        {"id": "mvA", "title": "Artist - Song A (Official Music Video)", "url": "https://www.youtube.com/watch?v=mvA", "duration": 262},
    ]})
    job = c.post("/api/jobs", json={"url": "https://www.youtube.com/watch?v=mvA"}).json()
    c.post(f"/api/jobs/{job['id']}/start")
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    assert c.patch(f"/api/jobs/{job['id']}/tracks/0", json={"use_original": True}).status_code == 200
    # Picking the album (what tapping a cover does) must keep the override.
    monkeypatch.setattr(metadata, "match_to_album", lambda tracks, matches, cid: (matches, [True] * len(matches)))
    assert c.post(f"/api/jobs/{job['id']}/pick-album", json={"collection_id": "mb:rg1:rel1"}).status_code == 200
    c.post(f"/api/jobs/{job['id']}/confirm-match", json={"action": "accept"})
    job = wait_for(c, job["id"], {"done"})
    assert env["downloaded"] == ["https://www.youtube.com/watch?v=mvA"]
    assert job["tracks"][0]["lyrics"] == "plain"
    lrc = (env["lib"] / "Artist" / "Album X" / "1 - Song A.lrc").read_text()
    assert "[" not in lrc


def test_catalog_song_starts_itself_and_auto_picks_album(env):
    c = env["client"]
    job = c.post("/api/jobs/catalog", json={"song": {
        "title": "Song B", "artist": "Artist", "album": "Album X", "duration": 180, "video_id": "catB"}}).json()
    assert job["status"] == "processing"
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    t = job["tracks"][0]
    assert t["source"]["kind"] == "album_audio" and t["source"]["video_id"] == "catB"
    assert job["guess_album_id"] == "mb:rg1:rel1" and job["selected_album_id"] is None  # auto-selected, no picker


def test_catalog_song_without_upload_is_found_by_length(env):
    c = env["client"]
    job = c.post("/api/jobs/catalog", json={"song": {
        "title": "Song A", "artist": "Artist", "album": "Other Album", "duration": 200}}).json()
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    assert job["tracks"][0]["source"]["video_id"] == "topicA"
    assert job["selected_album_id"] is None  # tapped a different album than the lookup found: let the user pick


def test_catalog_song_with_no_audio_anywhere_fails_cleanly(env):
    c = env["client"]
    job = c.post("/api/jobs/catalog", json={"song": {"title": "Song B", "artist": "Artist", "duration": 180}}).json()
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    assert job["tracks"][0]["source"] is None
    c.post(f"/api/jobs/{job['id']}/confirm-match", json={"action": "accept"})
    job = wait_for(c, job["id"], {"done"})
    assert job["tracks"][0]["status"] == "failed" and "find" in job["tracks"][0]["detail"]


def test_catalog_album_stops_for_review(env, monkeypatch):
    c = env["client"]
    monkeypatch.setattr(catalog, "album_tracks", lambda b, i: {"title": "Album X", "artist": "Artist", "year": "2020", "artwork": "", "tracks": [
        {"title": "Song A", "artist": "Artist", "duration": 201, "video_id": "topicA", "explicit": False, "track_number": 1},
        {"title": "Song B", "artist": "Artist", "duration": 180, "video_id": "catB", "explicit": False, "track_number": 2},
    ]})
    job = c.post("/api/jobs/catalog", json={"album": {"title": "Album X", "artist": "Artist", "ytm_browse_id": "MPRE1"}}).json()
    assert job["status"] == "ready" and job["album_hint"] == "Album X" and len(job["tracks"]) == 2
    c.patch(f"/api/jobs/{job['id']}/tracks/1", json={"accepted": False})
    c.post(f"/api/jobs/{job['id']}/start")
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    assert job["guess_album_id"] == "mb:rg1:rel1"
    c.post(f"/api/jobs/{job['id']}/confirm-match", json={"action": "accept"})
    job = wait_for(c, job["id"], {"done"})
    assert [t["status"] for t in job["tracks"]] == ["done", "skipped_not_accepted"]


def test_album_page_untick_after_lookup_skips_download(env, monkeypatch):
    c = env["client"]
    monkeypatch.setattr(catalog, "album_tracks", lambda b, i: {"title": "Album X", "artist": "Artist", "year": "2020", "artwork": "http://art", "tracks": [
        {"title": "Song A", "artist": "Artist", "duration": 201, "video_id": "topicA", "explicit": False, "track_number": 1},
        {"title": "Song B", "artist": "Artist", "duration": 180, "video_id": "catB", "explicit": False, "track_number": 2},
    ]})
    job = c.post("/api/jobs/catalog", json={"album": {"title": "Album X", "artist": "Artist", "ytm_browse_id": "MPRE1"}}).json()
    assert job["url"].startswith("catalog-album:")
    assert job["tracks"][0]["catalog"] == {"album": "Album X", "artwork": "http://art", "year": "2020", "album_artist": "Artist"}
    assert env["warmed"] == ["topicA", "catB"]  # previews resolved ahead of a tap
    c.post(f"/api/jobs/{job['id']}/start", json={"playlist_mode": "none"})
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    c.patch(f"/api/jobs/{job['id']}/tracks/0", json={"accepted": False})  # unticked on the album page
    c.post(f"/api/jobs/{job['id']}/confirm-match", json={"action": "accept", "playlist_mode": "none"})
    job = wait_for(c, job["id"], {"done"})
    assert [t["status"] for t in job["tracks"]] == ["skipped_not_accepted", "done"]
    assert env["downloaded"] == ["https://www.youtube.com/watch?v=catB"]
    assert os.path.exists(env["lib"] / "Artist" / "Album X" / "2 - Song B.m4a")


def test_playlist_choice_sent_with_start_is_kept(env, monkeypatch):
    c = env["client"]
    monkeypatch.setattr(downloader, "get_playlist", lambda url: {"title": "My mix", "is_playlist": True, "videos": [
        {"id": "vidB", "title": "Artist - Song B", "url": "https://www.youtube.com/watch?v=vidB", "duration": 181},
        {"id": "mvA", "title": "Artist - Song A", "url": "https://www.youtube.com/watch?v=mvA", "duration": 262},
    ]})
    job = c.post("/api/jobs", json={"url": "https://www.youtube.com/playlist?list=PLx"}).json()
    r = c.post(f"/api/jobs/{job['id']}/start", json={"playlist_mode": "new", "playlist_name": "Road trip"})
    assert r.status_code == 200
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    assert job["playlist_mode"] == "new" and job["playlist_name"] == "Road trip"
    assert c.post(f"/api/jobs/{job['id']}/start", json={"playlist_mode": "existing"}).status_code in (400, 409)


def test_lookup_data_is_reused_by_the_download(env, monkeypatch):
    """Genre and lyrics are fetched during the lookup; the download step
    must not ask the services again."""
    c = env["client"]
    calls = {"genre": 0, "lyrics": 0}
    def genre(m):
        calls["genre"] += 1
        return {**m, "genre": "Pop"}
    real_fetch = lyrics.fetch_lrc
    def fetch(*a, **kw):
        calls["lyrics"] += 1
        return real_fetch(*a, **kw)
    monkeypatch.setattr(metadata, "fill_genre", genre)
    monkeypatch.setattr(lyrics, "fetch_lrc", fetch)
    monkeypatch.setattr(downloader, "get_playlist", lambda url: {"title": "", "is_playlist": False, "videos": [
        {"id": "vidB", "title": "Artist - Song B", "url": "https://www.youtube.com/watch?v=vidB", "duration": 181},
    ]})
    job = c.post("/api/jobs", json={"url": "https://www.youtube.com/watch?v=vidB"}).json()
    c.post(f"/api/jobs/{job['id']}/start")
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    assert calls == {"genre": 1, "lyrics": 1}
    assert job["tracks"][0]["match"]["genre"] == "Pop" and job["tracks"][0]["quality"] == "good"
    c.post(f"/api/jobs/{job['id']}/confirm-match", json={"action": "accept"})
    job = wait_for(c, job["id"], {"done"})
    assert job["tracks"][0]["status"] == "done" and job["tracks"][0]["lyrics"] == "synced"
    assert calls == {"genre": 1, "lyrics": 1}


def test_weak_match_can_be_fixed_and_looked_up_again(env, monkeypatch):
    c = env["client"]
    def resolve_batch(tracks, album_hint=None, on_progress=None, on_step=None):
        out = []
        for t in tracks:
            if t["title"] == "Sng B garbled":
                out.append({"artist": "Somebody Else", "title": "Totally Different", "album": "Wrong", "duration": 100,
                            "source": "itunes", "genre": "", "artwork_url": "", "track_number": 1, "disc_number": 1})
            else:
                out.append({"artist": "Artist", "album_artist": "Artist", "title": t["title"], "album": "Album X",
                            "year": "2020", "genre": "", "artwork_url": "", "track_number": 2, "disc_number": 1,
                            "duration": 180, "source": "musicbrainz"})
        return out
    monkeypatch.setattr(metadata, "resolve_batch", resolve_batch)
    monkeypatch.setattr(downloader, "get_playlist", lambda url: {"title": "", "is_playlist": False, "videos": [
        {"id": "vidB", "title": "Artist - Sng B garbled", "url": "https://www.youtube.com/watch?v=vidB", "duration": 181},
    ]})
    job = c.post("/api/jobs", json={"url": "https://www.youtube.com/watch?v=vidB"}).json()
    c.post(f"/api/jobs/{job['id']}/start")
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    assert job["tracks"][0]["quality"] == "weak"
    r = c.post(f"/api/jobs/{job['id']}/tracks/0/lookup", json={"artist": "Artist", "title": "Song B"})
    assert r.status_code == 200
    t = r.json()["tracks"][0]
    assert t["quality"] == "good" and t["match"]["album"] == "Album X" and t["title"] == "Song B"
    assert t["source"]["kind"] == "your_video" and t["source"]["ok"] is True


def test_song_page_alternatives_and_choice(env, monkeypatch):
    c = env["client"]
    def ytm_search(query, filter_):
        if filter_ == "songs":
            return [{"videoId": "topicA", "title": "Song A", "artists": [{"name": "Artist"}], "duration_seconds": 201}]
        return [{"videoId": "lyrA", "title": "Artist - Song A (Lyrics)", "artists": [{"name": "Lyrics Ch"}], "duration_seconds": 199},
                {"videoId": "mvA", "title": "Song A", "artists": [{"name": "Artist"}], "duration_seconds": 262},
                {"videoId": "liveA", "title": "Song A (Live)", "artists": [{"name": "Artist"}], "duration_seconds": 300}]
    monkeypatch.setattr(sourcing, "_search", ytm_search)
    job = c.post("/api/jobs/catalog", json={"song": {"title": "Song A", "artist": "Artist", "album": "Album X",
                                                    "duration": 200, "artwork": "http://x/a.jpg", "year": "2020"}}).json()
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    t = job["tracks"][0]
    assert t["catalog"]["artwork"] == "http://x/a.jpg"
    assert t["lyrics_preview"] == {"kind": "synced", "lines": ["hello"]}
    assert t["existing"] is None

    alts = c.get(f"/api/jobs/{job['id']}/tracks/0/sources").json()
    ids = [a["video_id"] for a in alts]
    assert ids[:2] == ["topicA", "lyrA"] and "mvA" in ids and "liveA" not in ids
    assert next(a for a in alts if a["video_id"] == "mvA")["ok"] is False

    job = c.post(f"/api/jobs/{job['id']}/tracks/0/source", json={"video_id": "mvA"}).json()
    assert job["tracks"][0]["source"]["video_id"] == "mvA"
    # Lyrics were re-checked for the longer audio: no synced lyrics for it.
    assert job["tracks"][0]["lyrics_preview"]["kind"] == "plain"
    assert c.post(f"/api/jobs/{job['id']}/tracks/0/source", json={"video_id": "nope"}).status_code == 400


def test_preview_proxies_range_requests(env, monkeypatch):
    c = env["client"]
    server._stream_cache.clear()
    monkeypatch.setattr(downloader, "audio_stream", lambda vid: {"url": "https://media/x", "mime": "audio/mp4", "user_agent": "UA"})
    seen = {}

    class Up:
        status_code = 206
        headers = {"Content-Length": "4", "Content-Range": "bytes 0-3/100"}
        def iter_content(self, n): yield b"abcd"
        def close(self): pass

    def get(url, headers=None, stream=False, timeout=None):
        seen.update(headers)
        return Up()
    monkeypatch.setattr(server.requests, "get", get)
    r = c.get("/api/preview/abcdefghijk", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content == b"abcd" and r.headers["content-range"] == "bytes 0-3/100"
    assert seen["Range"] == "bytes=0-3" and seen["User-Agent"] == "UA"
    assert c.get("/api/preview/bad!id").status_code in (400, 404)


def test_deezer_answers_first_and_musicbrainz_corrects_the_album(env, monkeypatch):
    """The song is ready to add as soon as Deezer answers (its compilation
    pick shown); MusicBrainz then moves it to the original album, and a
    download started meanwhile is tagged with MusicBrainz's answer."""
    c = env["client"]
    deezer_pick = {"artist": "Artist", "album_artist": "Various Artists", "title": "Song B", "album": "Hits 2020",
                   "year": "2021", "genre": "Pop", "artwork_url": "http://dz/cover.jpg", "track_number": 7, "disc_number": 1,
                   "duration": 180, "deezer_album_id": 9, "source": "deezer"}
    monkeypatch.setattr(deezer, "match_song", lambda *a, **k: dict(deezer_pick))
    gate = threading.Event()
    real_resolve = metadata.resolve_batch
    def slow_resolve(*a, **k):
        assert gate.wait(10)
        return real_resolve(*a, **k)
    monkeypatch.setattr(metadata, "resolve_batch", slow_resolve)
    monkeypatch.setattr(downloader, "get_playlist", lambda url: {"title": "", "is_playlist": False, "videos": [
        {"id": "vidB", "title": "Artist - Song B", "url": "https://www.youtube.com/watch?v=vidB", "duration": 181},
    ]})
    job = c.post("/api/jobs", json={"url": "https://www.youtube.com/watch?v=vidB"}).json()
    c.post(f"/api/jobs/{job['id']}/start")
    end = time.time() + 10
    while time.time() < end:
        job = c.get(f"/api/jobs/{job['id']}").json()
        if job["status"] == "awaiting_confirmation":
            break
        time.sleep(0.05)
    # Deezer's answer, ready to add while MusicBrainz is still checking.
    assert job["refining"] and job["tracks"][0]["match"]["album"] == "Hits 2020"
    assert job["tracks"][0]["source"] is not None
    c.post(f"/api/jobs/{job['id']}/confirm-match", json={"action": "accept", "playlist_mode": "none"})
    time.sleep(0.5)
    # A pasted video has no trusted length yet: which upload to fetch waits
    # for MusicBrainz (Deezer's search often lands on a 12" mix or live take).
    assert env["downloaded"] == [] and job["tracks"][0]["source_tentative"]
    assert c.get(f"/api/jobs/{job['id']}").json()["status"] == "processing"
    gate.set()  # MusicBrainz answers
    job = wait_for(c, job["id"], {"done"})
    t = job["tracks"][0]
    assert t["status"] == "done" and t["match"]["album"] == "Album X" and t["match"]["track_number"] == 1
    assert os.path.exists(env["lib"] / "Artist" / "Album X" / "1 - Song B.m4a")
    assert not os.path.exists(env["lib"] / "Various Artists")


def test_musicbrainz_does_not_override_a_hand_lookup(env, monkeypatch):
    c = env["client"]
    gate = threading.Event()
    real_resolve = metadata.resolve_batch
    calls = []
    def resolve(tracks, *a, **k):
        calls.append(tracks[0]["title"])
        if len(calls) == 1:
            assert gate.wait(10)
        return real_resolve(tracks, *a, **k)
    monkeypatch.setattr(metadata, "resolve_batch", resolve)
    monkeypatch.setattr(deezer, "match_song", lambda artist, title, *a, **k: {
        "artist": artist, "title": title, "album": "DZ " + title, "duration": 180, "source": "deezer"})
    monkeypatch.setattr(downloader, "get_playlist", lambda url: {"title": "", "is_playlist": False, "videos": [
        {"id": "vidB", "title": "Artist - Song B", "url": "https://www.youtube.com/watch?v=vidB", "duration": 181},
    ]})
    job = c.post("/api/jobs", json={"url": "https://www.youtube.com/watch?v=vidB"}).json()
    c.post(f"/api/jobs/{job['id']}/start")
    end = time.time() + 10
    while c.get(f"/api/jobs/{job['id']}").json()["status"] != "awaiting_confirmation" and time.time() < end:
        time.sleep(0.05)
    fixed = c.post(f"/api/jobs/{job['id']}/tracks/0/lookup", json={"artist": "Artist", "title": "Song A"}).json()
    assert fixed["tracks"][0]["match"]["title"] == "Song A"
    gate.set()
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    assert job["tracks"][0]["match"]["title"] == "Song A"  # the slow first lookup didn't overwrite it


def test_album_pick_uses_one_album_lookup_not_per_song_searches(env, monkeypatch):
    c = env["client"]
    monkeypatch.setattr(catalog, "album_tracks", lambda b, i: {"title": "Album X", "artist": "Artist", "year": "2020", "artwork": "", "tracks": [
        {"title": "Song A", "artist": "Artist", "duration": 201, "video_id": "topicA", "explicit": False, "track_number": 1},
        {"title": "Song B", "artist": "Artist", "duration": 180, "video_id": "catB", "explicit": False, "track_number": 2},
    ]})
    dz = {"id": 5, "title": "Album X (Deluxe Edition)", "artist": "Artist", "record_type": "album", "release_date": "2021-01-01",
          "genre": "Dance", "cover": "http://dz/x.jpg", "explicit": False, "tracks": [
              {"id": 51, "title": "Song A", "title_short": "Song A", "artist": "Artist", "duration": 201, "track_position": 1, "disk_number": 1, "explicit": False},
              {"id": 52, "title": "Song B", "title_short": "Song B", "artist": "Artist", "duration": 180, "track_position": 2, "disk_number": 1, "explicit": False}]}
    monkeypatch.setattr(deezer, "find_album", lambda artist, title: dz)
    monkeypatch.setattr(musicbrainz, "search_release_groups", lambda artist, album: [
        {"rg_id": "rg1", "rg_title": "Album X", "rg_primary": "Album", "rg_secondary": [], "release_artist": "Artist",
         "release_artist_id": "a1", "date": "2020-05-01", "release_id": "rel1", "track_count": None}])
    monkeypatch.setattr(musicbrainz, "release_tracklist", lambda rid: {
        "title": "Album X", "album_artist": "Artist", "album_artist_id": "a1", "rg_id": "rg1", "rg_primary": "Album",
        "rg_secondary": [], "date": "2020-05-01", "tracks": [
            {"recording_id": "r1", "title": "Song A", "artist": "Artist", "artist_id": "a1", "track_number": 1, "disc_number": 1, "length": 201000},
            {"recording_id": "r2", "title": "Song B", "artist": "Artist", "artist_id": "a1", "track_number": 2, "disc_number": 1, "length": 180000}]})
    monkeypatch.setattr(metadata, "resolve_batch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no per-song lookups")))
    job = c.post("/api/jobs/catalog", json={"album": {"title": "Album X", "artist": "Artist", "ytm_browse_id": "MPRE1"}}).json()
    c.post(f"/api/jobs/{job['id']}/start", json={"playlist_mode": "none"})
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    m = job["tracks"][1]["match"]
    assert (m["album"], m["year"], m["track_number"], m["mb_recording_id"], m["genre"], m["artwork_url"]) == (
        "Album X", "2020", 2, "r2", "Dance", "http://dz/x.jpg")
    assert job["guess_album_id"] == "mb:rg1:rel1" and job["album_candidates"][0]["album"] == "Album X"


def test_catalog_song_downloads_while_musicbrainz_is_still_checking(env, monkeypatch):
    """A song-page pick knows its official length (from the catalog), so
    Add to Library starts downloading at once; tagging waits for MusicBrainz."""
    c = env["client"]
    monkeypatch.setattr(deezer, "match_song", lambda *a, **k: {
        "artist": "Artist", "title": "Song B", "album": "Hits 2020", "duration": 180, "source": "deezer"})
    gate = threading.Event()
    real_resolve = metadata.resolve_batch
    def slow_resolve(*a, **k):
        assert gate.wait(10)
        return real_resolve(*a, **k)
    monkeypatch.setattr(metadata, "resolve_batch", slow_resolve)
    job = c.post("/api/jobs/catalog", json={"song": {"title": "Song B", "artist": "Artist", "album": "Album X",
                                                      "duration": 180, "video_id": "catB"}}).json()
    end = time.time() + 10
    while c.get(f"/api/jobs/{job['id']}").json()["status"] != "awaiting_confirmation" and time.time() < end:
        time.sleep(0.05)
    c.post(f"/api/jobs/{job['id']}/confirm-match", json={"action": "accept", "playlist_mode": "none"})
    time.sleep(0.5)
    assert env["downloaded"] == ["https://www.youtube.com/watch?v=catB"]
    gate.set()
    job = wait_for(c, job["id"], {"done"})
    assert job["tracks"][0]["match"]["album"] == "Album X"
    assert os.path.exists(env["lib"] / "Artist" / "Album X" / "1 - Song B.m4a")


def test_preview_status_reports_youtubes_delay(env, monkeypatch):
    c = env["client"]
    gate = threading.Event()
    def audio_stream(vid):
        assert gate.wait(10)
        return {"url": "http://x", "mime": "audio/mp4", "available_at": time.time() + 12}
    monkeypatch.setattr(downloader, "audio_stream", audio_stream)
    server._stream_cache.clear()
    assert c.get("/api/preview-status/abcdefghijk").json()["state"] == "idle"
    server._stream_future("abcdefghijk")  # the player asked for it
    assert c.get("/api/preview-status/abcdefghijk").json()["state"] == "resolving"
    gate.set()
    end = time.time() + 5
    while c.get("/api/preview-status/abcdefghijk").json()["state"] == "resolving" and time.time() < end:
        time.sleep(0.05)
    d = c.get("/api/preview-status/abcdefghijk").json()
    assert d["state"] == "waiting" and 10 < d["wait"] <= 12
    assert c.get("/api/preview-status/bad").status_code == 400


def _lib_song(lib, artist, album, name, seconds=2, art=None):
    from tuberipper import tagger
    d = lib / artist / album
    d.mkdir(parents=True, exist_ok=True)
    path = str(d / name)
    make_audio(path, seconds)
    if art:
        tagger.apply_tags(path, {"artist": artist, "title": name, "album": album}, art)
    return path


def test_library_search_and_in_library_marks(env, monkeypatch):
    from tuberipper import library_index
    c = env["client"]
    library_index.invalidate()
    _lib_song(env["lib"], "Wheatus", "Wheatus", "4 - Teenage Dirtbag.m4a", art=b"\xff\xd8\xff\xe0" + b"0" * 40)
    _lib_song(env["lib"], "Beyoncé", "Lemonade", "1 - Pray You Catch Me.m4a")
    (env["lib"] / "Wheatus" / "Wheatus" / "4 - Teenage Dirtbag.lrc").write_text("[00:01.00] hi")

    found = c.get("/api/library/find", params={"q": "dirtbag wheat"}).json()
    assert [s["title"] for s in found["songs"]] == ["Teenage Dirtbag"]
    s = found["songs"][0]
    assert (s["artist"], s["album"], s["track_number"], s["has_lyrics"], s["path"]) == (
        "Wheatus", "Wheatus", 4, True, "Wheatus/Wheatus/4 - Teenage Dirtbag.m4a")
    assert c.get("/api/library/find", params={"q": "beyonce"}).json()["artists"] == [{"artist": "Beyoncé", "count": 1}]

    monkeypatch.setattr(catalog, "search", lambda q: {"songs": [
        {"title": "Teenage Dirtbag", "artist": "Wheatus", "album": "Wheatus"},
        {"title": "Teenage Dirtbag (Sped Up)", "artist": "Wheatus & Slater", "album": "x"},
    ], "albums": [{"title": "Wheatus (Deluxe Edition)", "artist": "Wheatus"}, {"title": "Hand Over Your Loved Ones", "artist": "Wheatus"}]})
    res = c.get("/api/search", params={"q": "teenage dirtbag"}).json()
    assert [x["in_library"] for x in res["songs"]] == [True, False]
    assert [x["in_library"] for x in res["albums"]] == [True, False]

    cover = c.get("/api/library/cover", params={"artist": "Wheatus", "album": "Wheatus"})
    assert cover.status_code == 200 and cover.headers["content-type"] == "image/jpeg"
    assert c.get("/api/library/cover", params={"artist": "Beyoncé", "album": "Lemonade"}).status_code == 404
    f = c.get("/api/library/file", params={"path": "Wheatus/Wheatus/4 - Teenage Dirtbag.m4a"}, headers={"Range": "bytes=0-99"})
    assert f.status_code == 206 and len(f.content) == 100
    assert c.get("/api/library/file", params={"path": "../secret.m4a"}).status_code in (400, 404)


def test_suggestions_never_fail(env, monkeypatch):
    c = env["client"]
    class Y:
        def get_search_suggestions(self, q):
            if q == "boom":
                raise RuntimeError("down")
            return ["teenage dirtbag", "teenage dirtbag clean", {"odd": 1}]
    monkeypatch.setattr(catalog, "ytm", lambda: Y())
    server._suggest_cache.clear()
    assert c.get("/api/suggest", params={"q": "teenage"}).json() == ["teenage dirtbag", "teenage dirtbag clean"]
    assert c.get("/api/suggest", params={"q": "boom"}).json() == []
    assert c.get("/api/suggest", params={"q": "t"}).json() == []


def test_quick_add_does_not_download_an_unsure_match(env, monkeypatch):
    c = env["client"]
    # The tapped row has no album, and the lookup lands on a different song.
    monkeypatch.setattr(metadata, "resolve_batch", lambda tracks, **k: [
        {"artist": "Kenny Chesney", "album_artist": "Kenny Chesney", "title": "Don't Blink", "album": "Just Who I Am",
         "year": "2007", "genre": "", "artwork_url": "", "track_number": 2, "disc_number": 1, "duration": 180, "source": "itunes"}
        for _ in tracks])
    job = c.post("/api/jobs/catalog", json={"song": {"title": "Every Breath You Take", "artist": "Kenneth Acoustic",
                                                      "duration": 180, "video_id": "catB"},
                                             "add": {"playlist_mode": "none"}}).json()
    job = wait_for(c, job["id"], {"awaiting_confirmation", "done"})
    assert job["status"] == "awaiting_confirmation" and job["needs_check"] == 1
    assert env["downloaded"] == []


def test_musicbrainz_pass_keeps_a_better_deezer_match(env, monkeypatch):
    c = env["client"]
    monkeypatch.setattr(deezer, "match_song", lambda artist, title, *a, **k: {
        "artist": artist, "title": title, "album": "Covers", "duration": 180, "source": "deezer"})
    monkeypatch.setattr(metadata, "resolve_batch", lambda tracks, **k: [
        {"artist": "Kenny Chesney", "title": "Don't Blink", "album": "Just Who I Am", "duration": 180, "source": "itunes"}
        for _ in tracks])
    job = c.post("/api/jobs/catalog", json={"song": {"title": "Every Breath You Take", "artist": "Kenneth Acoustic",
                                                      "duration": 180, "video_id": "catB"}}).json()
    job = wait_for(c, job["id"], {"awaiting_confirmation"})
    m = job["tracks"][0]["match"]
    assert (m["title"], m["album"], job["tracks"][0]["quality"]) == ("Every Breath You Take", "Covers", "good")


def test_quick_add_downloads_without_confirming_into_a_playlist(env, monkeypatch):
    from tuberipper import library_index
    c = env["client"]
    job = c.post("/api/jobs/catalog", json={"song": {"title": "Song B", "artist": "Artist", "album": "Album X",
                                                      "duration": 180, "video_id": "catB"},
                                             "add": {"playlist_mode": "new", "playlist_name": "Road trip"}}).json()
    job = wait_for(c, job["id"], {"done"})
    assert job["tracks"][0]["status"] == "done" and job["playlist_name"] == "Road trip"
    assert "Album X/1 - Song B.m4a" in (env["lib"] / "Playlists" / "Road trip.m3u8").read_text(encoding="utf-8")
    assert library_index.has_song(str(env["lib"]), "Artist", "Song B")  # index refreshed by the download


def test_song_nobody_knows_uses_the_search_row(env, monkeypatch):
    """A cover only YouTube Music has: no service knows it, so the tapped
    search row's album/year/cover are used -- and quick add goes ahead."""
    c = env["client"]
    monkeypatch.setattr(metadata, "resolve_batch", lambda tracks, **k: [metadata._blank_match(t["artist"], t["title"]) for t in tracks])
    job = c.post("/api/jobs/catalog", json={"song": {"title": "Every Breath You Take", "artist": "Kenneth Acoustic",
                                                      "album": "Acoustic Covers Vol 8", "year": "2019", "duration": 180,
                                                      "video_id": "catB", "artwork": "http://ytm/cover.jpg"},
                                             "add": {"playlist_mode": "none"}}).json()
    job = wait_for(c, job["id"], {"done", "awaiting_confirmation"})
    t = job["tracks"][0]
    assert job["status"] == "done" and t["status"] == "done", (job["status"], t["status"], job.get("needs_check"))
    assert (t["match"]["album"], t["match"]["year"], t["match"]["source"]) == ("Acoustic Covers Vol 8", "2019", "catalog")
    assert os.path.exists(env["lib"] / "Kenneth Acoustic" / "Acoustic Covers Vol 8" / "Every Breath You Take.m4a")


def test_library_home_listings_and_playlists(env):
    from tuberipper import library_index
    c = env["client"]
    library_index.invalidate()
    a = _lib_song(env["lib"], "Wheatus", "Wheatus", "4 - Teenage Dirtbag.m4a")
    b = _lib_song(env["lib"], "Beyoncé", "Lemonade", "1 - Pray You Catch Me.m4a")
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    library_index.invalidate()
    recent = c.get("/api/library/recent").json()
    assert [x["album"] for x in recent] == ["Lemonade", "Wheatus"]  # newest first
    assert [x["album"] for x in c.get("/api/library/albums").json()] == ["Lemonade", "Wheatus"]
    songs = c.get("/api/library/songs").json()
    assert [s["title"] for s in songs] == ["Pray You Catch Me", "Teenage Dirtbag"] and "_fold" not in songs[0] and "mtime" not in songs[0]

    pdir = env["lib"] / "Playlists"
    pdir.mkdir()
    (pdir / "Road trip.m3u8").write_text("#EXTM3U\n#PLAYLIST:Road trip\n#EXTINF:-1,x\n../Wheatus/Wheatus/4 - Teenage Dirtbag.m4a\n../gone/x.m4a\n", encoding="utf-8")
    pls = c.get("/api/library/playlists").json()
    assert pls == [{"id": "m3u:Road trip.m3u8", "name": "Road trip", "song_count": 2}]
    got = c.get("/api/library/playlist", params={"id": "m3u:Road trip.m3u8"}).json()
    assert [s["path"] for s in got] == ["Wheatus/Wheatus/4 - Teenage Dirtbag.m4a"]


def test_preview_is_remuxed_to_a_plain_mp4_for_safari(env, monkeypatch, tmp_path):
    """YouTube's fragmented m4a is served as a plain (non-fragmented) MP4
    with the right duration; seeking works."""
    import struct, subprocess, types
    c = env["client"]
    frag = tmp_path / "frag.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "6",
                    "-c:a", "aac", "-movflags", "frag_keyframe+empty_moov+default_base_moof", str(frag)], check=True)
    data = frag.read_bytes()
    assert b"moof" in data  # the input really is fragmented
    class Up:
        status_code = 206
        def iter_content(self, n): yield data
        def close(self): pass
    monkeypatch.setattr(server.requests, "get", lambda *a, **k: Up())
    monkeypatch.setattr(server, "_PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setattr(server, "_stream_info", lambda vid: {"url": "http://x", "mime": "audio/mp4", "user_agent": "t"})
    r = c.get("/api/preview/abcdefghijk")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mp4"
    boxes, i = [], 0
    while i + 8 <= len(r.content):
        size, typ = struct.unpack(">I4s", r.content[i:i + 8])
        boxes.append(typ)
        i += size
    assert b"moof" not in boxes and b"moov" in boxes
    out = tmp_path / "served.m4a"
    out.write_bytes(r.content)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True).stdout)
    assert 5.5 < dur < 6.5
    part = c.get("/api/preview/abcdefghijk", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and len(part.content) == 10
