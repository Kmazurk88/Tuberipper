"""Writes a downloaded YouTube playlist into the library as an .m3u8 file,
so it survives as a playlist instead of just scattering its songs across
Artist/Album folders. Navidrome auto-imports .m3u/.m3u8 files it finds in
its music folder on its next scan (AutoImportPlaylists, on by default) and
keeps them in sync when the file changes."""

import os

from . import tagger

PLAYLISTS_DIR = "Playlists"


def write_m3u(library_root: str, name: str, entries: list) -> str:
    """entries: [(path, artist, title)] in playlist order, paths absolute
    (anywhere under library_root). Paths are written relative to the
    playlist file, so they stay valid whatever absolute path Navidrome
    mounts the library at. Overwrites a same-named playlist, so re-running
    a playlist updates it rather than duplicating it. Returns the file path."""
    playlist_dir = os.path.join(library_root, PLAYLISTS_DIR)
    os.makedirs(playlist_dir, exist_ok=True)
    path = os.path.join(playlist_dir, tagger.sanitize_filename(name or "YouTube playlist") + ".m3u8")

    lines = ["#EXTM3U", f"#PLAYLIST:{name}"]
    for track_path, artist, title in entries:
        rel = os.path.relpath(track_path, playlist_dir).replace(os.sep, "/")
        lines.append(f"#EXTINF:-1,{artist} - {title}")
        lines.append(rel)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path
