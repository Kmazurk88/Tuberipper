"""Extra podcast information for the player's podcast screen.

Navidrome doesn't know an episode's real release date (the files carry it as an RFC 822 string in a
custom tag), nor the show's description. This reads the dates straight from the podcast files
(TUBERIPPER_PODCASTS_DIR, mounted read-only) and, for the About tab, asks iTunes for the show and reads its
public RSS feed (only to public addresses). Everything is cached."""

import ipaddress
import os
import socket
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urljoin

import requests
from fastapi import APIRouter, HTTPException

from . import diskcache, navidrome
from .discover import _norm, pod_norm

router = APIRouter()

_lock = threading.Lock()
_files = {}  # path -> (mtime, info)


def _dir() -> str:
    return os.environ.get("TUBERIPPER_PODCASTS_DIR", "/podcasts")


def _parse_date(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _read(path: str) -> dict | None:
    from mutagen import File
    try:
        a = File(path)
        tags = getattr(a, "tags", None)
        if not tags:
            return None
        title, date, itunes = "", "", ""
        for k, v in tags.items():
            lk = str(k).lower()
            val = str(v.text[0]) if hasattr(v, "text") and v.text else (str(v[0]) if isinstance(v, list) and v else str(v))
            if lk == "tit2" or lk == "\xa9nam":
                title = val
            elif lk == "txxx:releasedate":
                date = val
            elif lk in ("tdrc", "\xa9day") and not date:
                date = val
            elif lk == "txxx:itunes-id":
                itunes = val
        dt = _parse_date(date)
        return {"title": title, "ts": int(dt.timestamp()) if dt else None, "date": dt.date().isoformat() if dt else None, "itunes": itunes}
    except Exception:
        return None


def _index() -> dict:
    """normalised title -> {ts, date, itunes} for every episode file."""
    root = _dir()
    idx = {}
    if not os.path.isdir(root):
        return idx
    with _lock:
        for folder, _, names in os.walk(root):
            for n in names:
                if not n.lower().endswith((".mp3", ".m4a", ".m4b", ".opus", ".ogg")):
                    continue
                p = os.path.join(folder, n)
                try:
                    m = os.path.getmtime(p)
                except OSError:
                    continue
                cached = _files.get(p)
                if not cached or cached[0] != m:
                    cached = (m, _read(p))
                    _files[p] = cached
                info = cached[1]
                if info and info["title"]:
                    idx.setdefault(pod_norm(info["title"]), info)
    return idx


def _album_songs(album_id: str) -> tuple:
    if not navidrome.configured():
        raise HTTPException(503, "Navidrome isn't set up.")
    try:
        al = navidrome._call("getAlbum", [("id", album_id)]).get("album", {})
    except navidrome.NavidromeError as exc:
        raise HTTPException(502, str(exc))
    songs = al.get("song") or []
    return al, songs if isinstance(songs, list) else [songs]


@router.get("/api/podcast/dates")
def episode_dates(album: str):
    """{song id: {date: "2025-12-16", ts: epoch}} for the episodes of one show."""
    _, songs = _album_songs(album)
    idx = _index()
    out = {}
    for s in songs:
        info = idx.get(pod_norm(s.get("title", "")))
        if info and info["ts"]:
            out[s["id"]] = {"date": info["date"], "ts": info["ts"]}
    return {"dates": out}


# ---------- About ----------

def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return bool(infos)


def _safe_get(url: str, hops: int = 3):
    """GET that only ever talks to public internet addresses (the feed address comes from a third party)."""
    for _ in range(hops + 1):
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname or not _public_host(u.hostname):
            return None
        r = requests.get(url, timeout=8, stream=True, allow_redirects=False, headers={"User-Agent": "tuberipper/1.0"})
        if r.is_redirect:
            url = urljoin(url, r.headers.get("location", ""))
            r.close()
            continue
        return r
    return None


def _feed_info(feed_url: str) -> dict:
    r = _safe_get(feed_url)
    if r is None or r.status_code != 200:
        return {}
    data = b""
    for chunk in r.iter_content(65536):
        data += chunk
        if len(data) > 2_000_000:
            break
    r.close()
    try:
        # Only the channel header is needed; stop parsing at the first <item>.
        head = data.split(b"<item", 1)[0] + b"</channel></rss>"
        root = ET.fromstring(head)
    except ET.ParseError:
        return {}
    ch = root.find("channel")
    if ch is None:
        return {}
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    desc = (ch.findtext("description") or ch.findtext("itunes:summary", namespaces=ns) or "").strip()
    return {"description": desc, "link": (ch.findtext("link") or "").strip()}


@router.get("/api/podcast/about")
def about(album: str):
    al, songs = _album_songs(album)
    key = "pod:about:" + album
    hit = diskcache.get(key)
    if hit is not None:
        return hit
    idx = _index()
    itunes_id = next((idx[pod_norm(s.get("title", ""))]["itunes"] for s in songs if idx.get(pod_norm(s.get("title", ""))) and idx[pod_norm(s.get("title", ""))]["itunes"]), "")
    ts = sorted(i["ts"] for s in songs for i in [idx.get(pod_norm(s.get("title", "")))] if i and i["ts"])
    out = {"name": al.get("name", ""), "publisher": al.get("artist", ""), "episodes": len(songs),
           "genres": [g.get("name") for g in al.get("genres", []) if g.get("name") and g["name"] != "Podcasts"],
           "first_ts": ts[0] if ts else None, "latest_ts": ts[-1] if ts else None, "description": "", "link": "", "source": ""}
    if itunes_id.isdigit():
        try:
            r = requests.get("https://itunes.apple.com/lookup", params={"id": itunes_id}, timeout=8, headers={"User-Agent": "tuberipper/1.0"})
            res = (r.json().get("results") or [{}])[0] if r.status_code == 200 else {}
            if res.get("genres"):
                out["genres"] = [g for g in res["genres"] if g != "Podcasts"]
            if res.get("artistName"):
                out["publisher"] = res["artistName"]
            if res.get("feedUrl"):
                feed = _feed_info(res["feedUrl"])
                if feed.get("description"):
                    out.update(feed, source="the show's feed")
        except Exception:
            return out  # a network hiccup: answer with what we have and don't cache it
    diskcache.put(key, out)
    return out
