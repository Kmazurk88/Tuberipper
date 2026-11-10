"""Artist / album / song info endpoints, with YouTube Music and Wikipedia faked."""

import pytest
from fastapi.testclient import TestClient

from tuberipper import catalog, discover, server

THUMBS = [{"url": "https://x/y=w60-h60-l90-rj", "width": 60, "height": 60}]


class FakeYTM:
    def __init__(self):
        self.calls = []

    def search(self, q, filter=None, limit=None):
        self.calls.append(("search", q, filter))
        if filter == "artists":
            return [{"artist": "Billy Joel", "browseId": "UC1"}] if "joel" in q.lower() else [{"artist": "Totally Different", "browseId": "UC9"}]
        if filter == "albums":
            return [{"title": "Piano Man (Legacy Edition)", "browseId": "MP0", "artists": [{"name": "Billy Joel"}]},
                    {"title": "Piano Man", "browseId": "MP1", "artists": [{"name": "Billy Joel"}]}]
        return []

    def get_artist(self, bid):
        self.calls.append(("artist", bid))
        return {"name": "Billy Joel", "description": "A singer.\n\nFrom Wikipedia (https://en.wikipedia.org/wiki/Billy_Joel) under CC", "monthlyListeners": "25M",
                "thumbnails": [{"url": "https://x/banner=w2880-h1200-p"}],
                "songs": {"browseId": "VLx", "results": [{"videoId": "v1", "title": "Piano Man"}]},
                "albums": {"browseId": "MPAD", "params": "p", "results": [{"title": "Short", "browseId": "MPs"}]},
                "singles": {"browseId": None, "results": [{"title": "Single One", "browseId": "MPsingle", "type": "Single", "year": "1990"}]},
                "related": {"results": [{"title": "Carole King", "browseId": "UC2", "thumbnails": THUMBS}]}}

    def get_playlist(self, bid, limit=None):
        return {"tracks": [{"videoId": "v1", "title": "Piano Man", "artists": [{"name": "Billy Joel"}], "album": {"name": "Piano Man"}, "duration_seconds": 339, "thumbnails": THUMBS},
                           {"videoId": "v2", "title": "Vienna", "artists": [{"name": "Billy Joel"}], "album": {"name": "The Stranger"}, "duration_seconds": 214, "thumbnails": THUMBS}]}

    def get_artist_albums(self, bid, params):
        return [{"title": "Full One", "browseId": "MPa", "type": "Album", "year": "1973", "thumbnails": THUMBS},
                {"title": "Full Two", "browseId": "MPb", "type": "Album", "year": "1977", "thumbnails": THUMBS}]

    def get_album(self, bid):
        return {"title": "Piano Man", "type": "Album", "year": "1973", "artists": [{"name": "Billy Joel", "id": "UC1"}], "thumbnails": THUMBS, "duration_seconds": 1900,
                "tracks": [{"videoId": "a", "title": "Travelin' Prayer", "artists": [{"name": "Billy Joel"}], "trackNumber": 1},
                           {"videoId": "b", "title": "Piano Man", "artists": [{"name": "Billy Joel"}], "trackNumber": 2, "isExplicit": False}]}


@pytest.fixture
def client(monkeypatch, tmp_path):
    fake = FakeYTM()
    monkeypatch.setattr(catalog, "ytm", lambda: fake)
    monkeypatch.setattr(discover.library_index, "has_song", lambda root, artist, title: title == "Piano Man")
    monkeypatch.setattr(discover.library_index, "has_album", lambda root, artist, title: title == "Full One")
    c = TestClient(server.app)
    c.ytm = fake
    return c


def test_artist_page_data(client):
    d = client.get("/api/discover/artist?name=Billy Joel").json()
    assert d["name"] == "Billy Joel" and d["listeners"] == "25M"
    assert d["bio"] == "A singer." and d["bio_source"] == "Wikipedia"  # attribution moved to bio_source
    assert [s["title"] for s in d["popular"]] == ["Piano Man", "Vienna"]
    assert [s["in_library"] for s in d["popular"]] == [True, False]
    assert [a["title"] for a in d["albums"]] == ["Full One", "Full Two"]  # the full list, not the 1-item preview
    assert [a["in_library"] for a in d["albums"]] == [True, False]
    assert d["singles"][0]["type"] == "Single" and d["related"][0]["name"] == "Carole King"
    assert d["popular"][0]["video_id"] == "v1" and d["popular"][0]["artist"] == "Billy Joel"


