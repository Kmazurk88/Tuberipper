"""Jobs and playlist imports survive a restart (durable.py, server.resume_jobs, spotify_import.resume)."""

import os
import threading
import time
import types

import pytest

from tuberipper import durable, jobs, server, spotify_import, tagger
from tuberipper.jobs import build_track


class SyncThread:
    """Runs a thread's target right away, so the tests can check what a restart started."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(server.store, "_jobs", {})
    monkeypatch.setattr(spotify_import, "_jobs", {})
    monkeypatch.setattr(spotify_import, "_work", {})
    monkeypatch.setattr(spotify_import, "_running", None)
    # Only these two modules' view of threading: the real Thread is still used by thread pools.
    fake = types.SimpleNamespace(Thread=SyncThread, Lock=threading.Lock, RLock=threading.RLock, Event=threading.Event)
    monkeypatch.setattr(server, "threading", fake)
    monkeypatch.setattr(spotify_import, "threading", fake)
    yield


def _restart():
    """What a restart does to the in-memory state: everything is gone; only the disk copy is left."""
    server.store.save_all()
    spotify_import.save_all()
    server.store._jobs.clear()
    spotify_import._jobs.clear()
    spotify_import._work.clear()
    spotify_import._running = None
    with durable._lock:
        durable._saved.clear()


def _job(status, track_states, **extra):
    job = server.store.create("catalog-album:Artist - Album", "/music")
    for i, st in enumerate(track_states):
        t = build_track(i, f"v{i}", f"https://www.youtube.com/watch?v=v{i}", f"Song {i}", "Artist", f"Song {i}", "catalog")
        t["status"] = st
        t["match"] = {"artist": "Artist", "title": f"Song {i}", "album": "Album"}
        job.tracks.append(t)
    job.status = status
    for k, v in extra.items():
        setattr(job, k, v)
    return job


# ---------- durable ----------

def test_records_round_trip_and_unchanged_ones_are_not_rewritten():
    durable.put("x", "a", {"n": 1})
    first = durable._db().execute("SELECT updated FROM records WHERE id='a'").fetchone()[0]
    time.sleep(0.01)
    durable.put("x", "a", {"n": 1})
    assert durable._db().execute("SELECT updated FROM records WHERE id='a'").fetchone()[0] == first
    durable.put("x", "b", {"n": 2})
    assert sorted(d["n"] for d in durable.load("x")) == [1, 2]
    durable.delete("x", "a")
    assert [d["n"] for d in durable.load("x")] == [2]


def test_job_snapshot_round_trip():
    job = _job("awaiting_confirmation", ["awaiting_confirmation"], album_hint="Album", auto_accept=True, notify=False)
    again = jobs.Job.from_snapshot(job.snapshot())
    assert again.to_dict() == job.to_dict()
    assert again.auto_accept and not again.notify and again.out_dir == "/music" and again.created_at == job.created_at
    assert again.refined.is_set()   # a restored job never waits for MusicBrainz


def test_nothing_is_written_after_shutdown(monkeypatch):
    monkeypatch.setattr(durable, "_frozen", False)
    durable.put("x", "a", {"status": "processing"})
    durable.freeze()
    durable.put("x", "a", {"status": "error"})     # a thread failing while the process winds down
    assert durable.load("x") == [{"status": "processing"}]


# ---------- jobs ----------

def test_interrupted_downloads_carry_on_and_finished_songs_are_not_redone(monkeypatch):
    job = _job("processing", ["done", "downloading", "tagging", "queued_download", "skipped_not_accepted"])
    _restart()
    processed, told = [], []
    monkeypatch.setattr(server, "_process_track", lambda j, t: (processed.append(t["index"]), t.update(status="done")))
    monkeypatch.setattr(server.notify, "job_finished", lambda j: told.append(j.id))

    did = server.resume_jobs()

    assert did["downloads"] == [job.id]
    back = server.store.get(job.id)
    assert processed == [1, 2, 3]
    assert [t["status"] for t in back.tracks] == ["done", "done", "done", "done", "skipped_not_accepted"]
    assert back.status == "done" and told == [job.id]


def test_restart_between_last_song_and_playlist_still_finishes(monkeypatch):
    job = _job("processing", ["done", "failed"], playlist_mode="existing", playlist_id="pl1")
    _restart()
    playlists, processed = [], []
    monkeypatch.setattr(server, "_process_track", lambda j, t: processed.append(t))
    monkeypatch.setattr(server, "_apply_playlist", lambda j: playlists.append(j.playlist_id))
    monkeypatch.setattr(server.notify, "job_finished", lambda j: None)
    assert server.resume_jobs()["downloads"] == [job.id]
    assert processed == [] and playlists == ["pl1"] and server.store.get(job.id).status == "done"


def test_album_check_cut_off_by_the_restart_is_done_again_before_downloading(monkeypatch):
    job = _job("processing", ["done", "downloading", "queued_download"], refining=True, lookup_gen=4, album_hint="Hint")
    _restart()
    order = []
    monkeypatch.setattr(server, "_refine_with_musicbrainz",
                        lambda j, tracks, quick, hint, is_album, gen: order.append(("refine", [t["index"] for t in tracks], [q["album"] for q in quick], hint, is_album, gen, j.refining)))
    monkeypatch.setattr(server, "_finish_job", lambda job_id: order.append(("download", server.store.get(job_id).refining)))
    server.resume_jobs()
    assert order == [("refine", [1, 2], ["Album", "Album"], "Hint", True, 4, True), ("download", False)]


def test_no_album_check_again_when_it_had_finished(monkeypatch):
    _job("processing", ["downloading"])
    _restart()
    monkeypatch.setattr(server, "_refine_with_musicbrainz", lambda *a: pytest.fail("not needed"))
    monkeypatch.setattr(server, "_finish_job", lambda job_id: None)
    server.resume_jobs()


def test_interrupted_lookup_runs_again(monkeypatch):
    job = _job("processing", ["looking_up", "queued_lookup"])
    _restart()
    looked = []
    monkeypatch.setattr(server, "_match_job", lambda job_id, album_hint=None: looked.append(job_id))
    assert server.resume_jobs()["lookups"] == [job.id] and looked == [job.id]


def test_quick_add_waiting_for_its_final_check_is_decided(monkeypatch):
    job = _job("awaiting_confirmation", ["awaiting_confirmation", "awaiting_confirmation"], auto_accept=True)
    job.tracks[1]["accepted"] = False
    _restart()
    decided = []
    monkeypatch.setattr(server, "_quick_add_decide", lambda j, tracks, final: decided.append(([t["index"] for t in tracks], final)))
    assert server.resume_jobs()["quick_adds"] == [job.id]
    assert decided == [([0], True)] and server.store.get(job.id).auto_accept is False


def test_jobs_waiting_for_the_user_or_finished_are_just_shown_again(monkeypatch):
    waiting = _job("awaiting_confirmation", ["awaiting_confirmation"])
    done = _job("done", ["done"])
    _restart()
    monkeypatch.setattr(server, "_finish_job", lambda *a: pytest.fail("nothing should run"))
    monkeypatch.setattr(server, "_match_job", lambda *a, **k: pytest.fail("nothing should run"))
    did = server.resume_jobs()
    assert did["kept"] == 2 and server.store.get(waiting.id).status == "awaiting_confirmation" and server.store.get(done.id)


def test_old_finished_jobs_are_dropped_from_disk(monkeypatch):
    old = _job("done", ["done"])
    old.created_at = time.time() - 3 * 24 * 3600
    _restart()
    monkeypatch.setattr(server, "_finish_job", lambda *a: None)
    server.resume_jobs()
    assert server.store.get(old.id) is None and durable.load(jobs.KIND) == []


def test_busy_reports_a_resumed_download(monkeypatch):
    job = _job("processing", ["downloading"])
    _restart()
    monkeypatch.setattr(server, "_finish_job", lambda *a: None)   # still running
    server.resume_jobs()
    assert server.store.active().id == job.id


# ---------- imports ----------

ROWS = [{"title": f"Song {i}", "artists": ["Artist"], "secs": 200} for i in range(4)]


def test_import_carries_on_after_a_restart(monkeypatch):
    calls, made = [], []
    monkeypatch.setattr(spotify_import, "_wait_for_server", lambda timeout=180: True)
    monkeypatch.setattr(spotify_import, "_make_playlist", lambda imp, metas, pid, scan: made.append(([m[1] for m in metas], pid)))
    monkeypatch.setattr(spotify_import, "_discord", lambda imp: None)
    monkeypatch.setattr(spotify_import, "_save_report", lambda imp: None)

    # Before the restart: song 0 was added, song 1 not found, song 2's download had started (job j2), song 3 untouched.
    imp = {"id": "imp1", "name": "Road trip", "status": "running", "total": 4, "done": 2,
           "counts": {"added": 1, "already": 0, "review": 0, "nomatch": 1, "failed": 0}, "problems": [], "unplaced": [],
           "playlist": "", "playlist_id": None, "started": time.time()}
    spotify_import._jobs["imp1"] = imp
    spotify_import._work["imp1"] = {"rows": ROWS, "target": "pl9", "pending": {"2": {"job": "j2", "meta": {"title": "Song 2"}}},
                                    "results": {"0": ["added", {"title": "Song 0"}, "", "Artist - Song 0"],
                                                "1": ["nomatch", None, "", "Artist - Song 1"]}}
    _restart()

    def one(row, pending=None):
        calls.append((row["title"], pending and pending["job"]))
        return "added", {"title": row["title"]}, ""
    monkeypatch.setattr(spotify_import, "_import_one", one)

    assert spotify_import.resume() == ["imp1"]
    out = spotify_import.get("imp1")
    assert sorted(calls) == [("Song 2", "j2"), ("Song 3", None)]      # 0 and 1 not looked at again; 2 waits for its job
    assert out["status"] == "done" and out["done"] == 4
    assert out["counts"] == {"added": 3, "already": 0, "review": 0, "nomatch": 1, "failed": 0}
    assert [p["song"] for p in out["problems"]] == ["Artist - Song 1"]
    assert made == [(["Song 0", "Song 2", "Song 3"], "pl9")]           # CSV order, same target playlist
    assert "work" not in durable.load(spotify_import.KIND)[0]           # the song list is dropped once finished


def test_import_cut_off_by_shutdown_is_left_running_not_failed(monkeypatch):
    import requests
    reports = []
    monkeypatch.setattr(spotify_import, "_wait_for_server", lambda timeout=180: False)    # the server is going away
    monkeypatch.setattr(spotify_import, "_discord", lambda imp: reports.append("discord"))
    monkeypatch.setattr(spotify_import, "_save_report", lambda imp: reports.append("report"))
    monkeypatch.setattr(spotify_import, "_make_playlist", lambda *a: pytest.fail("not finished: no playlist"))
    monkeypatch.setattr(spotify_import, "WORKERS", 1)

    def one(row, pending=None):
        if row["title"] == "Song 2":
            raise requests.ConnectionError("Connection refused")
        return "added", {"title": row["title"]}, ""
    monkeypatch.setattr(spotify_import, "_import_one", one)
    imp = spotify_import.start(ROWS, "Cut off")
    assert imp["status"] == "running" and reports == []
    spotify_import.save_all()
    saved = durable.load(spotify_import.KIND)[0]
    assert "2" not in saved["work"]["results"] and "0" in saved["work"]["results"]   # song 2 is left for the restart
    assert saved["imp"]["counts"]["failed"] == 0


def test_a_short_hiccup_reaching_the_server_is_retried(monkeypatch):
    import requests
    calls = []
    monkeypatch.setattr(spotify_import, "_wait_for_server", lambda timeout=180: True)

    def one(row, pending=None):
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError("reset")
        return "added", {"title": row["title"]}, ""
    monkeypatch.setattr(spotify_import, "_import_one", one)
    assert spotify_import._import_one_safe(ROWS[0])[0] == "added" and len(calls) == 2


def test_finished_import_is_still_viewable_after_a_restart():
    spotify_import._jobs["imp2"] = {"id": "imp2", "name": "x", "status": "done", "total": 1, "done": 1, "counts": {}, "problems": [],
                                    "unplaced": [], "playlist": "Playlist “x” has 1 song.", "playlist_id": None, "started": time.time()}
    _restart()
    assert spotify_import.resume() == []
    assert spotify_import.get("imp2")["playlist"] == "Playlist “x” has 1 song."


def test_import_one_waits_for_its_old_job_instead_of_starting_another(monkeypatch):
    monkeypatch.setattr(spotify_import, "_job_alive", lambda job_id: job_id == "j2")
    monkeypatch.setattr(spotify_import, "_find", lambda *a: pytest.fail("must not search again"))
    monkeypatch.setattr(spotify_import, "_wait_for", lambda job_id: {"status": "done", "tracks": [{"status": "done"}]})
    assert spotify_import._import_one(ROWS[2], {"job": "j2", "meta": {"title": "Song 2"}}) == ("added", {"title": "Song 2"}, "")


# ---------- the library never gets half a song ----------

def test_write_tags_leaves_no_temporary_file(tmp_path, monkeypatch):
    src = tmp_path / "in.m4a"
    src.write_bytes(b"audio")
    monkeypatch.setattr(tagger, "apply_tags", lambda *a: None)
    new = tagger.write_tags(str(src), {"artist": "A", "album_artist": "A", "album": "B", "title": "C", "track_number": 1}, None, str(tmp_path / "lib"))
    assert open(new, "rb").read() == b"audio" and not src.exists()
    assert [f for f in os.listdir(os.path.dirname(new)) if f.endswith(".part")] == []
