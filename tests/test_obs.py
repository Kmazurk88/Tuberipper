"""Request ids and structured logs (obs.py), the dependency health check (health.py) and problem reports (diag.py)."""

import json
import logging
import os
import threading
import types

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tuberipper import config, diag, health, navidrome, obs, server


class Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def lines(self, event="request"):
        return [r for r in self.records if r.getMessage() == event]


@pytest.fixture
def logs():
    obs.setup()
    h = Collect()
    obs.log.addHandler(h)
    yield h
    obs.log.removeHandler(h)


def mini():
    app = FastAPI()
    app.add_middleware(obs.RequestLog)

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    @app.get("/missing")
    def missing():
        raise HTTPException(404, "nope")

    @app.get("/fine")
    def fine():
        return {"ok": True}

    @app.post("/write")
    def write():
        return {"ok": True}

    @app.get("/api/health")
    def quiet():
        return {"ok": True}

    @app.get("/api/nd/stream/abc")
    def stream():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


# ---------- request ids ----------

def test_every_answer_has_a_request_id_and_a_given_one_is_kept(logs):
    c = mini()
    assert len(c.get("/fine").headers["x-request-id"]) == 12
    assert c.get("/fine", headers={"X-Request-Id": "phone-abc123"}).headers["x-request-id"] == "phone-abc123"
    assert c.get("/fine", headers={"X-Request-Id": "bad id!"}).headers["x-request-id"] != "bad id!"


def test_an_unexpected_error_is_answered_with_its_ref_and_logged_under_it(logs):
    r = mini().get("/boom")
    rid = r.headers["x-request-id"]
    assert r.status_code == 500 and rid in r.json()["detail"] and "secret internal detail" not in r.text
    line = logs.lines()[-1]
    assert line.levelno == logging.ERROR and line.fields["status"] == 500 and line.fields["path"] == "/boom"
    assert "secret internal detail" in obs.JsonFormatter().format(line)          # the log has the traceback...
    assert json.loads(obs.JsonFormatter().format(line))["rid"] == rid            # ...under the same id


def test_failures_are_logged_but_fast_quiet_requests_are_not(logs):
    c = mini()
    c.get("/missing")
    assert logs.lines()[-1].fields["status"] == 404
    before = len(logs.records)
    c.get("/api/health")
    c.get("/api/nd/stream/abc")
    assert len(logs.records) == before
    c.get("/fine")                       # not on the quiet list: logged
    c.post("/write")
    assert [r.fields["path"] for r in logs.lines()[-2:]] == ["/fine", "/write"]


def test_slow_requests_are_warned_about_but_streams_are_never_slow(logs, monkeypatch):
    monkeypatch.setattr(obs, "SLOW_MS", 0)
    c = mini()
    c.get("/api/health")
    assert logs.lines()[-1].levelno == logging.WARNING
    before = len(logs.records)
    c.get("/api/nd/stream/abc")
    assert len(logs.records) == before


def test_the_query_string_is_never_logged(logs):
    mini().get("/fine?q=my+private+search&token=abc")
    text = " ".join(obs.JsonFormatter().format(r) for r in logs.records)
    assert "private" not in text and "token" not in text


def test_the_signed_in_user_is_in_the_log(logs):
    mini().get("/fine", headers={"x-tr-user": "alice"})
    assert logs.lines()[-1].fields["user"] == "alice"


def test_the_real_server_names_its_requests():
    assert TestClient(server.app).get("/api/meta").headers["x-request-id"]
    r = TestClient(server.app).get("/api/meta", headers={"Origin": "http://192.0.2.1:8765"})
    assert "x-request-id" in r.headers["access-control-expose-headers"].lower()


def test_errors_in_background_threads_are_logged(logs):
    def work():
        raise ValueError("thread went wrong")
    t = threading.Thread(target=work, name="bg-test")
    t.start()
    t.join()
    line = logs.lines("thread_error")[-1]
    assert line.fields["thread"] == "bg-test" and "thread went wrong" in obs.JsonFormatter().format(line)