def test_artist_results_are_cached(client):
    client.get("/api/discover/artist?name=Billy Joel")
    n = len(client.ytm.calls)
    client.get("/api/discover/artist?name=Billy Joel")
    assert len(client.ytm.calls) == n


def test_wrong_artist_is_a_404_not_a_guess(client):
    assert client.get("/api/discover/artist?name=Unknown Band").status_code == 404
    assert client.get("/api/discover/artist").status_code == 400


def test_credited_pair_falls_back_to_first_artist(client):
    # "Billy Joel, Someone" doesn't match as a whole but the first artist does
    assert client.get("/api/discover/artist?name=Billy Joel, Someone").status_code == 200


def test_album_resolution_needs_an_exact_title_not_the_deluxe_lookalike(client):
    d = client.get("/api/discover/album?artist=Billy Joel&title=Piano Man").json()
    assert d["browse_id"] == "MP1" and d["year"] == "1973"
    assert [t["track_number"] for t in d["tracks"]] == [1, 2]
    assert [t["in_library"] for t in d["tracks"]] == [False, True]
    assert d["tracks"][0]["album"] == "Piano Man" and d["tracks"][0]["artwork"]


class WikiResp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status

    def json(self):
        return self._b

    def raise_for_status(self):
        pass


def fake_wiki(monkeypatch, articles):
    def get(url, params=None, **kw):
        if "api.php" in url:
            return WikiResp({"query": {"search": [{"title": t} for t in articles]}})
        title = url.rsplit("/", 1)[-1].replace("_", " ")
        from urllib.parse import unquote
        a = articles.get(unquote(title))
        return WikiResp(a, 200) if a else WikiResp({}, 404)
    monkeypatch.setattr(discover.requests, "get", get)


def test_song_blurb_only_for_the_right_article(client, monkeypatch):
    fake_wiki(monkeypatch, {
        "Piano Man (film)": {"description": "1990 film", "extract": "A film."},
        "Piano Man": {"description": "1973 single by Billy Joel", "extract": "\"Piano Man\" is a song by Billy Joel.", "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Piano_Man"}}},
    })
    r = client.get("/api/discover/song?artist=Billy Joel&title=Piano Man").json()
    assert r["blurb"]["source"] == "Wikipedia" and "song by Billy Joel" in r["blurb"]["text"] and r["blurb"]["url"].endswith("Piano_Man")


def test_song_blurb_rejects_a_same_named_song_by_someone_else(client, monkeypatch):
    fake_wiki(monkeypatch, {"Piano Man": {"description": "2001 single by Another Band", "extract": "A different song."}})
    assert client.get("/api/discover/song?artist=Billy Joel&title=Piano Man").json() == {"blurb": None}


def test_song_without_an_article_is_null_and_remembered(client, monkeypatch):
    calls = []
    monkeypatch.setattr(discover, "_wiki_song", lambda a, t: calls.append(1) or {})
    assert client.get("/api/discover/song?artist=X&title=Y").json() == {"blurb": None}
    client.get("/api/discover/song?artist=X&title=Y")
    assert len(calls) == 1


def test_wikipedia_outage_is_not_cached(client, monkeypatch):
    def boom(a, t):
        raise RuntimeError("down")
    monkeypatch.setattr(discover, "_wiki_song", boom)
    assert client.get("/api/discover/song?artist=X&title=Z").json() == {"blurb": None}
    monkeypatch.setattr(discover, "_wiki_song", lambda a, t: {"text": "ok", "source": "Wikipedia", "url": ""})
    assert client.get("/api/discover/song?artist=X&title=Z").json()["blurb"]["text"] == "ok"
