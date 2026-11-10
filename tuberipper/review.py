"""Interactive CLI review step: confirm/edit Artist and Title before metadata lookup."""

from . import title_parser, metadata


def build_tracks(videos: list):
    tracks = []
    for video in videos:
        artist, title, confidence = title_parser.parse(video["title"])
        tracks.append({
            "id": video["id"],
            "url": video["url"],
            "raw_title": video["title"],
            "artist": artist,
            "title": title,
            "confidence": confidence,
        })
    return tracks


def _print_track(i: int, track: dict, total: int):
    flag = " [LOW CONFIDENCE]" if track["confidence"] == "low" else ""
    print(f"\n[{i}/{total}]{flag} Raw title: {track['raw_title']}")
    print(f"  Artist: {track['artist'] or '(blank)'}")
    print(f"  Title:  {track['title']}")


def _edit_track(track: dict, last_artist: str = ""):
    suggested_artist = track["artist"] or last_artist
    new_artist = input(f"  New artist [{suggested_artist}]: ").strip()
    new_title = input(f"  New title [{track['title']}]: ").strip()
    if new_artist:
        track["artist"] = new_artist
    elif suggested_artist:
        track["artist"] = suggested_artist
    if new_title:
        track["title"] = new_title

    print("  Looking up metadata match...")
    match = metadata.lookup(track["artist"], track["title"])
    if match:
        print(f"  -> Match: {match['artist']} - {match['title']} "
              f"| Album: {match['album']} | Year: {match['year']} | Genre: {match['genre']}")
    else:
        print("  -> No metadata match found.")
    return track


def review(tracks: list):
    """Walks the user through each track. High-confidence tracks can be bulk-accepted;
    low-confidence ones are always flagged for individual attention."""
    total = len(tracks)
    blank_artist_count = sum(1 for t in tracks if not t["artist"])

    if blank_artist_count > 1:
        default_artist = input(
            f"\n{blank_artist_count} tracks have no artist detected "
            f"(common for single-artist playlists/albums).\n"
            f"Enter an artist to apply to all of them, or leave blank if this playlist "
            f"has multiple artists (you'll enter each one individually, and can reuse "
            f"the last one you typed by just pressing Enter): "
        ).strip()
        if default_artist:
            for track in tracks:
                if not track["artist"]:
                    track["artist"] = default_artist
                    track["confidence"] = "high"

    low_conf_indices = [i for i, t in enumerate(tracks) if t["confidence"] == "low"]

    if low_conf_indices:
        print(f"\n{len(low_conf_indices)} of {total} tracks still need review (low-confidence title split).")
    else:
        print(f"\nAll {total} tracks parsed with high confidence.")

    bulk = input("Accept all high-confidence tracks as-is? [Y/n]: ").strip().lower()
    accept_high_conf = bulk in ("", "y", "yes")

    accepted = []
    last_artist = ""
    for i, track in enumerate(tracks):
        if track["confidence"] == "high" and accept_high_conf:
            print(f"[{i + 1}/{total}] Accepted: {track['artist']} - {track['title']}")
            accepted.append(track)
            last_artist = track["artist"] or last_artist
            continue

        _print_track(i + 1, track, total)
        choice = input("  Accept / edit / skip? [a/E/s]: ").strip().lower()
        if not choice:
            choice = "e"

        if choice == "s":
            continue
        elif choice == "e":
            track = _edit_track(track, last_artist)
            confirm = input("  Keep these values? [Y/n]: ").strip().lower()
            if confirm in ("n", "no"):
                continue

        accepted.append(track)
        last_artist = track["artist"] or last_artist

    return accepted
