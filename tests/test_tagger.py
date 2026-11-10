"""Tag writing/reading for .m4a (new downloads) and .mp3 (older library files)."""
import os
import subprocess

import pytest
from mutagen.mp4 import MP4

from tuberipper import library_merge, metadata, tagger

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

META = {
    "artist": "Artist feat. Guest", "album_artist": "Artist", "title": "Song", "album": "Album",
    "year": "2001", "genre": "Pop", "track_number": 3, "disc_number": 2,
    "mb_recording_id": "rec-1", "mb_release_id": "rel-1", "mb_release_group_id": "rg-1",
    "mb_artist_id": "art-1", "mb_album_artist_id": "art-1",
}


def make_audio(path, seconds=2):
    codec = (["-c:a", "aac", "-b:a", "32k"] if str(path).endswith(".m4a") else
             ["-c:a", "libopus", "-b:a", "24k", "-ar", "48000"] if str(path).endswith(".opus") else ["-q:a", "9"])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", str(seconds), *codec, str(path)], check=True)
    return str(path)


@pytest.mark.parametrize("ext", [".m4a", ".mp3", ".opus"])
def test_apply_tags_roundtrip(tmp_path, ext):
    path = make_audio(tmp_path / f"a{ext}")
    tagger.apply_tags(path, META, JPEG)
    f = tagger.read_fields(path)
    assert (f["title"], f["artist"], f["album_artist"], f["album"], f["year"], f["genre"], f["track"], f["disc"]) == (
        "Song", "Artist feat. Guest", "Artist", "Album", "2001", "Pop", "3", "2")
    assert f["MusicBrainz Album Id"] == "rel-1" and f["MusicBrainz Release Group Id"] == "rg-1"
    assert f["MusicBrainz Artist Id"] == "art-1" and f["MusicBrainz Album Artist Id"] == "art-1"
    assert f[tagger.RECORDING_ID] == "rec-1"
    assert f["artwork"] == JPEG
    assert 1.5 < tagger.audio_length(path) < 2.5

    # Retagging clears what the new match doesn't have.
    tagger.apply_tags(path, {"artist": "B", "title": "T"}, None)
    f = tagger.read_fields(path)
    assert (f["artist"], f["album"], f["track"], f["MusicBrainz Album Id"], f["artwork"]) == ("B", "", "", "", None)


def test_m4a_uses_standard_atoms_and_png_cover(tmp_path):
    path = make_audio(tmp_path / "a.m4a")
    tagger.apply_tags(path, META, PNG)
    tags = MP4(path).tags
    assert tags["\xa9nam"] == ["Song"] and tags["aART"] == ["Artist"] and tags["trkn"] == [(3, 0)]
    assert bytes(tags["----:com.apple.iTunes:MusicBrainz Album Id"][0]) == b"rel-1"
    assert tags["covr"][0].imageformat == tags["covr"][0].FORMAT_PNG


def test_write_tags_files_m4a_under_its_own_extension(tmp_path):
    lib = tmp_path / "lib"
    path = make_audio(tmp_path / "dl.m4a")
    new = tagger.write_tags(path, META, None, str(lib))
    assert new == os.path.join(str(lib), "Artist", "Album", "2-03 - Song.m4a")
    assert os.path.exists(new) and not os.path.exists(path)
    assert tagger.find_existing_track(META, str(lib)) == new


def test_unify_album_folder_mixes_mp3_and_m4a(tmp_path):
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    old = make_audio(album / "1 - Old.mp3")
    tagger.apply_tags(old, {"artist": "Artist", "title": "Old", "album": "Album", "track_number": 1}, None)
    new = make_audio(album / "2 - New.m4a")
    tagger.apply_tags(new, {**META, "title": "New", "track_number": 2, "disc_number": 1}, None)

    assert tagger.unify_album_folder(str(album), reference_path=new) == 1
    o = tagger.read_fields(old)
    assert (o["album_artist"], o["year"], o["MusicBrainz Album Id"], o["disc"]) == ("Artist", "2001", "rel-1", "1")
    assert o["title"] == "Old" and o["track"] == "1"  # per-song tags untouched
    assert tagger.unify_album_folder(str(album)) == 0  # already consistent


def test_merge_retags_and_renames_m4a_keeping_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(metadata, "lookup_album_tracklist", lambda artist, album: ["Intro", "Song"])
    a = tmp_path / "Artist" / "Album"
    b = tmp_path / "Artist" / "Album (Deluxe)"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    keep = make_audio(a / "Intro.mp3")
    tagger.apply_tags(keep, {"artist": "Artist", "title": "Intro", "album": "Album", "year": "2001"}, JPEG)
    moving = make_audio(b / "9 - Song.m4a")
    tagger.apply_tags(moving, {"artist": "Artist", "title": "Song", "album": "Album (Deluxe)"}, None)
    (b / "9 - Song.lrc").write_text("[00:01.00] hi")

    library_merge.plan_merge(str(a), str(b), dry_run=False)
    assert sorted(os.listdir(a)) == ["1 - Intro.mp3", "2 - Song.lrc", "2 - Song.m4a"]
    f = tagger.read_fields(str(a / "2 - Song.m4a"))
    assert (f["album"], f["year"], f["track"], f["artwork"]) == ("Album", "2001", "2", JPEG)


def test_untaggable_formats_are_left_alone(tmp_path):
    p = tmp_path / "x.flac"
    p.write_bytes(b"not really flac")
    assert tagger.read_fields(str(p)) is None
    assert not tagger.is_taggable(str(p))



def test_opus_uses_picard_comment_names_and_clears_fields(tmp_path):
    from mutagen.oggopus import OggOpus
    path = make_audio(tmp_path / "a.opus")
    tagger.apply_tags(path, META, PNG)
    tags = OggOpus(path).tags
    assert tags["albumartist"] == ["Artist"] and tags["tracknumber"] == ["3"] and tags["date"] == ["2001"]
    assert tags["musicbrainz_trackid"] == ["rec-1"] and tags["musicbrainz_releasegroupid"] == ["rg-1"]
    assert tagger.read_fields(path)["artwork"] == PNG
    tagger.write_fields(path, {"genre": "", "artwork": None})
    f = tagger.read_fields(path)
    assert f["genre"] == "" and f["artwork"] is None and f["title"] == "Song"


def test_write_tags_files_opus_under_its_own_extension(tmp_path):
    lib = tmp_path / "lib"
    new = tagger.write_tags(make_audio(tmp_path / "dl.opus"), META, JPEG, str(lib))
    assert new == os.path.join(str(lib), "Artist", "Album", "2-03 - Song.opus")
    assert tagger.find_existing_track(META, str(lib)) == new
