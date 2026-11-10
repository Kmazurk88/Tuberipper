"""downloader.trim_to_song: album uploads that run on into the next song."""
import subprocess

from tuberipper import downloader, tagger


def make(path, parts):
    """parts: [("tone"|"silence", seconds), ...] concatenated into one AAC .m4a."""
    inputs, filters = [], []
    for i, (kind, sec) in enumerate(parts):
        src = f"sine=frequency=440:duration={sec}" if kind == "tone" else f"anullsrc=r=44100:cl=mono:d={sec}"
        inputs += ["-f", "lavfi", "-i", src]
        filters.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=mono[a{i}]")
    graph = ";".join(filters) + ";" + "".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[out]"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
                    "-c:a", "aac", "-b:a", "64k", str(path)], check=True)
    return str(path)


def test_cuts_the_next_songs_intro_at_the_silence(tmp_path):
    path = make(tmp_path / "a.m4a", [("tone", 20), ("silence", 3), ("tone", 5)])
    cut = downloader.trim_to_song(path, 21, tagger.audio_length(path))
    assert cut is not None and 20 < cut < 21.5
    assert abs(tagger.audio_length(path) - cut) < 0.2


def test_leaves_audio_alone_without_a_silence_near_the_end(tmp_path):
    path = make(tmp_path / "a.m4a", [("tone", 28)])
    assert downloader.trim_to_song(path, 21, tagger.audio_length(path)) is None
    assert tagger.audio_length(path) > 27.5


def test_leaves_audio_alone_when_length_already_matches(tmp_path):
    path = make(tmp_path / "a.m4a", [("tone", 20), ("silence", 2)])
    assert downloader.trim_to_song(path, 21, tagger.audio_length(path)) is None


def test_cuts_at_the_end_not_at_a_pause_just_before_it(tmp_path):
    # A 1 s pause 3 s before the official end, then the real end + next song.
    path = make(tmp_path / "a.m4a", [("tone", 17), ("silence", 1), ("tone", 3), ("silence", 3), ("tone", 5)])
    cut = downloader.trim_to_song(path, 21, tagger.audio_length(path))
    assert cut is not None and 21 <= cut < 22
