"""Login for the PUBLIC door of TubeRipper.

TubeRipper itself has no login: the normal port (8000, published as 8765) is for the home network.
For use over the internet (through a Tailscale Funnel sidecar) the same app is also served on a second
port, TUBERIPPER_PUBLIC_PORT (see run.py), wrapped in PublicGate below. The decision "is this the public
door?" is made by which port the request arrived on, never by a header a client could forge or omit,
so it fails closed:

  - every request needs a valid session cookie, except the login page and the few static files the
    login page / app manifest need;
  - nothing is served at all until at least one user exists (users.json);
  - destructive or abusable endpoints are blocked (classic UI, deleting/merging library folders,
    downloading an arbitrary URL);
  - login attempts are rate-limited per user and per address, and lock-outs are reported to Discord.

Passwords are stored as scrypt hashes in <cache dir>/users.json; sessions are HMAC-signed cookies
(30 days, renewed while in use) keyed by <cache dir>/session.key. Changing a password signs that user
out everywhere. Manage users with:  python -m tuberipper.auth set <name>   (password read from stdin)
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from urllib.parse import quote

import requests
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

COOKIE_MAX_AGE = 30 * 24 * 3600
RENEW_AFTER = 24 * 3600
MIN_PASSWORD = 12
PREFIX = "/tuberipper"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

router = APIRouter()


# ---------------------------------------------------------------------------
# Users, passwords, session tokens
# ---------------------------------------------------------------------------

def _dir() -> str:
    return os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(os.environ.get("TUBERIPPER_OUTPUT_DIR", "/music"), ".tuberipper")


def _users_path() -> str:
    return os.path.join(_dir(), "users.json")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


# Verifying an unknown user still costs one scrypt, so response time doesn't reveal which names exist.
_DUMMY = hash_password("dummy-password-for-timing")


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except Exception:
        return False


def load_users() -> dict:
    try:
        with open(_users_path(), encoding="utf-8") as f:
            data = json.load(f).get("users", {})
        return {str(k).lower(): v for k, v in data.items() if isinstance(v, str)}
    except (OSError, ValueError, AttributeError):
        return {}


def save_user(name: str, password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters.")
    users = load_users()
    users[name.strip().lower()] = hash_password(password)
    os.makedirs(_dir(), exist_ok=True)
    tmp = _users_path() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"users": users}, f)
    os.replace(tmp, _users_path())


_key_lock = threading.Lock()


def _key() -> bytes:
    path = os.path.join(_dir(), "session.key")
    with _key_lock:
        try:
            with open(path, "rb") as f:
                k = f.read()
            if len(k) >= 32:
                return k
        except OSError:
            pass
        os.makedirs(_dir(), exist_ok=True)
        k = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(k)
        return k


def cookie_name() -> str:
    # Unique per instance: two TubeRipper instances on one hostname (different ports) share cookies,
    # so each must use its own cookie name or logging in to one would sign you out of the other.
    return "tr_" + hashlib.sha256(_key()).hexdigest()[:8]


def _fingerprint(stored: str) -> str:
    return hashlib.sha256(stored.encode()).hexdigest()[:12]


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def make_token(user: str, now: float | None = None) -> str:
    now = now or time.time()
    payload = _b64(json.dumps({"u": user, "iat": int(now), "exp": int(now) + COOKIE_MAX_AGE, "f": _fingerprint(load_users()[user])}).encode())
    sig = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
    return f"v1.{payload}.{sig}"


def check_token(token: str | None, now: float | None = None):
    """-> (user, issued_at) for a valid, unexpired token of a user that still exists with the same
    password; otherwise None."""
    try:
        v, payload, sig = (token or "").split(".")
        if v != "v1" or not hmac.compare_digest(sig, _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        user = data["u"]
        stored = load_users().get(user)
        if not stored or data["f"] != _fingerprint(stored) or (now or time.time()) > data["exp"]:
            return None
        return user, data["iat"]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class LoginLimiter:
    """Too many wrong guesses lock that user name (for everyone) and that address for a while."""
    WINDOW, MAX_USER, MAX_IP, LOCK = 900, 10, 20, 900

    def __init__(self):
        self.fails = {}
        self.lock_until = {}
        self.mutex = threading.Lock()
        self.last_alert = 0.0

    def _recent(self, key, now):
        lst = [t for t in self.fails.get(key, []) if now - t < self.WINDOW]
        self.fails[key] = lst
        return lst

    def wait(self, user: str, ip: str, now: float | None = None) -> int:
        """Seconds until this attempt may be tried, 0 if allowed now."""
        now = now or time.time()
        with self.mutex:
            return int(max([self.lock_until.get(k, 0) - now for k in (("u", user), ("i", ip))] + [0]))

    def fail(self, user: str, ip: str, now: float | None = None) -> bool:
        """Records a failure. Returns True if this one triggered a lock-out."""
        now = now or time.time()
        locked = False
        with self.mutex:
            for key, limit in ((("u", user), self.MAX_USER), (("i", ip), self.MAX_IP)):
                lst = self._recent(key, now)
                lst.append(now)
                if len(lst) >= limit:
                    self.lock_until[key] = now + self.LOCK
                    self.fails[key] = []
                    locked = True
        return locked

    def ok(self, user: str):
        with self.mutex:
            self.fails.pop(("u", user), None)


limiter = LoginLimiter()


def _alert(text: str):
    """Discord message about a lock-out, at most one per hour. Never raises."""
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url or time.time() - limiter.last_alert < 3600:
        return
    limiter.last_alert = time.time()
    try:
        requests.post(url, json={"embeds": [{"title": "TubeRipper: login lock-out", "description": text[:500], "color": 0xE74C3C}]},
                      headers={"User-Agent": "tuberipper"}, timeout=8)
    except requests.RequestException:
        pass


# ---------------------------------------------------------------------------
# Routes (used behind PublicGate; on the normal port they are inert)
# ---------------------------------------------------------------------------

def _is_public(request: Request) -> bool:
    return request.headers.get("x-tr-public") == "1"


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")) or "?"


@router.get("/login")
def login_page(request: Request):
    if not _is_public(request):
        return RedirectResponse(PREFIX + "/" if request.url.path.startswith(PREFIX) else "/")
    return FileResponse(os.path.join(STATIC_DIR, "login.html"), headers={"Cache-Control": "no-store"})


@router.post("/api/auth/login")
async def login(request: Request):
    if not _is_public(request):
        return JSONResponse({"ok": True, "auth": False})
    try:
        body = await request.json()
        name, password = str(body.get("username", "")).strip().lower(), str(body.get("password", ""))
    except Exception:
        return JSONResponse({"detail": "Enter your username and password."}, status_code=400)
    ip = client_ip(request)
    wait = limiter.wait(name, ip)
    if wait:
        return JSONResponse({"detail": f"Too many attempts. Try again in {max(1, wait // 60)} min."}, status_code=429, headers={"Retry-After": str(wait)})
    stored = load_users().get(name)
    good = await asyncio.to_thread(verify_password, password, stored or _DUMMY) and stored is not None
    if not good:
        locked = limiter.fail(name, ip)
        print(f"auth: failed login for {name[:30]!r} from {ip}", file=sys.stderr, flush=True)
        if locked:
            _alert(f"Too many wrong passwords for “{name[:30]}” (last from {ip}). Locked for {limiter.LOCK // 60} minutes.")
        await asyncio.sleep(0.6)  # slows guessing a little more
        return JSONResponse({"detail": "Wrong username or password."}, status_code=401)
    limiter.ok(name)
    resp = JSONResponse({"ok": True})
    set_session_cookie(resp, request, make_token(name))
    return resp


def set_session_cookie(resp, request: Request, token: str):
    secure = os.environ.get("TUBERIPPER_COOKIE_SECURE") == "1" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(cookie_name(), token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax", secure=secure, path="/")


@router.post("/api/auth/logout")
def logout(request: Request):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(cookie_name(), path="/")
    return resp


def _instance_id() -> str:
    """A random id for this install, created once. The Android app learns it over the signed-in public
    address and later uses it to prove that a home/Tailscale address is really this server."""
    path = os.path.join(_dir(), "instance.id")
    try:
        with open(path) as f:
            v = f.read().strip()
        if len(v) >= 32:
            return v
    except OSError:
        pass
    os.makedirs(_dir(), exist_ok=True)
    v = secrets.token_hex(24)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(v)
    return v


@router.get("/api/instance")
def instance(request: Request, nonce: str = ""):
    """?nonce=X -> {"proof": HMAC(instance id, X)}: lets the app check it reached the right server without
    the id crossing the network. Over the public (signed-in) address it also returns the id and the
    server's alternative addresses (TUBERIPPER_ALT_URLS: home / Tailscale), which the app then prefers."""
    out = {}
    if nonce:
        out["proof"] = hmac.new(_instance_id().encode(), nonce[:100].encode(), hashlib.sha256).hexdigest()
    if _is_public(request):
        out["id"] = _instance_id()
        out["urls"] = [u.strip() for u in os.environ.get("TUBERIPPER_ALT_URLS", "").split(",") if u.strip()]
    return out


