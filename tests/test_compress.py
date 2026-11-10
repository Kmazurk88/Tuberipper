from fastapi.testclient import TestClient

from tuberipper import server

client = TestClient(server.app)


def test_script_is_gzipped_and_kept_when_versioned():
    r = client.get("/static/app.js?v=1", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert "immutable" in r.headers["cache-control"]
    assert "function" in r.text  # the client un-gzips it back to the script


def test_unversioned_static_is_rechecked():
    r = client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.headers["cache-control"] == "no-cache"


def test_no_gzip_unless_asked():
    r = client.get("/static/app.js?v=1", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers
    assert int(r.headers["content-length"]) == len(r.content)


def test_images_and_ranges_pass_through():
    r = client.get("/static/icon-512.png", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in r.headers
    r = client.get("/static/app.js?v=1", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"})
    assert r.status_code == 206 and "content-encoding" not in r.headers and len(r.content) == 100


def test_page_is_gzipped():
    r = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip" and "app.js?v=" in r.text


def test_busy_counts_audio_being_sent(monkeypatch):
    import asyncio
    from tuberipper import activity

    seen = []

    async def audio_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"audio/mp4")]})
        seen.append(activity.streams())
        await send({"type": "http.response.body", "body": b"x"})

    async def sink(message):
        pass

    asyncio.run(activity.CountAudio(audio_app)({"type": "http"}, None, sink))
    assert seen == [1] and activity.streams() == 0
    assert client.get("/api/busy").json() == {"job": False, "streams": 0}
