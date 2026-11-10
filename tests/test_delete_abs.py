"""Deleting songs/episodes, and the Audiobookshelf link."""

import os
import subprocess

import pytest
from fastapi.testclient import TestClient
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError

from tuberipper import abs as absmod, auth, config, library_delete, navidrome, server


def make_mp3(path, title, album, artist="Artist", seconds=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(seconds), "-q:a", "9", str(path)], check=True)
    try:
        t = EasyID3(str(path))
    except ID3NoHeaderError:
        t = EasyID3()
    t["title"], t["album"], t["artist"] = title, album, artist
    t.save(str(path))
    return os.path.getsize(path)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = tmp_path / "music"
    root.mkdir()
    monkeypatch.setattr(config, "DEFAULT_OUTPUT_DIR", str(root))
    monkeypatch.setattr(library_delete, "_cache", {"at": 0.0, "by_size": {}})
    songs = {}
    monkeypatch.setattr(navidrome, "configured", lambda: True)
    monkeypatch.setattr(navidrome, "_call", lambda ep, params=None, **k: {"song": songs[dict(params)["id"]]} if ep == "getSong" else {})
    c = TestClient(server.app)
    c.root, c.songs = root, songs
    return c


def add(lib, sid, rel, title, album, **kw):
    p = lib.root / rel
    size = make_mp3(p, title, album, **kw)
    lib.songs[sid] = {"id": sid, "title": title, "album": album, "size": size}
    return p


def test_deleting_a_song_removes_the_file_lyrics_and_an_emptied_folder(lib):
    p = add(lib, "s1", "Band/Album One/01 - Alpha.mp3", "Alpha", "Album One")
    (lib.root / "Band/Album One/01 - Alpha.lrc").write_text("[00:01.00] hi")
    (lib.root / "Band/Album One/cover.jpg").write_bytes(b"x")
    r = lib.post("/api/library/delete", json={"ids": ["s1"]}).json()
    assert r == {"deleted": ["s1"], "failed": []}
    assert not (lib.root / "Band").exists()  # album folder and the now-empty artist folder are gone, cover included


def test_other_albums_and_the_artists_own_files_survive(lib):
    add(lib, "s1", "Band/Album One/01 - Alpha.mp3", "Alpha", "Album One")
    keep = add(lib, "s2", "Band/Album Two/01 - Beta.mp3", "Beta", "Album Two")
    (lib.root / "Band/folder.jpg").write_bytes(b"artist picture")
    (lib.root / "Band/Album One/cover.jpg").write_bytes(b"x")
    assert lib.post("/api/library/delete", json={"ids": ["s1"]}).json()["deleted"] == ["s1"]
    assert keep.exists() and (lib.root / "Band/folder.jpg").exists()  # the artist folder still has an album: untouched
    assert not (lib.root / "Band/Album One").exists()


def test_an_album_folder_keeps_its_cover_while_songs_remain(lib):
    add(lib, "s1", "B/A/01 - One.mp3", "One", "A")
    add(lib, "s2", "B/A/02 - Two.mp3", "Two", "A", seconds=2)
    (lib.root / "B/A/cover.jpg").write_bytes(b"x")
    lib.post("/api/library/delete", json={"ids": ["s1"]})
    assert (lib.root / "B/A/cover.jpg").exists() and (lib.root / "B/A/02 - Two.mp3").exists()
    lib.post("/api/library/delete", json={"ids": ["s2"]})
    assert not (lib.root / "B").exists()


def test_ambiguity_never_deletes_the_wrong_file(lib):
    a = add(lib, "s1", "X/Real Album/01 - Same.mp3", "Same", "Real Album")
    b = lib.root / "X/Other Folder/01 - Same.mp3"
    os.makedirs(b.parent)
    b.write_bytes(a.read_bytes())  # an identical copy elsewhere (same size, same tags)
    assert lib.post("/api/library/delete", json={"ids": ["s1"]}).json()["deleted"] == ["s1"]
    assert not a.exists() and b.exists()  # the one in the folder named like the album went; the copy stayed
    # two identical copies and NO folder match: refuse rather than guess
    c1 = lib.root / "Y/F1/s.mp3"; c2 = lib.root / "Y/F2/s.mp3"
    size = make_mp3(c1, "Tie", "Nowhere")
    os.makedirs(c2.parent); c2.write_bytes(c1.read_bytes())
    lib.songs["s9"] = {"id": "s9", "title": "Tie", "album": "Nowhere", "size": size}
    r = lib.post("/api/library/delete", json={"ids": ["s9"]}).json()
    assert r["deleted"] == [] and r["failed"][0]["id"] == "s9" and c1.exists() and c2.exists()


def test_unknown_songs_and_wrong_sizes_delete_nothing(lib):
    p = add(lib, "s1", "Q/R/01 - Song.mp3", "Song", "R")
    lib.songs["s1"]["size"] += 5
    r = lib.post("/api/library/delete", json={"ids": ["s1"]}).json()
    assert r["deleted"] == [] and p.exists() and "nothing was deleted" in r["failed"][0]["reason"]


