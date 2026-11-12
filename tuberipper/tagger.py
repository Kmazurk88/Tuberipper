"""Writes tags (ID3 for .mp3, MP4 atoms for .m4a, Vorbis comments for .opus; including embedded album art) and files the track into
the Artist/Album/Track library structure."""

import base64
import os
import re
import shutil
import unicodedata

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TPE2, TALB, TDRC, TCON, TRCK, TPOS, TXXX, UFID, APIC
from mutagen.flac import Picture
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus

# MusicBrainz ids, written the way Picard writes them so Navidrome (and other
# players) can group albums/artists by id instead of by spelling.
MB_TXXX = {
    "mb_release_id": "MusicBrainz Album Id",
    "mb_release_group_id": "MusicBrainz Release Group Id",
    "mb_artist_id": "MusicBrainz Artist Id",
    "mb_album_artist_id": "MusicBrainz Album Artist Id",
}

# Various dash-like characters that show up in metadata from different
# sources (iTunes, MusicBrainz, hand-typed folder names) but should all be
# treated as the same plain hyphen for matching and naming purposes.
DASH_VARIANTS = "‐‑‒–—―−"
DASH_TRANSLATION = str.maketrans({c: "-" for c in DASH_VARIANTS})

# Likewise for curly vs straight quotes/apostrophes ("Ain't" vs "Ain’t").
QUOTE_TRANSLATION = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})

# Tracks already in the library aren't only ones this app downloaded (as
# .mp3) -- some were imported some other way in a different format. A
# duplicate check that only looked for an exact .mp3 filename match missed
# all of those and re-downloaded them.
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".flac", ".wav", ".ogg", ".opus", ".aac", ".wma")


def normalize_dashes(name: str) -> str:
    return name.translate(DASH_TRANSLATION)


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "", normalize_dashes(name))
    return cleaned.strip() or "Unknown"


def _fold(name: str) -> str:
    """A loose key for matching folder or file names that differ only in
    dash style, quote style, case, or accent marks (e.g. an existing
    'blink‐182' vs a freshly generated 'blink-182', or "Ain’t" vs "Ain't")."""
    name = normalize_dashes(name).translate(QUOTE_TRANSLATION)
    normalized = unicodedata.normalize("NFKD", name)
    return "".join(c for c in normalized if not unicodedata.combining(c)).casefold()


def _resolve_existing_dir(parent_dir: str, name: str) -> str:
    """Returns an existing subdirectory of parent_dir that matches `name`
    loosely (dash/case/accent-insensitive), or `name` itself if none exists."""
    try:
        entries = os.listdir(parent_dir)
    except OSError:
        return name

    target_key = _fold(name)
    for entry in entries:
        if os.path.isdir(os.path.join(parent_dir, entry)) and _fold(entry) == target_key:
            return entry
    return name


# A featured-artist credit on the track artist ("David Guetta feat. Akon",
# "David Guetta ft. Akon"). Only these explicit markers are stripped -- not
# "&" or "," or "x", which are also part of real names ("Simon & Garfunkel",
# "Earth, Wind & Fire").
FEAT_RE = re.compile(r"\s*[\(\[]?\s*\b(feat\.?|ft\.?|featuring)\s.*$", re.IGNORECASE)
VARIOUS_ARTISTS = {"various artists", "various", "va"}


def primary_artist(artist: str) -> str:
    return FEAT_RE.sub("", artist or "").strip() or (artist or "").strip()


def album_artist(meta: dict) -> str:
    """The artist a track is filed under: the album's artist when known (so
    "David Guetta & Akon" on a David Guetta album lands in "David Guetta"),
    otherwise the track artist minus any feat. credit. Compilations
    ("Various Artists") fall back to the track's own artist."""
    album_level = (meta.get("album_artist") or "").strip()
    if album_level and album_level.lower() not in VARIOUS_ARTISTS:
        return primary_artist(album_level)
    return primary_artist(meta.get("artist", ""))


