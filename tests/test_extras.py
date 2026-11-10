"""Radio, Daily Mixes / Discover Weekly, listening stats and library health, with every outside service faked."""

import os
import time

import pytest
from fastapi.testclient import TestClient

from tuberipper import catalog, config, libhealth, library_index, mixes, radio, recommend, server, stats

THUMBS = [{"url": "https://x/y=w60-h60-l90-rj"}]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEFAULT_OUTPUT_DIR", str(tmp_path / "music"))
    os.makedirs(tmp_path / "music")
    stats.reset()
    monkeypatch.setattr(recommend, "_state", {"data": None, "built": 0.0, "building": False})
    yield TestClient(server.app)
    stats.reset()


# ---- radio ----------------------------------------------------------------
class RadioYTM:
    def get_watch_playlist(self, videoId=None, radio=False, limit=None):
        assert radio
        t = lambda vid, title, artist, typ="MUSIC_VIDEO_TYPE_ATV": {"videoId": vid, "title": title, "artists": [{"name": artist}], "length": "3:20",
                                                                      "thumbnail": THUMBS, "videoType": typ, "album": {"name": "A"}}
        return {"tracks": [t("seed0000001", "Seed Song", "Seed Artist"), t("aaaaaaaaaaa", "Owned One", "Band"), t("bbbbbbbbbbb", "New One", "Other"),
                           t("ccccccccccc", "Random Upload", "Nobody", "MUSIC_VIDEO_TYPE_UGC"), t("ddddddddddd", "New One", "Other")]}


def test_radio_marks_owned_songs_and_skips_seed_dupes_and_uploads(client, monkeypatch):
    monkeypatch.setattr(catalog, "ytm", lambda: RadioYTM())
    monkeypatch.setattr(radio.library_index, "has_song", lambda root, a, t: t == "Owned One")
    monkeypatch.setattr(radio, "_cache", {})
    monkeypatch.setattr(radio.navidrome, "_call", lambda ep, params=None, timeout=15: {"searchResult3": {"song": [
        {"id": "n1", "title": "Owned One", "artist": "Band", "album": "A", "coverArt": "al-1", "duration": 200, "size": 5}]}})
    d = client.get("/api/radio?video_id=seed0000001").json()
    assert [s["title"] for s in d["songs"]] == ["Owned One", "New One"]
    owned, new = d["songs"]
    assert owned["in_library"] and owned["lib"]["id"] == "n1" and "size" not in owned["lib"]
    assert not new["in_library"] and new["lib"] is None and new["video_id"] == "bbbbbbbbbbb" and new["duration"] == 200


def test_radio_needs_a_findable_seed(client, monkeypatch):
    monkeypatch.setattr(radio, "seed_video", lambda *a, **k: None)
    assert client.get("/api/radio?artist=Nobody&title=Nothing").status_code == 404


# ---- mixes -----------------------------------------------------------------
def _songs(prefix, artists, n):
    return [{"id": f"{prefix}{i}", "title": f"{prefix} song {i}", "artist": artists[i % len(artists)], "album": "X", "albumId": "a", "artistId": "b",
             "coverArt": f"al-{i}", "duration": 200, "size": 1} for i in range(n)]


def _nd(endpoint, params=None, timeout=15):
    params = dict(params or [])
    if endpoint == "getMusicFolders":
        return {"musicFolders": {"musicFolder": [{"id": "1", "name": "Music"}]}}
    if endpoint == "getGenres":
        return {"genres": {"genre": [{"value": "Hard Rock", "songCount": 30}, {"value": "Rock", "songCount": 30}, {"value": "Rap", "songCount": 14},
                                     {"value": "Podcast", "songCount": 99}, {"value": "Jazz", "songCount": 3}]}}
    if endpoint == "getSongsByGenre":
        return {"songsByGenre": {"song": {"Hard Rock": _songs("h", ["AC/DC", "Sabbath", "Maiden", "Priest", "Motorhead"], 30),
                                          "Rock": _songs("r", ["Zeppelin"], 30), "Rap": _songs("p", ["Eminem", "Jay", "Nas"], 14),
                                          "Jazz": _songs("j", ["Miles"], 3)}[params["genre"]]}}
    if endpoint == "getAlbumList2":
        return {"albumList2": {"album": [{"artist": "AC/DC", "playCount": 5}]}}
    if endpoint == "getStarred2":
        return {"starred2": {"song": [{"id": "p0"}]}}
    raise AssertionError(endpoint)


