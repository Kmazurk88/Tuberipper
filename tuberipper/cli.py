import argparse
import os
import sys

from . import downloader, metadata, review, tagger, config, lyrics


def main():
    parser = argparse.ArgumentParser(description="Download YouTube audio and auto-tag it.")
    parser.add_argument("url", nargs="?", help="YouTube video or playlist URL")
    parser.add_argument("-o", "--out", default=config.DEFAULT_OUTPUT_DIR,
                         help=f"Output directory (default: {config.DEFAULT_OUTPUT_DIR})")
    args = parser.parse_args()

    url = args.url or input("YouTube video or playlist URL: ").strip()
    if not url:
        print("No URL provided.")
        sys.exit(1)

    out_dir = args.out
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        print(f"Can't reach output folder '{out_dir}': {exc}")
        print("Check that the network share is connected and accessible, or pass a different path with --out.")
        sys.exit(1)

    print("Fetching video list...")
    try:
        videos = downloader.get_video_list(url)
    except Exception as exc:
        print(f"Failed to read URL: {exc}")
        sys.exit(1)

    if not videos:
        print("No videos found at that URL.")
        sys.exit(1)

    print(f"Found {len(videos)} track(s).")
    tracks = review.build_tracks(videos)
    accepted = review.review(tracks)

    if not accepted:
        print("\nNo tracks accepted. Nothing to download.")
        return

    print(f"\nProcessing {len(accepted)} track(s)...")
    print("Looking up metadata for the batch...")
    matches = metadata.resolve_batch([{"artist": t["artist"], "title": t["title"]} for t in accepted])

    for i, (track, match) in enumerate(zip(accepted, matches), start=1):
        print(f"\n[{i}/{len(accepted)}] {track['raw_title']}")

        existing = tagger.find_existing_track(match, out_dir)
        if existing:
            print(f"  Already exists, skipping: {existing}")
            continue

        print("  Downloading audio...")
        try:
            video = {"id": track["id"], "url": track["url"]}
            filepath = downloader.download_audio(video, out_dir)
        except Exception as exc:
            print(f"  Download failed: {exc}")
            continue

        artwork_bytes = metadata.fetch_artwork(match.get("artwork_url", ""))

        new_path = tagger.write_tags(filepath, match, artwork_bytes, library_root=out_dir)
        print(f"  Saved: {new_path}")

        print("  Looking up lyrics...")
        duration = tagger.audio_length(new_path)
        lrc_text, _kind = lyrics.fetch_lrc(match["artist"], match["title"], match.get("album", ""),
                                           official_duration=match.get("duration"), audio_duration=duration)
        if lrc_text:
            lrc_path = os.path.splitext(new_path)[0] + ".lrc"
            with open(lrc_path, "w", encoding="utf-8") as f:
                f.write(lrc_text)
            print(f"  Lyrics saved: {lrc_path}")
        else:
            print("  No lyrics found.")

    print("\nDone.")


if __name__ == "__main__":
    main()