def _track_label(meta: dict) -> str:
    """"3", or "2-03" for disc 2 -- so multi-disc albums don't collide."""
    disc = meta.get("disc_number")
    try:
        if disc and int(disc) > 1:
            return f"{int(disc)}-{int(meta['track_number']):02d}"
    except (TypeError, ValueError):
        pass
    return str(meta.get("track_number"))


def build_target_path(meta: dict, library_root: str, ext: str = ".m4a") -> str:
    """Computes where a track would be filed: <library_root>/<Artist>/<Album>/<Track> - <Title><ext>
    Reuses an existing Artist/Album folder that matches loosely (different
    dash character, case, etc) instead of creating a near-duplicate."""
    artist = sanitize_filename(album_artist(meta) or "Unknown Artist")
    album = sanitize_filename(meta.get("album", "") or "Unknown Album")
    title = sanitize_filename(meta.get("title", "") or "Unknown Title")
    track_number = meta.get("track_number")

    artist_dir = _resolve_existing_dir(library_root, artist)
    album_dir = _resolve_existing_dir(os.path.join(library_root, artist_dir), album)

    filename = f"{_track_label(meta)} - {title}{ext}" if track_number else f"{title}{ext}"
    return os.path.join(library_root, artist_dir, album_dir, filename)


def find_existing_track(meta: dict, library_root: str) -> str | None:
    """Returns the path of a track that already exists for this metadata, in
    the matching Artist/Album folder, under ANY recognized audio extension
    and matching loosely (case/accent/dash/quote-insensitive) -- so a track
    already in the library as e.g. a differently-cased .m4a isn't treated as
    missing and re-downloaded as a "new" .mp3 alongside it. None if no such
    track is found."""
    artist = sanitize_filename(album_artist(meta) or "Unknown Artist")
    album = sanitize_filename(meta.get("album", "") or "Unknown Album")
    title = sanitize_filename(meta.get("title", "") or "Unknown Title")
    track_number = meta.get("track_number")
    target_stem = f"{_track_label(meta)} - {title}" if track_number else title

    artist_dir = _resolve_existing_dir(library_root, artist)
    album_dir_name = _resolve_existing_dir(os.path.join(library_root, artist_dir), album)
    album_dir = os.path.join(library_root, artist_dir, album_dir_name)

    try:
        entries = os.listdir(album_dir)
    except OSError:
        return None

    target_key = _fold(target_stem)
    for entry in entries:
        stem, ext = os.path.splitext(entry)
        if ext.lower() in AUDIO_EXTENSIONS and _fold(stem) == target_key:
            return os.path.join(album_dir, entry)
    return None


# ---------------------------------------------------------------------------
# Tags, format-neutral. Downloads are .opus (YouTube's Opus stream, its best
# sounding one, kept as-is) or .m4a (its AAC stream, when there is no Opus);
# older library files are .mp3. All are read and written through these field
# names, mapped to ID3 frames, MP4 atoms or Vorbis comments the way Picard
# writes them, so Navidrome reads them identically.
# ---------------------------------------------------------------------------

TAGGABLE_EXTENSIONS = (".mp3", ".m4a", ".mp4", ".opus")

_ID3_TEXT = {"title": TIT2, "artist": TPE1, "album_artist": TPE2, "album": TALB,
             "year": TDRC, "genre": TCON, "track": TRCK, "disc": TPOS}
_MP4_TEXT = {"title": "\xa9nam", "artist": "\xa9ART", "album_artist": "aART", "album": "\xa9alb",
             "year": "\xa9day", "genre": "\xa9gen"}
_MP4_FREEFORM = "----:com.apple.iTunes:"
_OPUS_TEXT = {"title": "title", "artist": "artist", "album_artist": "albumartist", "album": "album",
              "year": "date", "genre": "genre", "track": "tracknumber", "disc": "discnumber"}
