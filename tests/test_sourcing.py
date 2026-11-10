"""Offline tests for picking the audio by length (sourcing.py) and for the
lyrics sync check (lyrics.py). Run: python -m pytest tests"""

import pytest

from tuberipper import lyrics, sourcing


def ytm_song(video_id, title, artist, seconds, explicit=False):
    return {"resultType": "song", "videoId": video_id, "title": title,
            "artists": [{"name": artist}], "duration_seconds": seconds, "isExplicit": explicit}


@pytest.fixture(autouse=True)
def clear_cache():
    sourcing._cache.clear()


def fake_search(songs=(), videos=()):
    def _search(query, filter_):
        return list(songs if filter_ == "songs" else videos)
    return _search


# ---------- score_result ----------

def test_album_audio_within_tolerance_is_accepted():
    r = ytm_song("a", "Lose Yourself", "Eminem", 322)
    assert sourcing.score_result(r, "Eminem", "Lose Yourself", 320) is not None


def test_music_video_length_is_rejected():
    r = ytm_song("v", "Lose Yourself", "Eminem", 358)
    assert sourcing.score_result(r, "Eminem", "Lose Yourself", 320) is None


def test_remix_and_wrong_artist_are_rejected():
    assert sourcing.score_result(ytm_song("r", "Lose Yourself (Remix)", "Eminem", 320), "Eminem", "Lose Yourself", 320) is None
    assert sourcing.score_result(ytm_song("c", "Lose Yourself", "Some Cover Band", 320), "Eminem", "Lose Yourself", 320) is None


def test_featured_artist_credit_still_matches():
    r = ytm_song("a", "Sexy Bitch (feat. Akon)", "David Guetta", 195)
    assert sourcing.score_result(r, "David Guetta feat. Akon", "Sexy Bitch", 195) is not None


def test_closer_length_and_same_explicitness_win():
    near = ytm_song("near", "Song", "Artist", 200)
    far = ytm_song("far", "Song", "Artist", 203)
    clean = ytm_song("clean", "Song", "Artist", 200, explicit=False)
    dirty = ytm_song("dirty", "Song", "Artist", 200, explicit=True)
    assert sourcing._best([far, near], "Artist", "Song", 200, None)["videoId"] == "near"
    assert sourcing._best([clean, dirty], "Artist", "Song", 200, True)["videoId"] == "dirty"


# ---------- find_audio_source ----------

def test_swaps_music_video_for_album_audio(monkeypatch):
    monkeypatch.setattr(sourcing, "_search", fake_search(songs=[
        ytm_song("mv", "Lose Yourself", "Eminem", 358),
        ytm_song("topic", "Lose Yourself", "Eminem", 321),
    ]))
    original = {"id": "mv", "url": "https://www.youtube.com/watch?v=mv", "duration": 358}
    src = sourcing.find_audio_source("Eminem", "Lose Yourself", 320, original)
    assert src["kind"] == "album_audio" and src["video_id"] == "topic" and src["ok"] is True
    assert src["diff"] == 1


def test_keeps_users_video_when_its_length_matches(monkeypatch):
    monkeypatch.setattr(sourcing, "_search", fake_search())
    original = {"id": "x", "url": "https://www.youtube.com/watch?v=x", "duration": 201}
    src = sourcing.find_audio_source("Artist", "Song", 200, original)
    assert src["kind"] == "your_video" and src["ok"] is True


def test_lyric_video_of_right_length_beats_long_music_video(monkeypatch):
    monkeypatch.setattr(sourcing, "_search", fake_search(videos=[
        {"videoId": "lyr", "title": "Song", "artists": [{"name": "Artist"}], "duration_seconds": 199},
    ]))
    original = {"id": "mv", "url": "https://www.youtube.com/watch?v=mv", "duration": 260}
    src = sourcing.find_audio_source("Artist", "Song", 200, original)
    assert src["kind"] == "matched_video" and src["video_id"] == "lyr"


def test_falls_back_to_users_video_flagged(monkeypatch):
    monkeypatch.setattr(sourcing, "_search", fake_search())
    original = {"id": "mv", "url": "https://www.youtube.com/watch?v=mv", "duration": 260}
    src = sourcing.find_audio_source("Artist", "Song", 200, original)
    assert src["kind"] == "your_video" and src["ok"] is False and src["diff"] == 60


def test_nothing_found_for_catalog_pick_without_video(monkeypatch):
    monkeypatch.setattr(sourcing, "_search", fake_search())
    assert sourcing.find_audio_source("Artist", "Song", 200, None) is None


def test_unknown_length_takes_best_title_match(monkeypatch):
    monkeypatch.setattr(sourcing, "_search", fake_search(songs=[ytm_song("t", "Song", "Artist", 250)]))
    src = sourcing.find_audio_source("Artist", "Song", None, None)
    assert src["video_id"] == "t" and src["ok"] is None


def test_youtube_music_outage_falls_back(monkeypatch):
    def boom(query, filter_):
        raise RuntimeError("down")
    monkeypatch.setattr(sourcing, "_search", boom)
    original = {"id": "x", "url": "u", "duration": 200}
    assert sourcing.find_audio_source("A", "S", 200, original)["kind"] == "your_video"


# ---------- lyrics ----------

class FakeResp:
    def __init__(self, data, ok=True):
        self._data, self.ok = data, ok

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


SYNCED = "[00:12.00] first line\n[00:15.50] second line"


def fake_lrclib(get_data, search_data):
    def get(url, params=None, **kw):
        return FakeResp(get_data if url == lyrics.LRCLIB_GET_URL else search_data)
    return get