# ---------- health ----------

@pytest.fixture
def healthy(tmp_path, monkeypatch):
    lib = tmp_path / "music"
    lib.mkdir()
    monkeypatch.setattr(config, "DEFAULT_OUTPUT_DIR", str(lib))
    monkeypatch.setattr(health, "_cached", (0.0, None))
    monkeypatch.setattr(navidrome, "configured", lambda: False)
    monkeypatch.setattr(health.shutil, "which", lambda name: "/usr/bin/" + name)
    return lib


def deps():
    r = TestClient(server.app).get("/api/health/deps")
    return r.status_code, r.json()


def test_health_ok_apart_from_an_unconfigured_navidrome(healthy):
    code, body = deps()
    assert code == 200 and body["status"] == "degraded"
    assert body["checks"]["library"]["ok"] and body["checks"]["cache"]["ok"] and not body["checks"]["navidrome"]["ok"]


def test_health_ok_when_everything_answers(healthy, monkeypatch):
    monkeypatch.setattr(navidrome, "configured", lambda: True)
    monkeypatch.setattr(navidrome, "_config", lambda: ("http://nd:4533", "someuser", "hunter2-secret"))
    fake = types.SimpleNamespace(ok=True, status_code=200, json=lambda: {"subsonic-response": {"status": "ok"}})
    seen = {}
    monkeypatch.setattr(health.requests, "get", lambda url, **kw: seen.update(kw=kw) or fake)
    code, body = deps()
    assert code == 200 and body["status"] == "ok"
    text = json.dumps(body)
    assert "hunter2-secret" not in text and "someuser" not in text and "nd:4533" not in text   # no credentials or addresses in the answer
    assert seen["kw"]["timeout"] == 3 and "hunter2-secret" not in json.dumps(seen["kw"]["params"])   # the password itself is never sent (token + salt)


def test_health_is_down_when_the_library_is_missing(healthy, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_OUTPUT_DIR", str(healthy / "nope"))
    code, body = deps()
    assert code == 503 and body["status"] == "down" and "not there" in body["checks"]["library"]["detail"]


def test_low_disk_degrades_and_a_nearly_full_disk_is_down(healthy, monkeypatch):
    usage = lambda free: types.SimpleNamespace(total=100e9, used=100e9 - free, free=free)
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: usage(3e9))
    assert health.check_library()["ok"] is False and not health.check_library().get("critical")
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: usage(0.5e9))
    assert health.check_library().get("critical") is True


def test_navidrome_failure_names_only_the_error_class(healthy, monkeypatch):
    monkeypatch.setattr(navidrome, "configured", lambda: True)
    monkeypatch.setattr(navidrome, "_config", lambda: ("http://10.1.2.3:4533", "u", "p"))

    def refuse(url, **kw):
        raise health.requests.ConnectionError("HTTPConnectionPool(host='10.1.2.3'): refused")
    monkeypatch.setattr(health.requests, "get", refuse)
    detail = health.check_navidrome()["detail"]
    assert detail == "ConnectionError" and "10.1.2.3" not in detail


def test_missing_tools_are_reported(healthy, monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda name: None if name == "deno" else "/usr/bin/" + name)
    assert health.check_tools() == {"ok": False, "detail": "missing: deno"}


def test_health_answers_are_kept_briefly(healthy, monkeypatch):
    calls = []
    monkeypatch.setitem(health.CHECKS, "library", lambda: calls.append(1) or {"ok": True, "detail": "x"})
    deps(), deps()
    assert len(calls) == 1


# ---------- problem reports ----------

class SyncThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def reports(monkeypatch):
    monkeypatch.setattr(diag, "_accepted", [])
    monkeypatch.setattr(diag, "_told", {})
    told = []
    monkeypatch.setattr(diag, "_discord", lambda text, rid: told.append(text))
    monkeypatch.setattr(diag, "threading", types.SimpleNamespace(Thread=SyncThread, Lock=threading.Lock))
    return told