_OPUS_ART = "metadata_block_picture"
# The recording id: a UFID frame in ID3, "MusicBrainz Track Id" in MP4.
RECORDING_ID = "MusicBrainz Track Id"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def is_taggable(path: str) -> bool:
    return path.lower().endswith(TAGGABLE_EXTENSIONS)


def _is_mp4(path: str) -> bool:
    return path.lower().endswith((".m4a", ".mp4"))


def _is_opus(path: str) -> bool:
    return path.lower().endswith(".opus")


def _opus_key(field: str) -> str:
    """The Vorbis comment for a field, Picard's naming: "MusicBrainz Album Id" -> musicbrainz_albumid (and
    the recording id, "MusicBrainz Track Id" -> musicbrainz_trackid)."""
    return _OPUS_TEXT.get(field) or "musicbrainz_" + field[len("MusicBrainz "):].lower().replace(" ", "")


def _opus_picture(data: bytes) -> str:
    pic = Picture()
    pic.type, pic.desc, pic.data = 3, "Cover", bytes(data)
    pic.mime = "image/png" if pic.data[:8] == _PNG_MAGIC else "image/jpeg"
    return base64.b64encode(pic.write()).decode("ascii")


def audio_length(path: str) -> float | None:
    """Length in seconds of any audio file mutagen understands."""
    try:
        audio = MutagenFile(path)
        return float(audio.info.length) if audio and audio.info else None
    except Exception:
        return None


def _num(value) -> int:
    try:
        return int(str(value).split("/")[0])
    except (TypeError, ValueError):
        return 0


def read_fields(path: str) -> dict | None:
    """{title, artist, album_artist, album, year, genre, track, disc,
    <MusicBrainz ... Id>..., artwork (bytes or None)} as strings ("" when
    missing). None if the file can't be read or isn't .mp3/.m4a/.opus."""
    try:
        if _is_opus(path):
            tags = OggOpus(path).tags or {}
            out = {k: str(tags[c][0]).split("/")[0] if k in ("track", "disc") and tags.get(c) else str(tags[c][0]) if tags.get(c) else ""
                   for k, c in _OPUS_TEXT.items()}
            for desc in list(MB_TXXX.values()) + [RECORDING_ID]:
                v = tags.get(_opus_key(desc))
                out[desc] = str(v[0]) if v else ""
            art = tags.get(_OPUS_ART)
            out["artwork"] = Picture(base64.b64decode(art[0])).data if art else None
            return out
        if _is_mp4(path):
            tags = MP4(path).tags or {}
            out = {k: str(tags[a][0]) if tags.get(a) else "" for k, a in _MP4_TEXT.items()}
            out["track"] = str(tags["trkn"][0][0]) if tags.get("trkn") and tags["trkn"][0][0] else ""
            out["disc"] = str(tags["disk"][0][0]) if tags.get("disk") and tags["disk"][0][0] else ""
            for desc in list(MB_TXXX.values()) + [RECORDING_ID]:
                v = tags.get(_MP4_FREEFORM + desc)
                out[desc] = bytes(v[0]).decode("utf-8", "replace") if v else ""
            out["artwork"] = bytes(tags["covr"][0]) if tags.get("covr") else None
            return out
        if not path.lower().endswith(".mp3"):
            return None
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        out = {}
        for k, cls in _ID3_TEXT.items():
            f = tags.get(cls.__name__)
            out[k] = str(f.text[0]) if f and f.text else ""
        for desc in MB_TXXX.values():
            f = tags.get(f"TXXX:{desc}")
            out[desc] = str(f.text[0]) if f and f.text else ""
        ufid = tags.get("UFID:http://musicbrainz.org")
        out[RECORDING_ID] = ufid.data.decode("utf-8", "replace") if ufid else ""
        apic = tags.getall("APIC")
        out["artwork"] = apic[0].data if apic else None
        return out
    except Exception:
        return None