@router.get("/api/auth/me")
def me(request: Request):
    return {"auth": _is_public(request), "user": request.headers.get("x-tr-user") or None}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

# Reachable without a session: the login page and what it / the installable manifest need.
OPEN_PATHS = {"/login", "/api/auth/login", "/static/app.webmanifest", "/static/icon-192.png", "/static/icon-512.png",
              "/static/apple-touch-icon.png", "/static/favicon-32.png", "/static/icon.svg", "/favicon.ico", "/apple-touch-icon.png"}
# The ordinary app stays available without relaxing any of the above.


def blocked_on_public(path: str, method: str) -> bool:
    """Things that are fine from the home network but too dangerous to offer over the internet."""
    if path == "/classic" or path.startswith("/api/debug"):
        return True
    if method == "DELETE" and path.startswith("/api/library"):
        return True
    if path in ("/api/library/merge", "/api/library/fix-albums", "/api/library/delete", "/api/podcast/delete", "/api/podcast/delete-show"):
        return True
    if path in ("/api/libhealth/fix", "/api/libhealth/restore", "/api/libhealth/trash/empty"):  # moves/removes library files
        return True
    if path == "/setup" or path.startswith("/api/setup"):  # pasting keys/passwords: home network only
        return True
    if path.startswith("/api/library/scan/") and path.endswith("/repair"):
        return True
    if method == "POST" and path == "/api/jobs":  # "download this URL": the server would fetch anything
        return True
    return False