CRASH = {"kind": "crash", "app": {"name": "1.9", "code": 10}, "device": {"android": 34, "model": "Pixel 8"},
         "crashes": [{"ts": 1, "thread": "main", "stack": "java.lang.NullPointerException: x\n\tat app.Foo.bar(Foo.java:1)", "playing": True, "where": "public"}],
         "log": "line1\nline2"}


def post(body, **kw):
    return TestClient(server.app).post("/api/app/diag", json=body, **kw)


def test_a_crash_report_is_saved_listed_and_told_to_discord(reports):
    r = post(CRASH, headers={"x-tr-user": "alice"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["ref"]
    listing = TestClient(server.app).get("/api/app/diag").json()
    assert len(listing) == 1 and listing[0]["kind"] == "crash" and listing[0]["user"] == "alice"
    assert "NullPointerException" in listing[0]["summary"] and "Pixel 8" in listing[0]["summary"]
    full = TestClient(server.app).get("/api/app/diag", params={"id": listing[0]["id"]}).json()
    assert full["crashes"][0]["playing"] is True and full["log"] == "line1\nline2" and full["rid"] == r.json()["ref"]
    assert len(reports) == 1 and "NullPointerException" in reports[0]


def test_the_same_problem_is_told_to_discord_once_per_ten_minutes(reports, monkeypatch):
    post(CRASH)
    post(CRASH)
    assert len(reports) == 1
    post(dict(CRASH, crashes=[dict(CRASH["crashes"][0], stack="java.lang.IllegalStateException: other")]))
    assert len(reports) == 2
    monkeypatch.setattr(diag, "DISCORD_EVERY", -1)
    post(CRASH)
    assert len(reports) == 3


def test_a_js_error_report(reports):
    r = post({"kind": "js", "app": {"name": "web", "code": 0}, "js": {"message": "x is not a function", "source": "/static/app.js", "line": 12, "stack": "s", "shell": "123", "ua": "UA"}})
    assert r.status_code == 200
    assert "JavaScript error: x is not a function" in reports[0]


def test_bad_reports_are_refused(reports):
    assert post({"kind": "weird"}).status_code == 400
    assert post([1, 2]).status_code == 400
    assert TestClient(server.app).post("/api/app/diag", content=b"not json").status_code == 400
    assert TestClient(server.app).post("/api/app/diag", content=b"x" * (diag.MAX_BODY + 1)).status_code == 413


def test_fields_are_cut_and_unknown_ones_dropped(reports):
    body = dict(CRASH, evil="x" * 100, note="n" * 5000, log="l" * 20000)
    body["crashes"] = [dict(CRASH["crashes"][0], stack="s" * 7000, secret="s")] * 9
    assert post(body).status_code == 200
    rid = TestClient(server.app).get("/api/app/diag").json()[0]["id"]
    saved = TestClient(server.app).get("/api/app/diag", params={"id": rid}).json()
    assert len(saved["note"]) == 500 and len(saved["log"]) == 16000 and len(saved["crashes"]) == 5
    assert len(saved["crashes"][0]["stack"]) == 6000 and "secret" not in saved["crashes"][0] and "evil" not in saved


def test_at_most_so_many_reports_an_hour(reports, monkeypatch):
    monkeypatch.setattr(diag, "PER_HOUR", 2)
    assert [post(CRASH).status_code for _ in range(3)] == [200, 200, 429]


def test_only_the_newest_reports_are_kept(reports, monkeypatch):
    monkeypatch.setattr(diag, "MAX_FILES", 3)
    for i in range(5):
        post(dict(CRASH, note=str(i)))
    files = [f for f in os.listdir(diag._dir()) if f.endswith(".json")]
    assert len(files) == 3


def test_reports_cannot_be_read_by_path(reports):
    c = TestClient(server.app)
    assert c.get("/api/app/diag", params={"id": "../../users.json"}).status_code == 400
    assert c.get("/api/app/diag", params={"id": "20260101-000000-abcdef.json"}).status_code == 404