def write_fields(path: str, fields: dict) -> None:
    """Sets the given fields (same names as read_fields); a value of "" or
    None removes that field. Fields not mentioned are left alone."""
    if _is_opus(path):
        audio = OggOpus(path)
        for k, v in fields.items():
            if k == "artwork":
                key, value = _OPUS_ART, ([_opus_picture(v)] if v else None)
            elif k in ("track", "disc"):
                key, value = _OPUS_TEXT[k], ([str(_num(v))] if _num(v) else None)
            else:
                key, value = _opus_key(k), ([str(v)] if v else None)
            if value is not None:
                audio.tags[key] = value
            elif key in audio.tags:
                del audio.tags[key]
        audio.save()
        return
    if _is_mp4(path):
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        for k, v in fields.items():
            if k in _MP4_TEXT:
                atom, value = _MP4_TEXT[k], ([str(v)] if v else None)
            elif k in ("track", "disc"):
                atom, value = ("trkn" if k == "track" else "disk"), ([(_num(v), 0)] if _num(v) else None)
            elif k == "artwork":
                fmt = MP4Cover.FORMAT_PNG if v and bytes(v[:8]) == _PNG_MAGIC else MP4Cover.FORMAT_JPEG
                atom, value = "covr", ([MP4Cover(v, imageformat=fmt)] if v else None)
            else:
                atom, value = _MP4_FREEFORM + k, ([MP4FreeForm(str(v).encode("utf-8"))] if v else None)
            if value is None:
                tags.pop(atom, None)
            else:
                tags[atom] = value
        audio.save()
        return

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    for k, v in fields.items():
        if k in _ID3_TEXT:
            cls = _ID3_TEXT[k]
            tags.delall(cls.__name__)
            if v:
                tags.add(cls(encoding=3, text=str(v)))
        elif k == "artwork":
            tags.delall("APIC")
            if v:
                mime = "image/png" if bytes(v[:8]) == _PNG_MAGIC else "image/jpeg"
                tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=v))
        elif k == RECORDING_ID:
            tags.delall("UFID:http://musicbrainz.org")
            if v:
                tags.add(UFID(owner="http://musicbrainz.org", data=str(v).encode()))
        else:
            tags.delall(f"TXXX:{k}")
            if v:
                tags.add(TXXX(encoding=3, desc=k, text=str(v)))
    tags.save(path, v2_version=3)


def apply_tags(filepath: str, meta: dict, artwork_bytes: bytes | None) -> None:
    """Writes all of a match's tags to the .mp3/.m4a/.opus at filepath in place --
    no filing/renaming. Used both by write_tags (below, for a fresh
    download) and by library_scan's repair pass (for an already-correctly-
    filed track that's just missing metadata)."""
    fields = {
        "title": meta.get("title", ""),
        "artist": meta.get("artist", ""),
        # Album artist: what music players group by, so a "feat." track
        # still shows up under the main artist instead of as a separate one.
        "album_artist": album_artist(meta),
        "album": meta.get("album"),
        "year": meta.get("year"),
        "genre": meta.get("genre"),
        "track": meta.get("track_number"),
        "disc": meta.get("disc_number"),
        RECORDING_ID: meta.get("mb_recording_id"),
        "artwork": artwork_bytes,
    }
    for key, desc in MB_TXXX.items():
        fields[desc] = meta.get(key)
    write_fields(filepath, fields)


def write_tags(filepath: str, meta: dict, artwork_bytes: bytes | None, library_root: str) -> str:
    """Writes tags to the .opus/.m4a/.mp3 at filepath, files it into
    <library_root>/<Artist>/<Album>/<Track> - <Title>.<ext>, and returns the new path."""
    apply_tags(filepath, meta, artwork_bytes)

    new_path = build_target_path(meta, library_root, os.path.splitext(filepath)[1].lower())
    os.makedirs(os.path.dirname(new_path), exist_ok=True)

    if os.path.abspath(new_path) != os.path.abspath(filepath):
        # Copy next to the target under a hidden temporary name, then rename it into place: the source is often a
        # local temp dir and the destination a network share (a different filesystem, so no direct rename), and a
        # restart in the middle of the copy must never leave half a song in the library (it would then count as
        # "already there"). A leftover hidden .part file is ignored by Navidrome.
        part = os.path.join(os.path.dirname(new_path), "." + os.path.basename(new_path) + ".part")
        shutil.copy2(filepath, part)   # like shutil.move across filesystems: keeps mode and times
        os.replace(part, new_path)
        os.remove(filepath)

    return new_path


