"""The login gate on the internet-facing port. The normal port must stay exactly as it was."""

import pytest
from fastapi.testclient import TestClient

from tuberipper import auth, server

PW = "correct horse battery"


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    async def instant(_):
        return None
    monkeypatch.setattr(auth.asyncio, "sleep", instant)
    auth.limiter.__init__()


@pytest.fixture
def users(tmp_path):
    auth.save_user("Mom", PW)  # names are case-insensitive
    auth.save_user("other", "another long password")


def public():
    return TestClient(server.public_app, base_url="https://music.example", follow_redirects=False)


def login(c, name="mom", pw=PW):
    return c.post("/api/auth/login", json={"username": name, "password": pw}, headers={"x-forwarded-proto": "https"})


def test_nothing_is_served_until_a_user_exists():
    c = public()
    for path in ("/", "/login", "/api/health", "/api/library"):
        assert c.get(path).status_code == 503


def test_unauthenticated_requests_are_turned_away(users):
    c = public()
    r = c.get("/")
    assert r.status_code == 302 and r.headers["location"].startswith("/login?next=")
    for path in ("/api/library", "/api/nd/rest/getAlbumList2", "/api/app/state", "/api/health", "/static/app.js", "/static/index.html"):
        assert c.get(path).status_code == 401, path
    r = c.get("/app.apk")  # a browser is sent to sign in first, then back to the download
    assert r.status_code == 302 and "/login?next=/app.apk" in r.headers["location"]
    assert c.get("/login").status_code == 200 and "Sign in" in c.get("/login").text
    assert c.get("/static/app.webmanifest").status_code == 200  # needed to install the app


def test_login_flow_and_cookie_flags(users):
    c = public()
    assert login(c, pw="wrong password here").status_code == 401
    assert login(c, name="nobody").status_code == 401
    r = login(c, name="MOM")
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie and "max-age=2592000" in cookie
    assert "static/app.js" in c.get("/").text
    assert c.get("/api/auth/me").json() == {"auth": True, "user": "mom"}


def test_a_forged_or_tampered_session_is_refused(users):
    c = public()
    login(c)
    good = c.cookies.get(auth.cookie_name())
    for bad in (good[:-3] + "AAA", "v1.e30.e30", "garbage", good.replace("v1.", "v2.")):
        c2 = public()
        c2.cookies.set(auth.cookie_name(), bad)
        assert c2.get("/api/library").status_code == 401
    c3 = public()
    c3.get("/", headers={"x-tr-user": "mom", "x-tr-public": "1"})
    assert c3.get("/api/library", headers={"x-tr-user": "mom"}).status_code == 401  # a client can't claim to be signed in


def test_changing_a_password_signs_that_user_out(users):
    c = public()
    login(c)
    assert c.get("/api/auth/me").json()["user"] == "mom"
    auth.save_user("mom", "a brand new password!")
    assert c.get("/api/auth/me").json()["user"] is None
    assert c.get("/api/library").status_code == 401
    assert login(c, pw="a brand new password!").status_code == 200


def test_dangerous_endpoints_are_blocked_even_when_signed_in(users):
    c = public()
    login(c)
    for method, path in (("GET", "/classic"), ("DELETE", "/api/library/SomeArtist"), ("DELETE", "/api/library/A/B/C"), ("POST", "/api/library/merge"),
                         ("POST", "/api/library/fix-albums"), ("POST", "/api/library/scan/abc/repair"), ("POST", "/api/jobs"), ("GET", "/api/debug/search")):
        assert c.request(method, path, json={} if method == "POST" else None).status_code == 404, (method, path)
    assert c.get("/tuberipper/classic").status_code == 404  # also under the path prefix
    assert c.get("/api/library").status_code == 200  # ordinary use still works


def test_cross_site_writes_and_cors_are_refused(users):
    c = public()
    login(c)
    r = c.put("/api/app/state", json={"pins": []}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.put("/api/app/state", json={"pins": []}, headers={"origin": "https://music.example"}).status_code == 200
    r = c.get("/api/auth/me", headers={"origin": "https://evil.example"})
    assert not any(h.lower().startswith("access-control-") for h in r.headers)
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_repeated_wrong_passwords_lock_the_user_name(users):
    c = public()
    for _ in range(auth.LoginLimiter.MAX_USER):
        assert login(c, pw="nope nope nope nope").status_code in (401, 429)
    r = login(c)  # even the right password is refused while locked
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert login(public(), name="other", pw="another long password").status_code == 200  # others are unaffected


def test_lockout_alert_goes_to_discord_once(users, monkeypatch):
    sent = []
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "http://hook.invalid/x")
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: sent.append(k["json"]))
    c = public()
    for _ in range(auth.LoginLimiter.MAX_USER + 3):
        login(c, pw="nope nope nope nope")
    assert len(sent) == 1 and "mom" in sent[0]["embeds"][0]["description"]


def test_logout_and_session_cookie_names_are_per_instance(users, tmp_path, monkeypatch):
    c = public()
    login(c)
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").json()["user"] is None
    first = auth.cookie_name()
    monkeypatch.setenv("TUBERIPPER_CACHE_DIR", str(tmp_path / "second-instance"))
    assert auth.cookie_name() != first  # two instances on one hostname must not share a cookie


def test_password_policy_and_hashes():
    with pytest.raises(ValueError):
        auth.save_user("x", "short")
    h = auth.hash_password("abcdefghijkl")
    assert h.startswith("scrypt$") and "abcdefghijkl" not in h and auth.hash_password("abcdefghijkl") != h
    assert auth.verify_password("abcdefghijkl", h) and not auth.verify_password("abcdefghijkm", h)


def test_the_normal_port_is_unchanged(users):
    lan = TestClient(server.app)
    assert lan.get("/").status_code == 200
    assert lan.get("/classic").status_code == 200
    assert lan.get("/api/health").status_code == 200
    assert lan.get("/api/auth/me").json() == {"auth": False, "user": None}
    # a client can't use headers to make the normal port think it is signed in; it simply has no login
    assert lan.get("/login", follow_redirects=False).status_code in (302, 307)


def test_instance_endpoint_proves_identity_without_leaking_the_id(users, monkeypatch):
    import hashlib, hmac
    monkeypatch.setenv("TUBERIPPER_ALT_URLS", "http://192.168.1.2:8765/, http://100.1.2.3:8765/")
    lan = TestClient(server.app)
    r = lan.get("/api/instance?nonce=abc123").json()
    assert set(r) == {"proof"}  # the normal port never reveals the id
    pub = public()
    assert pub.get("/api/instance").status_code == 401  # public: signed-in only
    login(pub)
    d = pub.get("/api/instance?nonce=abc123").json()
    assert d["id"] and d["urls"] == ["http://192.168.1.2:8765/", "http://100.1.2.3:8765/"]
    assert d["proof"] == r["proof"] == hmac.new(d["id"].encode(), b"abc123", hashlib.sha256).hexdigest()
    assert lan.get("/api/instance?nonce=other").json()["proof"] != r["proof"]  # a replayed answer is useless for a new nonce