def test_daily_mixes_group_by_kind_and_cap_per_artist(client, monkeypatch):
    monkeypatch.setattr(mixes.navidrome, "_call", _nd)
    monkeypatch.setattr(recommend.navidrome, "_call", _nd)
    monkeypatch.setattr(mixes.library_index, "has_song", lambda root, a, t: False)
    monkeypatch.setattr(mixes, "_day", {"key": None, "data": None})
    d = client.get("/api/mixes").json()
    fams = [m["family"] for m in d["mixes"]]
    assert set(fams) == {"Metal & Hard Rock", "Rock", "Hip-Hop & Rap"}          # podcasts skipped, 3-song Jazz too small
    assert [m["title"] for m in d["mixes"]] == ["Daily Mix 1", "Daily Mix 2", "Daily Mix 3"]
    rock = next(m for m in d["mixes"] if m["family"] == "Rock")
    assert len(rock["songs"]) == 3 and all(s["kind"] == "lib" for s in rock["songs"])      # one artist: at most 3 songs
    metal = next(m for m in d["mixes"] if m["family"] == "Metal & Hard Rock")
    assert len(metal["songs"]) == 15 and metal["sub"].endswith("and more") and len(metal["covers"]) == 4
    again = client.get("/api/mixes").json()
    assert [s["id"] for m in again["mixes"] for s in m["songs"]] == [s["id"] for m in d["mixes"] for s in m["songs"]]   # stable within a day


def test_discover_weekly_is_kept_for_the_week_and_drops_added_songs(client, monkeypatch):
    monkeypatch.setattr(mixes.navidrome, "_call", _nd)
    monkeypatch.setattr(recommend.navidrome, "_call", _nd)
    monkeypatch.setattr(mixes.library_index, "has_song", lambda root, a, t: t == "T0")
    monkeypatch.setattr(mixes, "_day", {"key": None, "data": None})
    shelf = lambda sid, n: {"id": sid, "title": sid, "songs": [{"title": f"T{i}", "artist": f"{sid}{i}", "video_id": f"{sid}{i:08d}"[:11], "because": "x"} for i in range(n)]}
    recommend._state["data"] = {"shelves": [shelf("discover", 10), shelf("because:a", 10)]}
    d = client.get("/api/mixes").json()
    assert d["weekly"]["title"] == "Discover Weekly" and len(d["weekly"]["songs"]) == 18          # T0 is owned, 20 - 2
    assert os.path.exists(mixes._cache_file())
    recommend._state["data"] = {"shelves": []}                                                  # later in the week: same list
    assert len(client.get("/api/mixes").json()["weekly"]["songs"]) == 18


# ---- stats ------------------------------------------------------------------
def play(uid, ts, title="Song", artist="Art", album="Alb", dur=180):
    return {"uid": uid, "ts": ts, "title": title, "artist": artist, "album": album, "duration": dur, "nd_id": "n" + title, "cover": "c-" + artist}


def test_stats_count_plays_hours_streaks(client):
    base = time.mktime((2026, 11, 2, 23, 30, 0, 0, 0, 0)) - time.timezone      # a Monday, 23:30 UTC
    for i, (title, artist) in enumerate([("A", "X"), ("A", "X"), ("B", "X"), ("C", "Y")]):
        assert client.post("/api/stats/play", json=play(f"u{i}", base + i * 60, title, artist)).json()["new"]
    assert not client.post("/api/stats/play", json=play("u0", base)).json()["new"]                  # same play twice counts once
    client.post("/api/stats/play", json=play("u9", base + 86400, "A", "X"))                       # next day
    client.post("/api/stats/play", json=play("u10", base + 86400 * 3, "A", "X"))                  # a gap
    d = client.get("/api/stats?period=all&tz=0").json()
    assert d["plays"] == 6 and d["minutes"] == 18 and d["unique_artists"] == 2 and d["unique_songs"] == 3
    assert d["top_artists"][0]["name"] == "X" and d["top_artists"][0]["plays"] == 5
    assert d["top_songs"][0]["title"] == "A" and d["top_songs"][0]["plays"] == 4
    assert d["by_hour"][23] == 6 and d["persona"] == "Night owl"
    assert d["longest_streak"] == 2 and d["busiest_day"]["plays"] == 4
    west = client.get("/api/stats?period=all&tz=300").json()                                      # UTC-5: 23:30 UTC is 18:30 there
    assert west["by_hour"][18] == 6
    assert client.get("/api/stats?period=decade").status_code == 400


