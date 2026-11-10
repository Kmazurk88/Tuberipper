"""Loudness (volume levelling) and silence detection."""

import types

from fastapi.testclient import TestClient

from tuberipper import audio_analysis as aa, navidrome, server


def fake_run(stderr):
    return lambda cmd, **kw: types.SimpleNamespace(stderr=stderr, stdout="", returncode=0)


def test_loudness_is_read_from_the_ffmpeg_summary(monkeypatch):
    out = "[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n    I:         -12.4 LUFS\n    Threshold: -22.4 LUFS\n"
    monkeypatch.setattr(aa.subprocess, "run", fake_run(out))
    assert aa.measure("x.mp3") == -12.4
    monkeypatch.setattr(aa.subprocess, "run", fake_run("nothing useful"))
    assert aa.measure("x.mp3") is None


def test_silences_are_paired_up_and_rounded(monkeypatch):
    out = "silence_start: 10.5\nsilence_end: 12.25 | silence_duration: 1.75\nsilence_start: -0.001\nsilence_end: 0.9 | silence_duration: 0.9\n"
    monkeypatch.setattr(aa.subprocess, "run", fake_run(out))
    assert aa.detect_silences("x.mp3") == [[10.5, 12.25], [0, 0.9]]


def test_the_app_gets_levels_for_measured_songs_and_the_rest_are_queued(monkeypatch):
    monkeypatch.setattr(aa, "start", lambda: None)
    monkeypatch.setattr(navidrome, "configured", lambda: True)
    songs = {"a": {"id": "a", "title": "Loud One", "size": 111}, "b": {"id": "b", "title": "New Song", "size": 222}}
    monkeypatch.setattr(navidrome, "_call", lambda ep, params=None, **k: {"song": songs[dict(params)["id"]]})
    monkeypatch.setattr(aa.library_delete, "find_file", lambda song: "/music/new.mp3")
    aa._loud.clear(); aa._song_key.clear(); aa._want.clear()
    aa._loud["111|loud one"] = -8.0
    r = TestClient(server.app).get("/api/loudness?ids=a,b").json()
    assert r["levels"] == {"a": -8.0}
    assert aa._want == [("222|new song", "/music/new.mp3")]   # measured next, ahead of the slow sweep
    assert TestClient(server.app).get("/api/loudness?ids=b").json()["levels"] == {}
    assert len(aa._want) == 1   # not queued twice


def test_silence_ids_are_validated(monkeypatch):
    monkeypatch.setattr(navidrome, "configured", lambda: True)
    assert TestClient(server.app).get("/api/podcast/silences?id=../../etc").status_code == 400


def test_silences_are_computed_once_then_served_from_the_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("TUBERIPPER_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(navidrome, "configured", lambda: True)
    monkeypatch.setattr(navidrome, "_call", lambda ep, params=None, **k: {"song": {"title": "Ep", "size": 5}})
    monkeypatch.setattr(aa, "_find_episode", lambda song: "/podcasts/ep.mp3")
    monkeypatch.setattr(aa, "detect_silences", lambda p: [[1.0, 3.0]])
    aa._sil_busy.clear()
    c = TestClient(server.app)
    assert c.get("/api/podcast/silences?id=ep1").json() == {"ready": False}
    import time
    for _ in range(50):
        r = c.get("/api/podcast/silences?id=ep1").json()
        if r.get("ready"):
            break
        time.sleep(0.1)
    assert r == {"ready": True, "ranges": [[1.0, 3.0]]}