def test_cross_site_requests_and_empty_bodies_are_refused(lib):
    p = add(lib, "s1", "Q/R/01 - Song.mp3", "Song", "R")
    assert lib.post("/api/library/delete", json={"ids": ["s1"]}, headers={"origin": "https://evil.example"}).status_code == 403
    assert p.exists()
    assert lib.post("/api/library/delete", json={"ids": []}).status_code == 400
    assert lib.post("/api/library/delete", content=b"nope").status_code == 400


def test_delete_file_refuses_anything_outside_the_library(lib, tmp_path):
    outside = tmp_path / "elsewhere.mp3"
    outside.write_bytes(b"x")
    with pytest.raises(Exception):
        library_delete.delete_file(str(outside))
    assert outside.exists()


def test_deleting_is_blocked_on_the_public_port(tmp_path):
    auth.save_user("kev", "a long enough password")
    c = TestClient(server.public_app, base_url="https://music.example", follow_redirects=False)
    assert c.post("/api/auth/login", json={"username": "kev", "password": "a long enough password"}, headers={"x-forwarded-proto": "https"}).status_code == 200
    assert c.post("/api/library/delete", json={"ids": ["x"]}).status_code == 404
    assert c.post("/api/podcast/delete", json={"song": "x"}).status_code == 404
    assert c.post("/api/podcast/delete-show", json={"songs": ["x"]}).status_code == 404
    assert c.get("/api/podcast/capabilities").status_code == 200  # sync is fine over the internet


# ---------------- Audiobookshelf ----------------

@pytest.fixture
def abs_env(monkeypatch):
    monkeypatch.setenv("AUDIOBOOKSHELF_TOKEN", "k")
    absmod._cache.update(at=0.0, map={}, rev={})
    calls = []

    def fake_req(method, path, **kw):
        calls.append((method, path, kw))
        if path == "/api/libraries":
            return {"libraries": [{"id": "L1", "mediaType": "podcast"}, {"id": "L2", "mediaType": "book"}]}
        if path == "/api/libraries/L1/items":
            return {"results": [{"id": "I1"}]}
        if path == "/api/items/I1":
            return {"media": {"metadata": {"title": "The Show"}, "episodes": [{"id": "E1", "title": "Ep 1 – Intro"}, {"id": "E2", "title": "Ep 2"}]}}
        if path == "/api/me":
            return {"mediaProgress": [{"episodeId": "E1", "currentTime": 120.5, "duration": 3000, "isFinished": False, "lastUpdate": 1700000000000},
                                      {"episodeId": "E2", "currentTime": 0, "duration": 100, "isFinished": True, "lastUpdate": 1700000005000},
                                      {"episodeId": "E3", "currentTime": 50, "duration": 100, "isFinished": False, "lastUpdate": 1},
                                      {"libraryItemId": "B1", "currentTime": 9}]}
        return {}

    def fake_nd(ep, params=None, **k):
        if ep == "getMusicFolders":
            return {"musicFolders": {"musicFolder": [{"id": 1, "name": "Music Library"}, {"id": 2, "name": "Podcasts"}]}}
        if ep == "getAlbumList2":
            return {"albumList2": {"album": [{"id": "A1"}]}}
        if ep == "getAlbum":
            return {"album": {"song": [{"id": "n1", "title": "Ep 1 - Intro", "album": "The Show"}, {"id": "n2", "title": "Ep 2", "album": "The Show"}, {"id": "n3", "title": "Unknown", "album": "The Show"}]}}
        return {}

    monkeypatch.setattr(absmod, "_req", fake_req)
    monkeypatch.setattr(navidrome, "_call", fake_nd)
    c = TestClient(server.app)
    c.calls = calls
    return c


def test_not_configured_means_503_and_the_app_is_told(monkeypatch):
    monkeypatch.delenv("AUDIOBOOKSHELF_TOKEN", raising=False)
    c = TestClient(server.app)
    assert c.get("/api/podcast/capabilities").json() == {"abs": False}
    assert c.get("/api/podcast/abs-progress").status_code == 503
    assert c.post("/api/podcast/delete", json={"song": "n1"}).status_code == 503


def test_the_token_can_live_in_a_file(monkeypatch, tmp_path):
    monkeypatch.delenv("AUDIOBOOKSHELF_TOKEN", raising=False)
    (tmp_path / "cache").mkdir(exist_ok=True)
    (tmp_path / "cache" / "abs-token.txt").write_text("filekey\n")
    assert absmod._token() == "filekey" and absmod.configured()


def test_progress_is_mapped_to_the_players_episode_ids(abs_env):
    assert abs_env.get("/api/podcast/capabilities").json() == {"abs": True}
    p = {x["song"]: x for x in abs_env.get("/api/podcast/abs-progress").json()["progress"]}
    assert set(p) == {"n1", "n2"}  # episodes the player doesn't have, and books, are ignored
    assert p["n1"]["position"] == 120500 and p["n1"]["finished"] is False and p["n1"]["ts"] == 1700000000000
    assert p["n2"]["finished"] is True