def test_stats_period_excludes_old_plays(client):
    now = time.time()
    client.post("/api/stats/play", json=play("old", now - 40 * 86400))
    client.post("/api/stats/play", json=play("new", now - 3600))
    assert client.get("/api/stats?period=week").json()["plays"] == 1
    assert client.get("/api/stats?period=month").json()["plays"] == 1
    assert client.get("/api/stats?period=all").json()["plays"] == 2


# ---- library health -----------------------------------------------------------
def make(root, rel, data=b"x" * 10):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(data)


def test_library_health_scan_fix_and_restore(client, monkeypatch):
    root = config.DEFAULT_OUTPUT_DIR
    make(root, "Band/Album/1 - Song.m4a")
    make(root, "Band/Greatest Hits/2 - Song.m4a")
    make(root, "Band/Album/2 - Broken.m4a", b"")
    make(root, "Band/Album/3 - Quiet.mp3")
    make(root, "Band/Album/3 - Quiet.lrc", b"[00:00.00]hi")
    make(root, "Band/Other Album/4 - Skit.m4a")                                      # same title on a different album: left alone
    make(root, "Band/Album/4 - Skit.m4a")
    make(root, "Band/Album/5 - Remix.m4a")                                           # same album, same title, but 40 s longer: not a copy
    make(root, "Band/Album/5 - Remix.mp3")
    make(root, "Band/Album (1999)/1 - Song.flac")                                    # the same album under a second folder name
    make(root, "Loose/1 - Take.flac"); make(root, "Loose/2 - Take (Alt).m4a")        # loose files, no album folder: never offered for removal
    info = {"Band/Album/1 - Song.m4a": dict(ok=True, bitrate=256, length=200.0, cover=True), "Band/Greatest Hits/2 - Song.m4a": dict(ok=True, bitrate=256, length=200.0, cover=False),
            "Band/Album/3 - Quiet.mp3": dict(ok=True, bitrate=64, length=200.0, cover=True),
            "Band/Other Album/4 - Skit.m4a": dict(ok=True, bitrate=256, length=60.0, cover=True), "Band/Album/4 - Skit.m4a": dict(ok=True, bitrate=256, length=75.0, cover=True),
            "Band/Album (1999)/1 - Song.flac": dict(ok=True, bitrate=900, length=200.0, cover=True),
            "Loose/1 - Take.flac": dict(ok=True, bitrate=900, length=200.0, cover=True), "Loose/2 - Take (Alt).m4a": dict(ok=True, bitrate=256, length=200.0, cover=True),
            "Band/Album/5 - Remix.m4a": dict(ok=True, bitrate=256, length=200.0, cover=True), "Band/Album/5 - Remix.mp3": dict(ok=True, bitrate=192, length=240.0, cover=True)}
    monkeypatch.setattr(libhealth, "_inspect", lambda p: dict(size=os.path.getsize(p), **info.get(os.path.relpath(p, root).replace(os.sep, "/"), dict(ok=False, bitrate=0, length=0.0, cover=False))))
    monkeypatch.setattr(libhealth, "_after_change", lambda: library_index.invalidate())
    job = client.post("/api/libhealth/scan").json()
    for _ in range(50):
        d = client.get(f"/api/libhealth/scan/{job['id']}").json()
        if d["status"] != "scanning":
            break
        time.sleep(0.1)
    assert d["status"] == "done", d
    sm = d["summary"]
    assert sm["bytes"] > 0 and sm["podcast_bytes"] >= 0
    assert (sm["duplicate_groups"], sm["duplicate_files"], sm["unreadable"], sm["no_cover"], sm["low_quality"], sm["several_albums"]) == (1, 1, 1, 1, 1, 4)
    assert d["duplicates"][0]["keep"]["album"] == "Album (1999)"                     # the lossless copy is kept; the compilation copy is the one that goes
    assert [r["album"] for r in d["duplicates"][0]["remove"]] == ["Greatest Hits"]   # the m4a on the plain album and loose files are left alone
    assert sm["twin_albums"] == 1 and d["twin_albums"][0]["albums"][0]["album"] == "Album" and d["twin_albums"][0]["albums"][1]["formats"] == ["flac"]
    assert d["no_cover"] == [{"artist": "Band", "album": "Greatest Hits", "songs": 1}]
    r = client.post("/api/libhealth/fix", json={"job": job["id"], "kind": "duplicates"}).json()
    assert r["moved"] == 1 and not os.path.exists(os.path.join(root, "Band/Greatest Hits/2 - Song.m4a"))
    assert os.path.exists(os.path.join(root, "Band/Album/1 - Song.m4a"))
    assert not os.path.exists(os.path.join(root, "Band", "Greatest Hits"))             # emptied folder is tidied
    trash = client.get("/api/libhealth/trash").json()["batches"]
    assert len(trash) == 1 and trash[0]["songs"] == 1
    back = client.post("/api/libhealth/restore", json={"batch": trash[0]["batch"]}).json()
    assert back["restored"] == 1 and os.path.exists(os.path.join(root, "Band/Greatest Hits/2 - Song.m4a"))
    assert client.get("/api/libhealth/trash").json()["batches"] == []
    assert client.post("/api/libhealth/restore", json={"batch": "../../etc"}).status_code == 404


