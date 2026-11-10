"""A page to paste API keys / passwords into, instead of copying them into a file on the server.

Open http://<server>:8765/setup on the home network or Tailscale (the internet-facing port blocks it, auth.py).
Each secret is checked against the service it belongs to before it is saved, saved with owner-only permissions,
and never sent back to the browser. To add another secret, add an entry to SECRETS."""

import html
import os

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import abs as audiobookshelf, recommend
from .library_delete import same_origin

router = APIRouter()


def _check_abs(value: str) -> None:
    try:
        r = requests.get(audiobookshelf._url() + "/api/me", headers={"Authorization": "Bearer " + value}, timeout=10)
    except requests.RequestException:
        raise HTTPException(502, "Couldn't reach Audiobookshelf to check the key, so it wasn't saved.")
    if r.status_code in (401, 403):
        raise HTTPException(400, "Audiobookshelf refused that key. Copy it again from Settings > API Keys (it is shown only once).")
    if r.status_code >= 400:
        raise HTTPException(502, f"Audiobookshelf answered {r.status_code}, so the key wasn't saved.")


def _check_lastfm(value: str) -> None:
    try:
        recommend.check_lastfm_key(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except ConnectionError as exc:
        raise HTTPException(502, str(exc))


def _check_github(value: str) -> None:
    try:
        r = requests.get("https://api.github.com/user", headers={"Authorization": "Bearer " + value.strip(), "Accept": "application/vnd.github+json"}, timeout=10)
    except requests.RequestException:
        raise HTTPException(502, "Couldn't reach GitHub to check the token, so it wasn't saved.")
    if r.status_code in (401, 403):
        raise HTTPException(400, "GitHub refused that token. Copy it again (it is shown only once) and check it has not expired.")
    if r.status_code >= 400:
        raise HTTPException(502, f"GitHub answered {r.status_code}, so the token wasn't saved.")


def _github_path() -> str:
    root = os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(os.environ.get("TUBERIPPER_OUTPUT_DIR", "/music"), ".tuberipper")
    return os.path.join(root, "github-token.txt")


def _abs_path() -> str:
    root = os.environ.get("TUBERIPPER_CACHE_DIR") or os.path.join(os.environ.get("TUBERIPPER_OUTPUT_DIR", "/music"), ".tuberipper")
    return os.environ.get("AUDIOBOOKSHELF_TOKEN_FILE") or os.path.join(root, "abs-token.txt")


# name -> (label, where to get it, file path, check, after-save)
SECRETS = {
    "abs": ("Audiobookshelf API key", "Audiobookshelf > Settings > API Keys > Add (no expiry), then copy the key.",
            _abs_path, _check_abs, audiobookshelf.invalidate),
    "github": ("GitHub token (code backup)", "github.com > Settings > Developer settings > Fine-grained tokens > Generate: only your two private repos, permission Contents = Read and write. Used only to push the TubeRipper code backup.",
               _github_path, _check_github, lambda: None),
    "lastfm": ("Last.fm API key", "last.fm/api/accounts > the \"API key\" (not the shared secret). Used for the \"Because you listened to\" shelves on Home.",
               recommend.key_path, _check_lastfm, lambda: None),
}


def _is_set(name: str) -> bool:
    try:
        return os.path.getsize(SECRETS[name][2]()) > 0
    except OSError:
        return False


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>TubeRipper setup</title>
<style>body{font:16px system-ui;background:#121212;color:#eee;max-width:34rem;margin:2rem auto;padding:0 1rem}
.card{background:#1e1e1e;border-radius:12px;padding:1rem 1.2rem;margin:1rem 0}h1{font-size:1.4rem}h2{font-size:1.1rem;margin:0}
small{color:#aaa}input{width:100%;box-sizing:border-box;padding:.7rem;margin:.6rem 0;border-radius:8px;border:1px solid #444;background:#111;color:#fff;font-size:1rem}
button{padding:.7rem 1.2rem;border:0;border-radius:99px;background:#1db954;color:#000;font-weight:700;font-size:1rem}
.ok{color:#1db954}.bad{color:#ff6b6b}</style>
<h1>TubeRipper setup</h1><small>Paste a key below. It is checked, saved on Titan and never shown again.</small>
%CARDS%
<script>
document.querySelectorAll('form').forEach(f=>f.addEventListener('submit',async e=>{
  e.preventDefault();const msg=f.querySelector('.msg'),inp=f.querySelector('input');msg.className='msg';msg.textContent='Checking…';
  try{const r=await fetch('api/setup/secret',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:f.dataset.name,value:inp.value})});
    const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Error '+r.status);
    inp.value='';msg.className='msg ok';msg.textContent='Saved. It works.';f.closest('.card').querySelector('.state').textContent='saved';
  }catch(x){msg.className='msg bad';msg.textContent=x.message}}));
</script>"""


@router.get("/setup", response_class=HTMLResponse)
def setup_page():
    cards = "".join(
        f'<div class=card><h2>{html.escape(label)} <small class=state>{"saved" if _is_set(n) else "not set"}</small></h2>'
        f'<small>{html.escape(how)}</small><form data-name="{n}" autocomplete=off>'
        f'<input type=password name=value placeholder="Paste it here" autocomplete=off required>'
        f'<button>Save</button> <span class=msg></span></form></div>'
        for n, (label, how, *_rest) in SECRETS.items())
    return HTMLResponse(PAGE.replace("%CARDS%", cards), headers={"Cache-Control": "no-store"})


@router.post("/api/setup/secret")
async def save_secret(request: Request):
    if not same_origin(request):
        raise HTTPException(403, "Cross-site request refused.")
    try:
        b = await request.json()
        name, value = str(b["name"]), str(b["value"]).strip()
    except Exception:
        raise HTTPException(400, "Send {name, value}.")
    if name not in SECRETS or not value or len(value) > 4096 or any(c in value for c in "\r\n\0"):
        raise HTTPException(400, "That doesn't look like a valid key.")
    _label, _how, path_fn, check, after = SECRETS[name]
    check(value)
    path = path_fn()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    os.replace(tmp, path)
    after()
    return {"ok": True}