class PublicGate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            return  # no websockets here
        headers = [(k, v) for k, v in scope["headers"] if k not in (b"x-tr-public", b"x-tr-user")]
        hmap = {k.decode("latin-1"): v.decode("latin-1") for k, v in headers}
        path, method = scope["path"], scope["method"]
        p = path[len(PREFIX):] if path.startswith(PREFIX + "/") or path == PREFIX else path
        p = p or "/"

        async def reply(status, body, extra=None, ctype=b"application/json"):
            hdrs = [(b"content-type", ctype), (b"cache-control", b"no-store"), (b"content-length", str(len(body)).encode())] + (extra or [])
            await send({"type": "http.response.start", "status": status, "headers": hdrs + _SECURITY})
            await send({"type": "http.response.body", "body": body})

        if not load_users():
            return await reply(503, b'{"detail":"This server has no users set up yet."}')

        user, issued = None, None
        cookies = dict(c.strip().split("=", 1) for c in hmap.get("cookie", "").split(";") if "=" in c)
        got = check_token(cookies.get(cookie_name()))
        if got:
            user, issued = got

        if p not in OPEN_PATHS and p != "/api/auth/me" and not user:
            if method == "GET" and not p.startswith("/api/") and not p.startswith("/static/"):
                nxt = re.sub(r"[^\w/%.\-?=&]", "", path)
                return await reply(302, b"", [(b"location", (PREFIX if path.startswith(PREFIX) else "").encode() + b"/login?next=" + quote(nxt, safe="/").encode())], b"text/plain")
            return await reply(401, b'{"detail":"Please sign in."}')
        if user and blocked_on_public(p, method):
            return await reply(404, b'{"detail":"Not available here."}')
        if user and method not in ("GET", "HEAD", "OPTIONS"):
            origin = hmap.get("origin")
            if origin and origin.split("://", 1)[-1].split("/")[0] not in (hmap.get("host"), hmap.get("x-forwarded-host")):
                return await reply(403, b'{"detail":"Cross-site request refused."}')

        headers.append((b"x-tr-public", b"1"))
        if user:
            headers.append((b"x-tr-user", user.encode()))
        scope = dict(scope, headers=headers)
        renew = user and issued is not None and time.time() - issued > RENEW_AFTER

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                hdrs = [(k, v) for k, v in message["headers"] if not k.lower().startswith(b"access-control-")]
                hdrs += _SECURITY
                if renew:
                    from starlette.responses import Response
                    tmp = Response()
                    set_session_cookie(tmp, type("R", (), {"headers": hmap})(), make_token(user))
                    hdrs += [(k, v) for k, v in tmp.raw_headers if k == b"set-cookie"]
                message = dict(message, headers=hdrs)
            await send(message)

        await self.app(scope, receive, send_wrapped)


_SECURITY = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"), (b"referrer-policy", b"same-origin")]


# ---------------------------------------------------------------------------
# Command line: python -m tuberipper.auth set <name>   (password on stdin)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "set":
        pw = sys.stdin.read().rstrip("\r\n")
        try:
            save_user(sys.argv[2], pw)
        except ValueError as exc:
            sys.exit(str(exc))
        print(f"saved user {sys.argv[2].lower()} in {_users_path()}")
    elif len(sys.argv) == 2 and sys.argv[1] == "list":
        print("\n".join(sorted(load_users())) or "(no users)")
    else:
        sys.exit("usage: python -m tuberipper.auth set <name>  (password on stdin) | list")