def test_health_fixes_are_blocked_on_the_public_port():
    from tuberipper import auth
    for p in ("/api/libhealth/fix", "/api/libhealth/restore", "/api/libhealth/trash/empty"):
        assert auth.blocked_on_public(p, "POST")
    assert not auth.blocked_on_public("/api/libhealth/scan", "POST")


def test_podcasts_and_sleep_sounds_are_not_counted(client):
    assert not client.post("/api/stats/play", json=play("p1", time.time(), dur=3600)).json()["new"]                        # an hour-long episode
    assert not client.post("/api/stats/play", json=dict(play("p2", time.time()), artist="White Noise Masters", album="Rain")).json()["new"]
    assert client.post("/api/stats/play", json=play("p3", time.time())).json()["new"]
    assert client.get("/api/stats?period=all").json()["plays"] == 1


# ---- importer matching (why well-known songs were "not found") --------------------------------------------------
def test_importer_accepts_the_same_artist_written_differently():
    from tuberipper import spotify_import as si
    assert si.artist_ok("The Jackson 5", "Jackson 5") and si.artist_ok("Daryl Hall & John Oates", "Daryl Hall, John Oates")
    assert si.artist_ok("TOTO", "Toto") and si.artist_ok("Joan Jett & the Blackhearts", "Joan Jett and the Blackhearts")
    assert not si.artist_ok("Toto", "Martin Miller") and not si.artist_ok("The Who", "Wham!")


def test_importer_ignores_which_release_a_title_names():
    from tuberipper import spotify_import as si
    for raw in ['Lose Yourself - From "8 Mile" Soundtrack', "Layla - 40th Anniversary Version / 2010 Remastered", "Shotgun - Extended Stereo Mix 2005",
                "Mr. Sandman - 1954 #1 Billboard chart hit", "Sara - Single; 2018 Remaster", "Baby Love - Juke Box Single Version"]:
        assert " - " not in si._clean(raw), raw
    assert si._clean("Blue - Studio Live") == "Blue - Studio Live"                    # live is a different recording
    cand = {"title": "Lose Yourself", "artist": "Eminem", "duration": 326}
    assert si._score(cand, ["Eminem"], 'Lose Yourself - From "8 Mile" Soundtrack', 326) > 0.9
    assert si._score({"title": "Africa [Karaoke Version]", "artist": "Karaoke Channel Toto", "duration": 300}, ["Toto"], "Africa", 300) is None


def test_importer_retries_an_empty_search(monkeypatch):
    from tuberipper import spotify_import as si
    calls = []
    class R:
        def __init__(self, songs): self.songs = songs
        def raise_for_status(self): pass
        def json(self): return {"songs": self.songs}
    monkeypatch.setattr(si.requests, "get", lambda *a, **k: (calls.append(1), R([] if len(calls) < 3 else [{"title": "Africa", "artist": "Toto", "duration": 295}]))[1])
    monkeypatch.setattr(si.time, "sleep", lambda s: None)
    assert si._find(["TOTO"], "Africa", 295)["title"] == "Africa" and len(calls) == 3


