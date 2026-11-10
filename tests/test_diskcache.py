import time

import pytest

from tuberipper import diskcache, musicbrainz


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TUBERIPPER_CACHE_DIR", str(tmp_path))
    diskcache.reset()
    yield tmp_path
    diskcache.reset()


def test_roundtrip_and_expiry(cache_dir, monkeypatch):
    diskcache.put("k", {"a": [1, "ü"]})
    assert diskcache.get("k") == {"a": [1, "ü"]}
    assert diskcache.get("missing") is None
    real = time.time
    monkeypatch.setattr(diskcache.time, "time", lambda: real() + diskcache.TTL + 1)
    assert diskcache.get("k") is None


def test_musicbrainz_answers_come_from_disk_the_second_time(cache_dir, monkeypatch):
    calls = []

    found = {"recordings": [{"id": "r1"}], "count": 1}

    def fetch(path, params):
        calls.append(path)
        if path == "recording":
            return found if params["query"] == "x" else {"recordings": [], "count": 0}
        return None

    monkeypatch.setattr(musicbrainz, "_fetch", fetch)
    assert musicbrainz._get("recording", {"query": "x"}) == found
    diskcache.reset()  # as after a restart
    assert musicbrainz._get("recording", {"query": "x"}) == found
    assert calls == ["recording"]
    # Failures aren't cached.
    assert musicbrainz._get("release/abc", {}) is None
    assert musicbrainz._get("release/abc", {}) is None
    assert calls == ["recording", "release/abc", "release/abc"]
    # Nor is "nothing found" (the song may be added to MusicBrainz tomorrow).
    musicbrainz._get("recording", {"query": "new song"})
    musicbrainz._get("recording", {"query": "new song"})
    assert calls[-2:] == ["recording", "recording"]


def test_unwritable_location_just_disables_caching(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("TUBERIPPER_CACHE_DIR", str(blocker / "sub"))
    diskcache.reset()
    diskcache.put("k", 1)
    assert diskcache.get("k") is None
    diskcache.reset()
