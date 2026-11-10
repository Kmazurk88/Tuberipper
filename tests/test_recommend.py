"""Home recommendations: what you play -> songs you don't own, with YouTube Music, Last.fm and Navidrome faked."""

import pytest
from fastapi.testclient import TestClient

from tuberipper import catalog, recommend, server

THUMBS = [{"url": "https://x/y=w60-h60-l90-rj"}]


def song(vid, title, artist):
    return {"videoId": vid, "title": title, "artists": [{"name": artist}], "album": {"name": "A"}, "duration_seconds": 200, "thumbnails": THUMBS}


class FakeYTM:
    def search(self, q, filter=None, limit=None):
        if filter == "artists":
            return [{"artist": q, "browseId": "UC-" + q.replace(" ", "")}]
        if filter == "songs":                      # "Artist Title" -> that song
            for artist in ("Carole King", "James Taylor", "Elton John"):
                if q.startswith(artist):
                    title = q[len(artist):].strip()
                    return [{"resultType": "song", "videoId": "v" + artist[:3] + title.replace(" ", ""), "title": title, "artists": [{"name": artist}],
                             "album": {"name": "A"}, "duration_seconds": 200, "thumbnails": THUMBS}]
        return []

    def get_artist(self, bid):
        if bid == "UC-BillyJoel":
            return {"name": "Billy Joel", "songs": {"results": [song("b1", "Vienna", "Billy Joel")]},
                    "related": {"results": [{"title": "Elton John", "browseId": "UC-Elton"}, {"title": "Carole King", "browseId": "UC-Carole"},
                                            {"title": "Owned Artist", "browseId": "UC-Owned"}]}}
        name = {"UC-Elton": "Elton John", "UC-Carole": "Carole King", "UC-Owned": "Owned Artist"}[bid]
        return {"name": name, "songs": {"results": [song("s-" + name[:3], "Top Song", name)]}, "related": {"results": []}}


def nd_call(endpoint, params=None, timeout=15):
    if endpoint == "getMusicFolders":
        return {"musicFolders": {"musicFolder": [{"id": "1", "name": "Music"}, {"id": "2", "name": "Podcasts"}]}}
    if endpoint == "getAlbumList2":
        assert ("musicFolderId", "1") in (params or [])          # podcasts are left out
        return {"albumList2": {"album": [{"artist": "Billy Joel", "playCount": 9}]}}
    if endpoint == "getStarred2":
        return {"starred2": {}}
    raise AssertionError(endpoint)


@pytest.fixture
def client(monkeypatch, tmp_path):
    key = tmp_path / "lastfm-key.txt"
    monkeypatch.setenv("LASTFM_KEY_FILE", str(key))
    monkeypatch.setattr(catalog, "ytm", lambda: FakeYTM())
    monkeypatch.setattr(recommend.navidrome, "_call", nd_call)
    monkeypatch.setattr(recommend.library_index, "has_song", lambda root, artist, title: artist == "Owned Artist" or title == "Owned Song")
    monkeypatch.setattr(recommend, "_state", {"data": None, "built": 0.0, "building": False})
    warmed = []
    monkeypatch.setattr(recommend, "_warm", warmed.append)
    c = TestClient(server.app)
    c.key, c.warmed = key, warmed
    return c


def lastfm(monkeypatch):
    def fake(method, **p):
        if method == "artist.getsimilar":
            return {"similarartists": {"artist": [{"name": n} for n in ("Carole King", "James Taylor", "Elton John")]}}
        return {"toptracks": {"track": [{"name": "Owned Song"}, {"name": "Fresh Song"}]}}
    monkeypatch.setattr(recommend, "_lastfm", fake)


def test_discover_shelf_skips_owned_artists_and_podcasts(client, monkeypatch):
    monkeypatch.setattr(recommend, "MIN_SHELF", 2)
    d = client.get("/api/recommend/home").json()
    assert d["lastfm"] is False
    shelf = d["shelves"][0]
    assert shelf["title"] == "Discover"
    assert {s["artist"] for s in shelf["songs"]} == {"Elton John", "Carole King"}      # Owned Artist is dropped
    assert all(s["video_id"] and s["because"] == "Billy Joel" for s in shelf["songs"])