def test_retry_tool_splits_artists_and_titles():
    from tuberipper import import_retry as ir
    assert ir.parse("Daryl Hall & John Oates - Maneater") == (["Daryl Hall & John Oates"], "Maneater")
    assert ir.parse("Mellow In Blue, Mathias Heise - In A Sentimental Mood") == (["Mellow In Blue", "Mathias Heise"], "In A Sentimental Mood")
    assert ir.parse("Eminem - Lose Yourself - From \"8 Mile\" Soundtrack")[1] == 'Lose Yourself - From "8 Mile" Soundtrack'


def test_playlist_matching_finds_songs_the_old_exact_matcher_missed(monkeypatch):
    from tuberipper import spotify_import as si
    library = {("Pink Floyd", "Wish You Were Here"): "n1", ("The Rolling Stones", "Paint It, Black"): "n2", ("Led Zeppelin", "Stairway to Heaven"): "n3",
               ("Prince", "When Doves Cry"): "n4"}
    def fake(artist, title):
        for (a, t), sid in library.items():
            if si._norm(a) == si._norm(artist) and si._norm(t) == si._norm(title):
                return {"id": sid}
    monkeypatch.setattr(si.radio, "library_song", fake)
    monkeypatch.setattr(si.navidrome, "find_song_id", lambda meta: None)
    monkeypatch.setattr(si.time, "sleep", lambda s: None)
    entries = [(["The Rolling Stones"], "Paint It Black", {"title": "Paint It Black", "artist": "The Rolling Stones"}),
               (["Led Zeppelin"], "Stairway to Heaven - Remaster", {"title": "Stairway to Heaven (Remaster)", "artist": "Led Zeppelin"}),
               (["Prince"], "When Doves Cry - 2015 Paisley Park Remaster", {"title": "When Doves Cry (2015 Paisley Park Remaster)", "artist": "Prince & The Revolution"}),
               (["Pink Floyd"], "Wish You Were Here", {"title": "Wish You Were Here", "artist": "Pink Floyd"}),
               (["Nobody"], "Not In The Library", {"title": "Not In The Library", "artist": "Nobody"})]
    ids, missing = si._playlist_ids(entries)
    assert ids == ["n2", "n3", "n4", "n1"] and missing == 1


def test_library_lookup_survives_symbols_and_artist_spellings(client, monkeypatch):
    seen = []
    def nd(ep, params=None, timeout=15):
        seen.append(dict(params)["query"])
        return {"searchResult3": {"song": [{"id": "n1", "title": "Layla", "artist": "Derek and the Dominos", "album": "A"}]}}
    monkeypatch.setattr(radio.navidrome, "_call", nd)
    assert radio.library_song("Derek & The Dominos", "Layla")["id"] == "n1"
    assert "&" not in seen[0] and "(" not in seen[0]
    seen.clear(); radio.library_song("Jack", "Song (Live at X, NY - 1990)")
    assert seen[0] == "Song Live at X NY 1990 Jack"


# ---- finder improvements --------------------------------------------------------------------------------------
def test_live_picks_keep_their_own_album_instead_of_the_studio_song():
    live = {"confidence": "catalog", "artist": "Billy Joel", "title": "Allentown (Live at Yankee Stadium, Bronx, NY - June 1990)",
            "target_duration": 236, "catalog": {"album": "Live at Yankee Stadium", "year": "1990", "artwork": "u"}}
    m = server._alt_catalog_match(live)
    assert m["album"] == "Live at Yankee Stadium" and m["title"].startswith("Allentown (Live") and m["year"] == "1990"
    assert server._alt_catalog_match(dict(live, title="Allentown")) is None                    # a studio song still goes through the lookup
    assert server._alt_catalog_match(dict(live, confidence="parsed")) is None