def test_synced_when_audio_is_album_length(monkeypatch):
    monkeypatch.setattr(lyrics.requests, "get", fake_lrclib(
        {"duration": 212, "syncedLyrics": SYNCED, "plainLyrics": "first line\nsecond line"}, []))
    text, kind = lyrics.fetch_lrc("A", "Song", "", official_duration=212, audio_duration=213)
    assert kind == "synced" and text == SYNCED


def test_plain_only_when_audio_is_a_long_music_video(monkeypatch):
    monkeypatch.setattr(lyrics.requests, "get", fake_lrclib(
        {"duration": 212, "syncedLyrics": SYNCED, "plainLyrics": None}, []))
    text, kind = lyrics.fetch_lrc("A", "Song", "", official_duration=212, audio_duration=275)
    assert kind == "plain" and "[" not in text and "first line" in text


def test_search_fallback_only_takes_synced_of_matching_length(monkeypatch):
    monkeypatch.setattr(lyrics.requests, "get", fake_lrclib({}, [
        {"trackName": "Song", "duration": 280, "syncedLyrics": "[00:40.00] video timing", "plainLyrics": "video timing"},
        {"trackName": "Song", "duration": 211, "syncedLyrics": SYNCED, "plainLyrics": "x"},
    ]))
    text, kind = lyrics.fetch_lrc("A", "Song", "", official_duration=None, audio_duration=212)
    assert kind == "synced" and text == SYNCED


def test_nothing_found(monkeypatch):
    monkeypatch.setattr(lyrics.requests, "get", fake_lrclib({}, [{"trackName": "Other Song", "syncedLyrics": SYNCED}]))
    assert lyrics.fetch_lrc("A", "Song", "", 200, 200) == (None, None)


def test_third_party_lyric_video_read_from_title(monkeypatch):
    # Real YouTube Music shape: the uploading channel is listed as the
    # artist, the real artist is in the title.
    monkeypatch.setattr(sourcing, "_search", fake_search(videos=[
        {"videoId": "mv", "title": "Lose Yourself", "artists": [{"name": "Eminem"}], "duration_seconds": 328},
        {"videoId": "lyr", "title": "Eminem - Lose Yourself (Lyrics)", "artists": [{"name": "7clouds"}], "duration_seconds": 321},
        {"videoId": "8d", "title": "Eminem - Lose Yourself (8D Audio) 🎧", "artists": [{"name": "meshy"}], "duration_seconds": 322},
        {"videoId": "other", "title": "Someone Else - Lose Yourself (Lyrics)", "artists": [{"name": "7clouds"}], "duration_seconds": 322},
    ]))
    src = sourcing.find_audio_source("Eminem", "Lose Yourself", 322, None)
    assert src["kind"] == "matched_video" and src["video_id"] == "lyr"


def test_credited_upload_beats_title_parsed_one(monkeypatch):
    monkeypatch.setattr(sourcing, "_search", fake_search(videos=[
        {"videoId": "lyr", "title": "Artist - Song (Lyrics)", "artists": [{"name": "Lyrics Channel"}], "duration_seconds": 200},
        {"videoId": "own", "title": "Song", "artists": [{"name": "Artist"}], "duration_seconds": 200},
    ]))
    assert sourcing.find_audio_source("Artist", "Song", 200, None)["video_id"] == "own"


def test_title_parsed_upload_with_leftover_brackets_rejected(monkeypatch):
    monkeypatch.setattr(sourcing, "_search", fake_search(videos=[
        {"videoId": "drum", "title": "Eminem - Lose Yourself (Drumless Track) | Drum Practice 85 BPM",
         "artists": [{"name": "DrumlessOJam"}], "duration_seconds": 322},
        {"videoId": "odd", "title": "Eminem - Lose Yourself (Ultra Bass Edition)",
         "artists": [{"name": "Somebody"}], "duration_seconds": 322},
    ]))
    assert sourcing.find_audio_source("Eminem", "Lose Yourself", 322, None) is None


def test_performance_labels_stripped_from_video_titles():
    from tuberipper.title_parser import parse
    assert parse("Ricchi E Poveri - Sarà perché ti amo (Thommys Popshow extra 17.12.1983)")[:2] == ("Ricchi E Poveri", "Sarà perché ti amo")
    assert parse("AURORA - RUNAWAY - The 2015 Nobel Peace Prize Concert")[:2] == ("AURORA", "RUNAWAY")
    assert parse("Tears For Fears - Everybody Wants To Rule The World (Official Archive Video)")[1] == "Everybody Wants To Rule The World"
    assert parse("Belinda Carlisle - Heaven Is A Place On Earth (4K Remaster)")[1] == "Heaven Is A Place On Earth"
    assert parse("Oasis - Live Forever")[:2] == ("Oasis", "Live Forever")


def test_catalog_pick_falls_back_to_closest_upload_flagged(monkeypatch):
    monkeypatch.setattr(sourcing, "_search", fake_search(videos=[
        {"videoId": "mv", "title": "Safe And Sound", "artists": [{"name": "Capital Cities"}], "duration_seconds": 227},
        {"videoId": "live", "title": "Safe And Sound (Live)", "artists": [{"name": "Capital Cities"}], "duration_seconds": 195},
    ]))
    src = sourcing.find_audio_source("Capital Cities", "Safe and Sound", 193, None)
    assert src["video_id"] == "mv" and src["ok"] is False and src["diff"] == 34


def test_show_name_with_year_is_stripped_but_not_a_dated_remix():
    from tuberipper import title_parser
    assert title_parser.parse_batch(["Air Supply - Making Love Out Of Nothing At All (Vorsicht Musik!, 1983)"])[0][:2] == (
        "Air Supply", "Making Love Out Of Nothing At All")
    assert title_parser.parse_batch(["Song - Title (Remix, 2019)"])[0][1] == "Title (Remix, 2019)"
