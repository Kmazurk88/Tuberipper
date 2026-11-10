"""Offline tests for merging YouTube Music + iTunes search rows (catalog.py)."""

from tuberipper import catalog


def ytm(title, artist, album, seconds, vid, explicit=False):
    return {"title": title, "artist": artist, "album": album, "year": "", "duration": seconds,
            "explicit": explicit, "artwork": "ytm.jpg", "video_id": vid, "ytm_album_id": None}


def itunes(title, artist, album, seconds, tid, explicit=False, year="2002"):
    return {"title": title, "artist": artist, "album": album, "year": year, "duration": seconds,
            "explicit": explicit, "artwork": "it.jpg", "itunes_id": tid, "genre": "Hip-Hop/Rap"}


def test_same_song_merges_and_keeps_video_id():
    rows = catalog.merge_songs(
        [ytm("Lose Yourself", "Eminem", "8 Mile", 321, "v1", True)],
        [itunes("Lose Yourself", "Eminem", "8 Mile (Music from and Inspired by the Motion Picture)", 320, 11, True)],
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["video_id"] == "v1" and r["itunes_id"] == 11 and r["year"] == "2002" and r["artwork"] == "it.jpg"


def test_clean_and_explicit_stay_separate():
    rows = catalog.merge_songs([ytm("Song", "A", "Alb", 200, "v1", True)], [itunes("Song", "A", "Alb", 200, 1, False)])
    assert len(rows) == 2 and rows[1]["video_id"] is None


def test_different_length_is_a_different_version():
    rows = catalog.merge_songs([ytm("Song", "A", "Alb", 200, "v1")], [itunes("Song", "A", "Alb", 260, 1)])
    assert len(rows) == 2


def test_exact_duplicates_collapse():
    rows = catalog.merge_songs([ytm("Song", "A", "Alb", 200, "v1"), ytm("Song", "A", "Alb", 200, "v2")], [])
    assert [r["video_id"] for r in rows] == ["v1"]


def test_main_artist_and_artist_match():
    assert catalog.main_artist("David Guetta feat. Akon") == "David Guetta"
    assert catalog.main_artist("Jennifer Lopez, Pitbull") == "Jennifer Lopez"
    assert catalog.artist_match("Eminem", ["Eminem, Dido"]) == 1.0
    assert catalog.artist_match("Eminem", ["Taylor Swift"]) < 0.6


def test_albums_merge_by_canonical_title():
    rows = catalog.merge_albums(
        [{"title": "The Lumineers", "artist": "The Lumineers", "year": "2012", "type": "Album",
          "explicit": False, "artwork": "y", "track_count": None, "ytm_browse_id": "MPRE1"}],
        [{"title": "The Lumineers (Deluxe Edition)", "artist": "The Lumineers", "year": "2012", "type": "Album",
          "explicit": False, "artwork": "i", "track_count": 14, "itunes_id": 5}],
    )
    assert len(rows) == 1 and rows[0]["itunes_id"] == 5 and rows[0]["track_count"] == 14
