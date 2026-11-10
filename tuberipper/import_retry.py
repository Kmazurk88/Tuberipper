"""A second pass over songs a playlist import couldn't place: find them again (with the importer's current, looser
matching) and put each one into the playlist(s) it came from.

    python -m tuberipper.import_retry <songs.json> [--dry] [--library-only]

songs.json: [{"song": "Artist, Artist - Title", "playlists": ["Playlist name as in Navidrome", ...]}, ...]
For every song: use the copy already in the library if there is one, otherwise download the best catalog match; then add
it to each of its playlists (never twice). Prints one line per song and a JSON summary. --dry only looks;
--library-only never downloads (it only puts songs the library already has into their playlists).
An item may also give "artists": [...] and "title" instead of parsing "song"."""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from . import navidrome
from . import spotify_import as si

WORKERS = 4


def parse(song: str):
    """"A, B - Title - extra" -> (["A", "B"], "Title - extra"). Spotify joins several artists with ", "."""
    artists_part, _, title = song.partition(" - ")
    artists = [a.strip() for a in artists_part.replace(";", ",").split(",") if a.strip()] or [artists_part.strip()]
    return artists, (title or artists_part).strip()


def recent_id(title, hours=4):
    """A song with this title added to the library in the last few hours, when it is the only one: a download whose
    artist was tagged differently ("Jackson 5" saved as "The Jacksons") can't be found by artist."""
    import datetime
    q = " ".join(re.sub(r"[^\w\s]+", " ", si._clean(title)).split())
    songs = navidrome._call("search3", [("query", q), ("songCount", "30"), ("artistCount", "0"), ("albumCount", "0")]).get("searchResult3", {}).get("song") or []
    songs = songs if isinstance(songs, list) else [songs]
    now, key, hits = datetime.datetime.now(datetime.timezone.utc), si._norm(si._clean(title)), []
    for s in songs:
        try:
            made = datetime.datetime.fromisoformat(s["created"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if si._norm(si._clean(s.get("title", ""))) == key and (now - made).total_seconds() < hours * 3600:
            hits.append(s["id"])
    return hits[0] if len(hits) == 1 else None


def library_id(artists, title):
    return si.library_id(artists, title) or recent_id(title)


def resolve_playlist(name: str, playlists: list):
    key = si._norm(name)
    return next((p for p in playlists if si._norm(p["name"]) == key), None)


def process(item: dict, playlists: list, dry: bool, library_only: bool = False) -> dict:
    artists, title = (item["artists"], item["title"]) if item.get("artists") else parse(item["song"])
    out = {"song": item["song"], "playlists": item["playlists"], "result": "", "detail": ""}
    targets = []
    for name in item["playlists"]:
        p = resolve_playlist(name, playlists)
        if p:
            targets.append(p)
        else:
            out["detail"] += f"no playlist called {name!r}; "
    sid, how = library_id(artists, title), "library"
    cand = None
    if not sid:
        if library_only:
            out["result"] = "not in library"
            return out
        cand = si._find(artists, title, 0)
        if not cand:
            out["result"] = "not found"
            return out
        if cand.get("in_library"):
            sid = library_id([cand["artist"]] + artists, cand["title"])
        how = f"{cand['artist']} - {cand['title']}" + (f" [{cand.get('album') or ''}]" if cand.get("album") else "")
        if not sid and dry:
            out.update(result="would download", detail=how)
            return out
    if not sid:
        meta = {k: cand.get(k) for k in ("title", "artist", "album", "duration", "explicit", "video_id", "artwork", "year")}
        r = requests.post(f"{si.BASE}/api/jobs/catalog", json={"song": meta, "add": {"playlist_mode": "none", "notify": False}}, timeout=60)
        r.raise_for_status()
        job = si._wait_for(r.json()["id"])
        tr = (job.get("tracks") or [{}])[0]
        if tr.get("status") not in ("done", "skipped_exists"):
            out.update(result="failed", detail=tr.get("detail") or job.get("error") or "needs a manual look")
            return out
        out.update(result="downloaded", detail=how, _after=(artists, cand))   # placed in its playlists after one scan for all downloads
        return out
    added = 0
    for p in targets:
        if dry:
            added += sid not in navidrome.playlist_song_ids(p["id"])          # what a real run would add
            continue
        added += navidrome.add_to_playlist(p["id"], [sid])
    out["song_id"] = sid
    out["to_playlists"] = [p["name"] for p in targets]
    out["added_to_playlists"] = added
    if how == "library":
        out.update(result=("would add to playlist" if dry else "added to playlist") if added else "already in playlist", detail="already in the library")
    else:
        out.update(result="added", detail=how)
    return out


def place(out: dict, playlists: list) -> dict:
    """After the scan: find a downloaded song in Navidrome and add it to its playlists."""
    artists, cand = out.pop("_after")
    sid = None
    for _ in range(6):                                         # the scan can lag a few seconds behind the download
        sid = library_id([cand["artist"]] + artists, cand["title"])
        if sid:
            break
        time.sleep(5)
    if not sid:
        out["result"] = "downloaded, not in Navidrome yet"
        return out
    targets = [resolve_playlist(n, playlists) for n in out["playlists"]]
    added = sum(navidrome.add_to_playlist(p["id"], [sid]) for p in targets if p)
    out.update(result="added", detail="downloaded: " + out["detail"], to_playlists=[p["name"] for p in targets if p], added_to_playlists=added)
    return out


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    items = json.load(open(argv[1]))
    dry, library_only = "--dry" in argv, "--library-only" in argv
    playlists = navidrome.list_playlists()
    def one(item):
        try:
            return process(item, playlists, dry, library_only)
        except Exception as exc:
            return {"song": item["song"], "playlists": item["playlists"], "result": "error", "detail": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:          # a few songs at a time, like the importer
        results = list(pool.map(one, items))
    pending = [r for r in results if r.get("_after")]
    if pending:
        navidrome.wait_for_scan()                                  # one scan for everything that was downloaded
        for r in pending:
            try:
                place(r, playlists)
            except Exception as exc:
                r.pop("_after", None)
                r.update(result="error", detail=f"{type(exc).__name__}: {exc}")
    for r in results:
        print(f"{r['result']:>34} | {r['song']}  ->  {r.get('detail', '')}", flush=True)
    summary = {}
    for r in results:
        summary[r["result"]] = summary.get(r["result"], 0) + 1
    print(json.dumps(summary), flush=True)
    json.dump(results, open(argv[1].replace(".json", "-results.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv)
