"""/api/meta, the X-TR-Shell header and the API contract snapshot."""

import json
import os
import re

from fastapi.testclient import TestClient

from tuberipper import meta, server

client = TestClient(server.app)
CONTRACT = os.path.join(os.path.dirname(__file__), "api_contract.json")


def _write_release(tmp_path, body, apk=True):
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    (cache / "tuberipper.apk.json").write_text(json.dumps(body))
    if apk:
        (cache / "tuberipper.apk").write_bytes(b"PK")


def test_meta_without_a_published_app():
    r = client.get("/api/meta").json()
    assert r == {"api": meta.API_VERSION, "shell": meta.asset_version(), "latest_app": None, "min_app": 0}


def test_meta_reports_the_published_app(tmp_path):
    _write_release(tmp_path, {"versionCode": 9, "versionName": "1.8", "minVersionCode": 3})
    r = client.get("/tuberipper/api/meta").json()
    assert r["latest_app"] == {"versionCode": 9, "versionName": "1.8"} and r["min_app"] == 3


def test_meta_ignores_a_version_file_without_the_apk(tmp_path):
    _write_release(tmp_path, {"versionCode": 9, "versionName": "1.8"}, apk=False)
    assert client.get("/api/meta").json()["latest_app"] is None


def test_meta_survives_a_broken_version_file(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "tuberipper.apk.json").write_text("{not json")
    r = client.get("/api/meta").json()
    assert r["latest_app"] is None and r["min_app"] == 0


def test_every_answer_names_the_shell_version_and_it_matches_the_page():
    r = client.get("/")
    v = re.search(r"app\.js\?v=(\d+)", r.text).group(1)
    assert r.headers["x-tr-shell"] == v == meta.asset_version()
    assert client.get("/api/health").headers["x-tr-shell"] == v
    # the header must not break streamed / ranged static answers
    rr = client.get("/static/app.js?v=1", headers={"Range": "bytes=0-9", "Accept-Encoding": "identity"})
    assert rr.status_code == 206 and rr.headers["x-tr-shell"] == v


def test_shell_header_is_readable_cross_origin():
    r = client.get("/api/meta", headers={"Origin": "http://192.0.2.1:8765"})
    assert "x-tr-shell" in r.headers.get("access-control-expose-headers", "").lower()


def test_page_reloads_itself_only_for_a_new_version():
    js = open(os.path.join(meta.STATIC_DIR, "app.js"), encoding="utf-8").read()
    assert "noteShell(r.headers.get('x-tr-shell'))" in js and "tr.reloadedFor" in js


# ---------- API contract ----------
# tests/api_contract.json lists every route the page/app may call with its required parameters. Removing a route
# or adding a required parameter breaks old pages and old APKs, so it fails here until the snapshot is updated
# on purpose (and API_VERSION raised if the change is incompatible). New routes and optional parameters are fine.
# Update after a reviewed change:  TR_UPDATE_CONTRACT=1 python -m pytest tests/test_meta.py

def _current_contract():
    spec = server.app.openapi()
    out = {}
    for path, ops in spec["paths"].items():
        if path.startswith("/tuberipper/"):
            continue  # the same routes again under the prefix
        for method, op in ops.items():
            required = sorted(p["name"] for p in op.get("parameters", []) if p.get("required") and p.get("in") != "path")
            body = op.get("requestBody", {}).get("required", False)
            out[f"{method.upper()} {path}"] = {"required": required, "body": bool(body)}
    return dict(sorted(out.items()))


def test_api_contract_has_no_breaking_change():
    now = _current_contract()
    if os.environ.get("TR_UPDATE_CONTRACT"):
        with open(CONTRACT, "w", encoding="utf-8") as f:
            json.dump(now, f, indent=1)
            f.write("\n")
    with open(CONTRACT, encoding="utf-8") as f:
        snap = json.load(f)
    removed = [k for k in snap if k not in now]
    stricter = [k for k in snap if k in now and (set(now[k]["required"]) - set(snap[k]["required"]) or (now[k]["body"] and not snap[k]["body"]))]
    assert not removed, f"routes removed (old pages/apps still call them): {removed}"
    assert not stricter, f"routes now require more from callers: {stricter}"
