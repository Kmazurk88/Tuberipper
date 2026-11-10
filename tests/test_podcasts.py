"""Podcast release dates (from the files), the About tab, and 'finished' episode state."""

import subprocess

import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import ID3, TIT2, TXXX

from tuberipper import navidrome, podcasts, server


def make_episode(path, title, released, itunes="1594668202"):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", "-q:a", "9", str(path)], check=True)
    t = ID3()
    t.add(TIT2(encoding=3, text=title))
    t.add(TXXX(encoding=3, desc="releasedate", text=released))
    t.add(TXXX(encoding=3, desc="itunes-id", text=itunes))
    t.save(str(path))


@pytest.fixture
def shows(tmp_path, monkeypatch):
    d = tmp_path / "podcasts" / "Show"
    d.mkdir(parents=True)
    make_episode(d / "a.mp3", "Ep 2 | Cigars & Rum – Ep. #2", "Tue, 16 Dec 2025 05:00:00 -0500")
    make_episode(d / "b.mp3", "Ep 1", "Tue, 09 Dec 2025 05:00:00 -0500")
    monkeypatch.setenv("TUBERIPPER_PODCASTS_DIR", str(tmp_path / "podcasts"))
    podcasts._files.clear()
    monkeypatch.setattr(navidrome, "configured", lambda: True)
    songs = [{"id": "s2", "title": "Ep 2 | Cigars & Rum – Ep. #2"}, {"id": "s1", "title": "Ep 1"}, {"id": "s0", "title": "No such file"}]
    monkeypatch.setattr(navidrome, "_call", lambda ep, params=None, **k: {"album": {"name": "Show", "artist": "Host", "genres": [{"name": "Podcasts"}, {"name": "Hobbies"}], "song": songs}})
    return TestClient(server.app)


def test_dates_come_from_the_files_and_match_by_title(shows):
    d = shows.get("/api/podcast/dates?album=x").json()["dates"]
    assert d["s2"]["date"] == "2025-12-16" and d["s1"]["date"] == "2025-12-09"
    assert d["s2"]["ts"] > d["s1"]["ts"]
    assert "s0" not in d  # an episode with no matching file just has no date


def test_a_missing_podcast_folder_is_not_an_error(shows, monkeypatch, tmp_path):
    monkeypatch.setenv("TUBERIPPER_PODCASTS_DIR", str(tmp_path / "nowhere"))
    assert shows.get("/api/podcast/dates?album=x").json() == {"dates": {}}


def test_date_formats():
    assert podcasts._parse_date("Tue, 16 Dec 2025 05:00:00 -0500").date().isoformat() == "2025-12-16"
    assert podcasts._parse_date("2024-03-05T10:00:00Z").date().isoformat() == "2024-03-05"
    assert podcasts._parse_date("") is None and podcasts._parse_date("garbage") is None


def test_about_uses_itunes_and_the_feed_and_is_cached(shows, monkeypatch):
    calls = []

    class R:
        status_code = 200

        def json(self):
            return {"results": [{"artistName": "The Host Co", "genres": ["Leisure", "Podcasts"], "feedUrl": "https://feeds.example/rss"}]}

    monkeypatch.setattr(podcasts.requests, "get", lambda *a, **k: calls.append(a) or R())
    monkeypatch.setattr(podcasts, "_feed_info", lambda url: {"description": "A weekly show.", "link": "https://show.example"})
    d = shows.get("/api/podcast/about?album=x").json()
    assert d["publisher"] == "The Host Co" and d["genres"] == ["Leisure"] and d["description"] == "A weekly show." and d["episodes"] == 3
    assert d["first_ts"] < d["latest_ts"]
    shows.get("/api/podcast/about?album=x")
    assert len(calls) == 1  # the second answer came from the cache


def test_the_feed_fetcher_never_touches_private_addresses(monkeypatch):
    got = []
    monkeypatch.setattr(podcasts.requests, "get", lambda *a, **k: got.append(a))
    for url in ("http://127.0.0.1/x", "http://localhost/x", "http://192.168.1.50:4533/x", "http://10.0.0.5/", "http://169.254.169.254/latest", "file:///etc/passwd", "ftp://example.com/x"):
        assert podcasts._safe_get(url) is None, url
    assert not got  # nothing was even requested


def test_feed_header_is_parsed_without_reading_the_episodes(monkeypatch):
    xml = b'<?xml version="1.0"?><rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><title>T</title><link>https://s.example</link><description>About the show</description><item><title>E</title></item></channel></rss>'

    class R:
        status_code = 200
        is_redirect = False

        def iter_content(self, n):
            yield xml

        def close(self):
            pass

    monkeypatch.setattr(podcasts, "_safe_get", lambda url: R())
    assert podcasts._feed_info("https://feeds.example/rss") == {"description": "About the show", "link": "https://s.example"}


def test_finished_episodes_are_saved_with_the_app_state(shows):
    r = shows.put("/api/app/state", json={"pins": [], "recents": [], "finished": ["a1", "b2", 5, "x" * 99, ""]})
    assert r.status_code == 200
    assert shows.get("/api/app/state").json()["finished"] == ["a1", "b2"]


def test_episode_titles_match_when_navidrome_appends_the_description():
    from tuberipper.discover import pod_norm
    nd = "Olivia Colman (Actress feels excited.\n\n\n\nCall our voicemail: (323) 362-2295.)"
    assert pod_norm(nd) == pod_norm("Olivia Colman") == "olivia colman"