def test_because_shelf_needs_the_lastfm_key(client, monkeypatch):
    monkeypatch.setattr(recommend, "MIN_SHELF", 2)
    lastfm(monkeypatch)
    client.key.write_text("abc\n")
    d = client.get("/api/recommend/home").json()
    titles = [s["title"] for s in d["shelves"]]
    assert "Because you listened to Billy Joel" in titles and d["lastfm"] is True
    because = next(s for s in d["shelves"] if s["id"].startswith("because"))
    assert {s["title"] for s in because["songs"]} == {"Fresh Song"}                   # "Owned Song" is skipped
    assert {s["artist"] for s in because["songs"]} == {"Carole King", "James Taylor", "Elton John"}


def test_no_because_shelf_without_key(client, monkeypatch):
    monkeypatch.setattr(recommend, "MIN_SHELF", 2)
    lastfm(monkeypatch)
    d = client.get("/api/recommend/home").json()
    assert all(not s["id"].startswith("because") for s in d["shelves"])


def test_added_songs_drop_out_and_first_ones_are_warmed(client, monkeypatch):
    monkeypatch.setattr(recommend, "MIN_SHELF", 1)
    first = client.get("/api/recommend/home").json()["shelves"][0]["songs"]
    assert client.warmed and set(client.warmed) <= {s["video_id"] for s in first}
    monkeypatch.setattr(recommend.library_index, "has_song", lambda root, artist, title: artist in ("Owned Artist", "Elton John"))
    again = client.get("/api/recommend/home").json()["shelves"][0]["songs"]
    assert {s["artist"] for s in again} == {"Carole King"}


def test_navidrome_down_gives_no_shelves(client, monkeypatch):
    def boom(*a, **k):
        raise recommend.navidrome.NavidromeError("down")
    monkeypatch.setattr(recommend.navidrome, "_call", boom)
    assert client.get("/api/recommend/home").json()["shelves"] == []


def test_key_check(monkeypatch):
    class R:
        status_code = 200
        def __init__(self, body): self.body = body
        def json(self): return self.body
    monkeypatch.setattr(recommend.requests, "get", lambda *a, **k: R({"error": 10, "message": "Invalid API key"}))
    with pytest.raises(ValueError):
        recommend.check_lastfm_key("bad")
    monkeypatch.setattr(recommend.requests, "get", lambda *a, **k: R({"artists": {}}))
    recommend.check_lastfm_key("good")


def test_sleep_sounds_are_not_taste(client, monkeypatch):
    def calls(endpoint, params=None, timeout=15):
        if endpoint == "getAlbumList2":
            return {"albumList2": {"album": [{"artist": "White Noise Masters, Deep Sleep", "name": "Rain Sound 200 Minutes", "playCount": 50},
                                              {"artist": "Billy Joel", "name": "Piano Man", "playCount": 2}]}}
        return nd_call(endpoint, params, timeout)
    monkeypatch.setattr(recommend.navidrome, "_call", calls)
    assert recommend.top_artists() == ["Billy Joel"]


def test_a_song_is_on_one_shelf_only():
    a = {"video_id": "v1", "artist": "X", "title": "One"}
    shelves = recommend._dedupe([
        {"id": "a", "songs": [a, dict(a, video_id="v2", title="Two"), dict(a, video_id="v3", title="Three"), dict(a, video_id="v4", title="Four"), dict(a, video_id="v5", title="Five")]},
        {"id": "b", "songs": [a, dict(a, video_id="v9", title="Nine")]}])
    assert [s["id"] for s in shelves] == ["a"]


def test_no_play_history_falls_back_to_the_biggest_library_artists(client, monkeypatch):
    def empty(endpoint, params=None, timeout=15):
        return {"albumList2": {}} if endpoint == "getAlbumList2" else nd_call(endpoint, params, timeout)
    monkeypatch.setattr(recommend.navidrome, "_call", empty)
    songs = [{"artist": "Queen", "album": "A"}] * 3 + [{"artist": "Abba", "album": "B"}] * 2 + [{"artist": "Various Artists", "album": "C"}] * 9
    monkeypatch.setattr(recommend.library_index, "all_songs", lambda root: songs)
    assert recommend.top_artists() == ["Queen", "Abba"]