# ---------------------------------------------------------------------------
# Album identity. Navidrome groups songs into an album by the tags, not the
# folder: by MusicBrainz album id when present, otherwise by album artist +
# album name + date. One folder whose songs disagree on any of those shows
# up as two (or more) albums -- e.g. older downloads without an album artist
# or MusicBrainz id next to newer ones with them.
# ---------------------------------------------------------------------------

ALBUM_FIELDS = ("album", "album_artist", "year", "MusicBrainz Album Id", "MusicBrainz Release Group Id",
                "MusicBrainz Album Artist Id")


def read_album_identity(path: str) -> dict | None:
    """The album-level tags of an .mp3/.m4a ({field: value}), or None if unreadable."""
    fields = read_fields(path)
    return {k: fields[k] for k in ALBUM_FIELDS} if fields is not None else None


def _write_album_identity(path: str, ident: dict) -> None:
    write_fields(path, {k: ident.get(k, "") for k in ALBUM_FIELDS})


def unify_album_folder(album_dir: str, reference_path: str | None = None) -> int:
    """Makes every .mp3/.m4a in `album_dir` agree on album name, album artist,
    year and MusicBrainz album ids, so a player shows the folder as one
    album. Titles, track numbers and track artists are never touched.

    The identity copied to the others comes from `reference_path` (the song
    just downloaded) if given; otherwise from the songs themselves: those
    with a MusicBrainz album id win (the most common one), else the most
    common combination. A missing album artist is filled from the folder
    artist's name. Returns how many files were changed."""
    try:
        names = os.listdir(album_dir)
    except OSError:
        return 0
    paths = [os.path.join(album_dir, n) for n in names if is_taggable(n)]
    fields = {p: read_fields(p) for p in paths}
    fields = {p: f for p, f in fields.items() if f is not None}
    idents = {p: {k: f[k] for k in ALBUM_FIELDS} for p, f in fields.items()}
    if not idents:
        return 0

    if reference_path and idents.get(reference_path):
        ref = dict(idents[reference_path])
    else:
        with_mb = [i for i in idents.values() if i.get("MusicBrainz Album Id")]
        pool = with_mb or list(idents.values())
        counts: dict = {}
        for i in pool:
            key = tuple(sorted(i.items()))
            counts[key] = counts.get(key, 0) + 1
        ref = dict(max(counts, key=counts.get))
        # Fill gaps from any song that has the field (e.g. year missing on one).
        for field in ref:
            if not ref[field]:
                ref[field] = next((i[field] for i in idents.values() if i.get(field)), "")
    if not ref.get("album_artist"):
        ref["album_artist"] = os.path.basename(os.path.dirname(album_dir.rstrip(os.sep)))
    if not ref.get("album"):
        ref["album"] = os.path.basename(album_dir.rstrip(os.sep))

    changed = set()
    for path, ident in idents.items():
        if ident != ref:
            try:
                _write_album_identity(path, ref)
                changed.add(path)
            except Exception:
                pass

    # Disc numbers: a song with none shows up as "disc 0" next to "disc 1"
    # songs, so the album looks like it has two discs. Give the ones without
    # a disc number disc 1 (the only sensible guess; real multi-disc albums
    # get proper disc numbers from MusicBrainz).
    discs = {p: _num(f.get("disc")) for p, f in fields.items()}
    if any(discs.values()):
        for path, disc in discs.items():
            if not disc:
                try:
                    write_fields(path, {"disc": "1"})
                    changed.add(path)
                except Exception:
                    pass
    return len(changed)


