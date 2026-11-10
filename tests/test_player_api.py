"""The player's Navidrome proxy, saved state and Spotify CSV import, with Navidrome faked."""

import pytest
import requests
from fastapi.testclient import TestClient

from tuberipper import player_api, server, spotify_import


class FakeResp:
    def __init__(self, status=200, body=b"", headers=None, json_body=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self._json = json_body

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, n):
        yield self._body

    def close(self):
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NAVIDROME_URL", "http://nd:4533")
    monkeypatch.setenv("NAVIDROME_USER", "tuberipper")
    monkeypatch.setenv("NAVIDROME_PASSWORD", "secret")
    calls = []

    def fake_get(url, params=None, headers=None, **kw):
        calls.append({"url": url, "params": dict(params), "list": list(params), "headers": headers or {}})
        if url.endswith("/stream"):
            return FakeResp(206, b"abc", {"Content-Type": "audio/mpeg", "Content-Range": "bytes 0-2/10", "Content-Length": "3"})
        if url.endswith("/getCoverArt"):
            return FakeResp(200, b"jpg", {"Content-Type": "image/jpeg"})
        return FakeResp(200, json_body={"subsonic-response": {"status": "ok"}})

    monkeypatch.setattr(player_api._http, "get", fake_get)
    c = TestClient(server.app)
    c.calls = calls
    return c


def test_proxy_signs_requests_and_never_leaks_the_password(client):
    r = client.get("/api/nd/rest/getAlbumList2?type=newest&size=5")
    assert r.status_code == 200
    sent = client.calls[0]["params"]
    assert sent["u"] == "tuberipper" and sent["type"] == "newest" and sent["f"] == "json"
    assert "secret" not in str(client.calls) and sent["t"] != "secret" and "p" not in sent


def test_client_cannot_choose_who_it_signs_in_as(client):
    client.get("/api/nd/rest/ping?u=admin&p=hunter2&t=x&s=y")
    sent = client.calls[0]["params"]
    assert sent["u"] == "tuberipper" and "p" not in sent and sent["t"] != "x" and sent["s"] != "y"


def test_only_whitelisted_calls_are_forwarded(client):
    assert client.get("/api/nd/rest/getUsers").status_code == 404
    assert client.get("/api/nd/rest/deleteUser?username=x").status_code == 404
    assert not client.calls


def test_repeated_params_survive(client):
    client.get("/api/nd/rest/updatePlaylist?playlistId=1&songIdToAdd=a&songIdToAdd=b")
    assert [v for k, v in client.calls[0]["list"] if k == "songIdToAdd"] == ["a", "b"]


def test_stream_passes_range_through_and_asks_for_the_raw_file(client):
    r = client.get("/api/nd/stream/song1", headers={"Range": "bytes=0-2"})
    assert r.status_code == 206 and r.content == b"abc"
    assert r.headers["content-range"] == "bytes 0-2/10"
    call = client.calls[0]
    assert call["headers"]["Range"] == "bytes=0-2" and call["params"]["format"] == "raw" and call["params"]["id"] == "song1"


def test_navidrome_error_bodies_are_not_served_as_audio_or_covers(client, monkeypatch):
    monkeypatch.setattr(player_api._http, "get", lambda *a, **k: FakeResp(200, b"<xml/>", {"Content-Type": "application/xml"}))
    assert client.get("/api/nd/stream/nope").status_code == 404
    assert client.get("/api/nd/cover/nope").status_code == 404


def test_unconfigured_navidrome_is_a_clear_503(monkeypatch):
    for k in ("NAVIDROME_URL", "NAVIDROME_USER", "NAVIDROME_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert TestClient(server.app).get("/api/nd/rest/ping").status_code == 503


def test_state_round_trip_and_cleanup(client):
    assert client.get("/api/app/state").json()["pins"] == []
    body = {"pins": [{"type": "playlist", "id": "p1", "name": "Rap", "evil": "x"}, {"bad": 1}], "recents": [{"type": "album", "id": "a1", "name": "A", "ts": 5}]}
    assert client.put("/api/app/state", json=body).status_code == 200
    s = client.get("/api/app/state").json()
    assert s["pins"] == [{"type": "playlist", "id": "p1", "name": "Rap"}]  # unknown keys and malformed rows dropped
    assert s["recents"][0]["id"] == "a1"


def test_state_rejects_junk(client):
    assert client.put("/api/app/state", content=b"not json").status_code == 400
    assert client.put("/api/app/state", content=b"x" * (300 * 1024)).status_code == 413


def test_root_is_the_player_and_classic_still_serves_the_old_ui(client):
    assert "static/app.js" in client.get("/").text
    assert "Add to Library" in client.get("/classic").text or "tuberipper" in client.get("/classic").text
    assert client.get("/tuberipper/").status_code == 200


# ---------- Spotify CSV import ----------

CSV = ("Track URI,Track Name,Artist Name(s),Duration (ms)\n"
       "u1,Piano Man,Billy Joel,339000\n"
       'u2,Under Pressure - Remastered 2011,"Queen;David Bowie",248000\n'
       "u3,,Nobody,1\n")


def test_csv_parsing_skips_blank_rows_and_splits_artists():
    rows = spotify_import.parse_csv("﻿" + CSV)
    assert [r["title"] for r in rows] == ["Piano Man", "Under Pressure - Remastered 2011"]
    assert rows[1]["artists"] == ["Queen", "David Bowie"] and rows[1]["secs"] == 248


def test_matching_prefers_studio_over_live_and_needs_the_artist():
    ok = {"title": "Piano Man", "artist": "Billy Joel", "duration": 339}
    live = {"title": "Piano Man (Live)", "artist": "Billy Joel", "duration": 339}
    other = {"title": "Piano Man", "artist": "Someone Else", "duration": 339}
    assert spotify_import._score(ok, ["Billy Joel"], "Piano Man", 339) > 0.9
    assert spotify_import._score(live, ["Billy Joel"], "Piano Man", 339) is None
    assert spotify_import._score(other, ["Billy Joel"], "Piano Man", 339) is None
    # a "- 2011 Remaster" suffix on Spotify's side doesn't hurt the match
    assert spotify_import._score({"title": "Under Pressure", "artist": "Queen & David Bowie", "duration": 248}, ["Queen"], "Under Pressure - 2011 Remaster", 248) > 0.9


def test_import_endpoint_validates_input(client):
    assert client.post("/api/app/import", json={"csv": "nonsense"}).status_code == 400
    assert client.post("/api/app/import", content=b"not json").status_code == 400


def test_apk_download(client, tmp_path, monkeypatch):
    assert client.get("/app.apk").status_code == 404
    import os
    root = os.path.dirname(player_api._state_path())
    os.makedirs(root, exist_ok=True)
    open(os.path.join(root, "tuberipper.apk"), "wb").write(b"PK\x03\x04")
    r = client.get("/app.apk")
    assert r.status_code == 200 and r.headers["content-type"] == "application/vnd.android.package-archive" and r.content == b"PK\x03\x04"


def test_app_assets_are_versioned_and_the_page_always_revalidated(client):
    html = client.get("/").text
    import re
    assert "AUTO" not in html
    assert re.search(r'static/app\.css\?v=\d+', html) and re.search(r'static/app\.js\?v=\d+', html)
    assert client.get("/").headers["cache-control"] == "no-cache"
    # The page is always re-checked and names the current version; a versioned file can then be kept for good.
    assert "immutable" in client.get("/static/app.js?v=1").headers["cache-control"]
    assert client.get("/static/app.css").headers["cache-control"] == "no-cache"