def test_pushing_progress_updates_audiobookshelf(abs_env):
    assert abs_env.post("/api/podcast/abs-progress", json={"song": "n1", "position": 90000, "duration": 3000, "finished": False}).json() == {"ok": True}
    m, path, kw = [c for c in abs_env.calls if c[0] == "PATCH"][0]
    assert path == "/api/me/progress/I1/E1" and kw["json"]["currentTime"] == 90 and kw["json"]["isFinished"] is False and abs(kw["json"]["progress"] - 0.03) < 1e-6
    abs_env.post("/api/podcast/abs-progress", json={"song": "n2", "position": 10, "duration": 100, "finished": True})
    last = [c for c in abs_env.calls if c[0] == "PATCH"][-1][2]["json"]
    assert last["isFinished"] is True and last["currentTime"] == 100 and last["progress"] == 1.0
    assert abs_env.post("/api/podcast/abs-progress", json={"song": "n3", "position": 1, "duration": 1, "finished": False}).status_code == 404


def test_deleting_an_episode_goes_through_audiobookshelf_with_the_file(abs_env):
    assert abs_env.post("/api/podcast/delete", json={"song": "n2"}).json() == {"ok": True}
    m, path, kw = [c for c in abs_env.calls if c[0] == "DELETE"][0]
    assert path == "/api/podcasts/I1/episode/E2" and kw["params"] == {"hard": 1}
    assert abs_env.post("/api/podcast/delete", json={"song": "n3"}).status_code == 404  # not in Audiobookshelf: nothing happens
    assert abs_env.post("/api/podcast/delete", json={"song": "n2"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert len([c for c in abs_env.calls if c[0] == "DELETE"]) == 1


def test_deleting_a_whole_podcast_goes_through_audiobookshelf_with_the_files(abs_env):
    assert abs_env.post("/api/podcast/delete-show", json={"songs": ["n1", "n2", "n3"]}).json() == {"ok": True, "podcasts": 1}
    m, path, kw = [c for c in abs_env.calls if c[0] == "DELETE"][0]
    assert path == "/api/items/I1" and kw["params"] == {"hard": 1}
    assert abs_env.post("/api/podcast/delete-show", json={"songs": ["n3"]}).status_code == 404  # unknown to Audiobookshelf: nothing happens
    assert abs_env.post("/api/podcast/delete-show", json={"songs": ["n1"]}, headers={"origin": "https://evil.example"}).status_code == 403
    assert len([c for c in abs_env.calls if c[0] == "DELETE"]) == 1


def test_audiobookshelf_errors_are_reported_not_hidden(abs_env, monkeypatch):
    def boom(method, path, **kw):
        raise absmod.AbsError("Audiobookshelf refused the API key.")
    monkeypatch.setattr(absmod, "_req", boom)
    r = abs_env.get("/api/podcast/abs-progress")
    assert r.status_code == 502 and "refused" in r.json()["detail"]


# ---------------- the paste-a-key page ----------------

def test_setup_page_saves_a_checked_key_privately_and_never_echoes_it(tmp_path, monkeypatch):
    from tuberipper import secrets_setup
    target = tmp_path / "abs-token.txt"
    monkeypatch.setenv("AUDIOBOOKSHELF_TOKEN_FILE", str(target))
    monkeypatch.delenv("AUDIOBOOKSHELF_TOKEN", raising=False)
    monkeypatch.setitem(secrets_setup.SECRETS, "abs", secrets_setup.SECRETS["abs"][:3] + (lambda v: None if v == "good" else (_ for _ in ()).throw(secrets_setup.HTTPException(400, "refused")), absmod.invalidate))
    c = TestClient(server.app)
    page = c.get("/setup")
    assert page.status_code == 200 and "not set" in page.text
    assert c.post("/api/setup/secret", json={"name": "abs", "value": "bad"}).status_code == 400 and not target.exists()
    assert c.post("/api/setup/secret", json={"name": "nope", "value": "x"}).status_code == 400
    assert c.post("/api/setup/secret", json={"name": "abs", "value": "good"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/setup/secret", json={"name": "abs", "value": "good"}).json() == {"ok": True}
    assert target.read_text() == "good\n" and oct(target.stat().st_mode & 0o777) == "0o600"
    assert absmod.configured() and "good" not in c.get("/setup").text and "saved" in c.get("/setup").text


def test_setup_page_is_not_available_on_the_public_port(tmp_path):
    auth.save_user("kev", "a long enough password")
    c = TestClient(server.public_app, base_url="https://music.example", follow_redirects=False)
    c.post("/api/auth/login", json={"username": "kev", "password": "a long enough password"}, headers={"x-forwarded-proto": "https"})
    assert c.get("/setup").status_code == 404 and c.post("/api/setup/secret", json={"name": "abs", "value": "x"}).status_code == 404