def test_age_restricted_download_falls_back_to_another_upload(monkeypatch):
    calls = []
    def fake_download(video, work_dir):
        calls.append(video["id"])
        if video["id"] in ("restricted1", "restricted2"):
            raise RuntimeError("ERROR: Sign in to confirm your age. This video may be inappropriate for some users.")
        return "/tmp/ok.m4a"
    monkeypatch.setattr(server.downloader, "download_audio", fake_download)
    monkeypatch.setattr(server.sourcing, "audio_candidates", lambda *a, **k: [
        {"video_id": "restricted1", "url": "u1"}, {"video_id": "restricted2", "url": "u2"}, {"video_id": "audioOK0001", "url": "u3"}])
    class J:  # a job with just the lock the helper uses
        import threading
        lock = threading.Lock()
    track = {"artist": "Lil Wayne", "title": "Rich As Fuck", "match": {}, "target_duration": 200}
    path = server._download_with_fallback(J, track, {"id": "restricted1", "url": "u1"}, "/tmp")
    assert path == "/tmp/ok.m4a" and calls == ["restricted1", "restricted2", "audioOK0001"] and track["source"]["video_id"] == "audioOK0001"
    monkeypatch.setattr(server.sourcing, "audio_candidates", lambda *a, **k: [{"video_id": "restricted2", "url": "u2"}])
    with pytest.raises(RuntimeError):
        server._download_with_fallback(J, track, {"id": "restricted1", "url": "u1"}, "/tmp")           # nothing else to try: the original error
    monkeypatch.setattr(server.downloader, "download_audio", lambda v, w: (_ for _ in ()).throw(RuntimeError("HTTP Error 500")))
    with pytest.raises(RuntimeError, match="500"):
        server._download_with_fallback(J, track, {"id": "x", "url": "u"}, "/tmp")                       # other errors are not retried


def test_songs_only_search_skips_the_album_lookups(monkeypatch):
    called = []
    monkeypatch.setattr(catalog, "_ytm_songs", lambda q, n: [])
    monkeypatch.setattr(catalog, "_itunes_songs", lambda q, n: [])
    monkeypatch.setattr(catalog, "_ytm_albums", lambda q, n: called.append("ytm albums") or [])
    monkeypatch.setattr(catalog, "_itunes_albums", lambda q, n: called.append("itunes albums") or [])
    assert catalog.search("x", songs_only=True) == {"songs": [], "albums": []} and called == []
    catalog.search("x")
    assert sorted(called) == ["itunes albums", "ytm albums"]


def test_import_keeps_the_uploaded_file_and_lists_unplaced_songs(tmp_path, monkeypatch):
    from tuberipper import spotify_import as si
    monkeypatch.setenv("TUBERIPPER_CACHE_DIR", str(tmp_path))
    si.save_upload("Track Name,Artist Name(s)\nA,B\n", "Big Little Lies (All songs)")
    files = list((tmp_path / "import-files").iterdir())
    assert len(files) == 1 and files[0].name.endswith("Big_Little_Lies_All_songs_.csv") and "Track Name" in files[0].read_text()
    monkeypatch.setattr(si, "library_id", lambda artists, title, meta=None: "n1" if title == "Found" else None)
    monkeypatch.setattr(si.navidrome, "find_song_id", lambda meta: None)
    monkeypatch.setattr(si.time, "sleep", lambda s: None)
    ids, missing, unplaced = si._playlist_ids([(["A"], "Found", {}), (["B", "C"], "Lost", {})], with_names=True)
    assert ids == ["n1"] and missing == 1 and unplaced == ["B, C - Lost"]


def test_retry_tool_downloads_in_parallel_then_places_after_one_scan(monkeypatch):
    from tuberipper import import_retry as ir
    scans, added = [], []
    monkeypatch.setattr(ir.navidrome, "list_playlists", lambda: [{"id": "p1", "name": "Mix"}])
    monkeypatch.setattr(ir.navidrome, "wait_for_scan", lambda: scans.append(1))
    monkeypatch.setattr(ir.navidrome, "add_to_playlist", lambda pid, ids: added.append((pid, ids)) or 1)
    monkeypatch.setattr(ir.si, "library_id", lambda artists, title, meta=None: "n-" + title if scans else None)    # only visible after the scan
    monkeypatch.setattr(ir, "recent_id", lambda title: None)
    monkeypatch.setattr(ir.si, "_find", lambda artists, title, secs: {"artist": artists[0], "title": title, "album": "A", "in_library": False})
    monkeypatch.setattr(ir.si, "_wait_for", lambda jid: {"status": "done", "tracks": [{"status": "done"}]})
    monkeypatch.setattr(ir.si.requests, "post", lambda *a, **k: type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"id": "j"}})())
    monkeypatch.setattr(ir.time, "sleep", lambda s: None)
    import json, sys, tempfile
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump([{"song": f"Art - T{i}", "playlists": ["Mix"]} for i in range(6)], f); f.close()
    ir.main(["x", f.name])
    assert len(scans) == 1 and sorted(i[1][0] for i in added) == [f"n-T{i}" for i in range(6)]
