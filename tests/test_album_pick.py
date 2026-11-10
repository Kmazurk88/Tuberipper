"""Album ranking in metadata.resolve_batch, with MusicBrainz/iTunes stubbed."""
from tuberipper import metadata, musicbrainz

VA = musicbrainz.VARIOUS_ARTISTS_ID


def opt(rg_id, rg_title, primary, date, *, secondary=(), artist="A", artist_id="a1", rec_date="", track=1):
    return {
        "rec_id": "r1", "rec_title": "Song", "rec_artist": "A", "rec_artist_id": "a1", "alt": False, "score": 100,
        "genre": "", "release_id": f"{rg_id}-rel", "release_date": date, "release_artist": artist,
        "release_artist_id": artist_id, "rg_id": rg_id, "rg_title": rg_title, "rg_primary": primary,
        "rg_secondary": list(secondary), "track_number": track, "disc_number": 1, "track_count": 10,
        "first_release_date": rec_date or date, "length": 200000,
    }


def stub(monkeypatch, options, true_dates=None, itunes=None):
    monkeypatch.setattr(musicbrainz, "track_options", lambda a, t: options)
    monkeypatch.setattr(musicbrainz, "known_rg_date", lambda rg: "")
    monkeypatch.setattr(musicbrainz, "rg_first_date", lambda rg: (true_dates or {}).get(rg, ""))
    monkeypatch.setattr(musicbrainz, "release_tracklist", lambda rid: None)
    monkeypatch.setattr(musicbrainz, "pick_release", lambda rg: None)
    monkeypatch.setattr(metadata, "_itunes_song_search", lambda a, t: itunes or [])


def test_album_that_only_holds_the_song_on_a_later_reissue_loses_to_the_ep(monkeypatch):
    # "The Fame" (2008) has "Bad Romance" (2009) only via its Fame Monster
    # reissue; The Fame Monster EP is the song's real home.
    stub(monkeypatch, [
        opt("fame", "The Fame", "Album", "2009-11-18", rec_date="2009-10"),
        opt("monster", "The Fame Monster", "EP", "2009-11-23", rec_date="2009-10"),
    ], true_dates={"fame": "2008-08-19", "monster": "2009-11-18"})
    m = metadata.resolve_batch([{"artist": "A", "title": "Song"}])[0]
    assert m["album"] == "The Fame Monster"


def test_remastered_original_album_still_wins(monkeypatch):
    # Only the 2019 remaster's recording matched, but the album is the
    # 1978 original and nothing else competes: keep it.
    stub(monkeypatch, [
        opt("lt", "Love Tracks", "Album", "2019-05-01", rec_date="2019-05-01"),
        opt("single", "Song", "Single", "2019-05-01", rec_date="2019-05-01"),
    ], true_dates={"lt": "1978-08-01"})
    m = metadata.resolve_batch([{"artist": "A", "title": "Song"}])[0]
    assert m["album"] == "Love Tracks"


def test_compilation_only_song_uses_the_artists_own_itunes_album(monkeypatch):
    stub(monkeypatch, [
        opt("bild", "Bild Sommer", "Album", "2003", secondary=["Compilation"], artist="Various Artists", artist_id=VA),
    ], itunes=[
        {"artistName": "A", "trackName": "Song (Disney Version)", "collectionName": "Mickey's Dance Party",
         "collectionArtistName": "Various Artists", "releaseDate": "2001-01-01T00:00:00Z", "trackNumber": 3},
        {"artistName": "A", "trackName": "Song [Radio Version]", "collectionName": "Planet Pop",
         "releaseDate": "2000-01-01T00:00:00Z", "trackNumber": 2, "primaryGenreName": "Dance"},
    ])
    m = metadata.resolve_batch([{"artist": "A", "title": "Song"}])[0]
    assert (m["album"], m["title"], m["track_number"]) == ("Planet Pop", "Song", 2)


def test_compilation_only_song_stays_when_itunes_has_nothing_better(monkeypatch):
    stub(monkeypatch, [
        opt("bild", "Bild Sommer", "Album", "2003", secondary=["Compilation"], artist="Various Artists", artist_id=VA),
    ])
    m = metadata.resolve_batch([{"artist": "A", "title": "Song"}])[0]
    assert m["album"] == "Bild Sommer"


def test_live_only_musicbrainz_match_uses_the_itunes_studio_album(monkeypatch):
    live = opt("live", "On Stage - Live", "Album", "2025-07-18", secondary=["Live"])
    live["rec_title"], live["alt"] = "Song - Live", True
    stub(monkeypatch, [live], itunes=[
        {"artistName": "A", "trackName": "Song", "collectionName": "Studio Album",
         "releaseDate": "2017-02-13T00:00:00Z", "trackNumber": 4},
    ])
    m = metadata.resolve_batch([{"artist": "A", "title": "Song"}])[0]
    assert m["album"] == "Studio Album"
