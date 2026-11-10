"""Merges two library folders that turned out to be the same thing under
different names -- a duplicate artist spelling, or an album that got
downloaded twice under a slightly different edition name (e.g. "The
Lumineers" vs "The Lumineers (Deluxe Edition)").

Two folders being merged can each be an artist folder (containing album
subfolders) or an album folder (containing tracks) -- either way, `plan_merge`
walks both sides once and figures out what to do.

`dir_a` is always the keeper: its own copy of a duplicate track always wins
(no format/size guessing), and once the merge lands, every .mp3/.m4a that
ends up in the folder -- both dir_a's own tracks and whatever moved in from
dir_b -- gets its tags (and track number) rewritten to agree on one album
identity, so the result isn't just visually merged on disk but reads as a
single album to tag-based players like Navidrome. Track numbers come from
whichever real tracklist (looked up by album name, trying every distinct
album name seen among the merging tracks) covers the most of the actual
tracks present; anything that doesn't match a slot in that tracklist is
appended after the highest matched number instead of colliding with it.
Other formats (flac/ogg/etc) are still merged and deduped, just not
retagged -- this app's own downloads are .m4a (older ones .mp3), and tag
writers for everything else are more than this needs right now.

Safety model: every caller must run with dry_run=True first and show the
plan to the user before calling again with dry_run=False. Nothing is moved,
removed, or retagged while dry_run=True.
"""

import os
import shutil
from difflib import SequenceMatcher

from . import metadata
from .tagger import AUDIO_EXTENSIONS, _fold, is_taggable, read_fields, sanitize_filename, write_fields

TRACK_MATCH_THRESHOLD = 0.72


def _text_score(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _track_key(entry: str) -> str | None:
    """Fold-normalized key for an audio file's name (ignoring extension), or
    None if `entry` isn't a recognized audio file."""
    stem, ext = os.path.splitext(entry)
    if ext.lower() not in AUDIO_EXTENSIONS:
        return None
    return _fold(stem)


def _listdir_safe(d: str) -> list[str]:
    try:
        return os.listdir(d)
    except OSError:
        return []


def _same_path(p1: str, p2: str) -> bool:
    """Whether two paths refer to the same file -- checked via identity
    (os.path.samefile), not string equality, because a plain string/normpath
    comparison gives the wrong answer on a case-insensitive filesystem
    (Windows, SMB): two differently-cased paths can be the exact same file
    on disk while comparing unequal as strings."""
    if os.path.normpath(p1) == os.path.normpath(p2):
        return True
    try:
        return os.path.exists(p1) and os.path.exists(p2) and os.path.samefile(p1, p2)
    except OSError:
        return False


def _read_tags(path: str) -> dict:
    """Reads the tags relevant to album identity from an .mp3/.m4a. Returns
    {} for other formats or anything unreadable -- tag reconciliation just
    skips those rather than failing the whole merge."""
    if not is_taggable(path):
        return {}
    fields = read_fields(path)
    if fields is None:
        return {}
    return {k: fields[k] for k in ("title", "artist", "album", "year", "genre", "artwork")}


def _write_album_identity(path: str, artist: str, album: str, year: str, genre: str,
                          artwork: bytes | None, track_number: int | None):
    fields = {"artist": artist, "album": album, "year": year, "genre": genre, "track": track_number}
    if artwork:
        fields["artwork"] = artwork
    write_fields(path, fields)


def _match_track_number(title: str, tracklist: list[str], used_numbers: set[int]) -> int | None:
    if not tracklist or not title:
        return None
    best_idx, best_score = None, 0.0
    for idx, candidate in enumerate(tracklist):
        score = _text_score(title, candidate)
        if score > best_score:
            best_score, best_idx = score, idx
    if best_idx is not None and best_score >= TRACK_MATCH_THRESHOLD and (best_idx + 1) not in used_numbers:
        return best_idx + 1
    return None


def _reconcile_album(dir_a: str, files: list[tuple[str, str]], dry_run: bool) -> list[dict]:
    """Rewrites (or, if dry_run, plans rewriting) ID3 tags on the mp3s that
    will directly reside in dir_a once this level's merge completes, so they
    agree on one album identity and carry non-colliding track numbers from a
    real tracklist. `files` is [(read_path, plan_path), ...]: read_path is
    where the file can be read from *right now* (its final dir_a location if
    it's already resident or this is a real run where the move already
    happened; its original dir_b location if this is a dry run, since
    nothing has actually moved yet) -- plan_path is always its eventual
    dir_a location, used for naming/relpath purposes only. Returns
    [{"from": relpath-in, "to": relpath-out, "track_number": n}, ...]."""
    tagged = [(read_path, plan_path, _read_tags(read_path)) for read_path, plan_path in files]
    tagged = [(r, p, t) for r, p, t in tagged if t]  # drop unreadable/untaggable
    if not tagged:
        return []

    reference = next((t for _, _, t in tagged if t.get("album")), None)
    if reference is None:
        return []  # nothing usable to build a consistent identity from

    ref_artist = reference.get("artist") or os.path.basename(os.path.dirname(dir_a))
    ref_album = reference.get("album") or os.path.basename(dir_a)
    ref_year = next((t.get("year") for _, _, t in tagged if t.get("year")), "")
    ref_genre = next((t.get("genre") for _, _, t in tagged if t.get("genre")), "")
    ref_artwork = next((t.get("artwork") for _, _, t in tagged if t.get("artwork")), None)

    # Try every distinct album name actually seen among the merging tracks
    # (e.g. both "The Lumineers" and "The Lumineers (Deluxe Edition)"), and
    # use whichever real tracklist covers more of the tracks actually
    # present -- not just whichever name happens to belong to the keeper.
    candidate_albums = {t["album"] for _, _, t in tagged if t.get("album")}
    candidate_albums.add(ref_album)

    best_tracklist, best_coverage = None, -1
    for album_name in candidate_albums:
        tl = metadata.lookup_album_tracklist(ref_artist, album_name)
        if not tl:
            continue
        coverage = sum(
            1 for _, _, t in tagged
            if t.get("title") and max((_text_score(t["title"], c) for c in tl), default=0) >= TRACK_MATCH_THRESHOLD
        )
        if coverage > best_coverage:
            best_coverage, best_tracklist = coverage, tl

    assignments: dict[str, int] = {}
    used_numbers: set[int] = set()
    leftovers = []
    for read_path, _, t in tagged:
        title = t.get("title") or os.path.splitext(os.path.basename(read_path))[0]
        number = _match_track_number(title, best_tracklist, used_numbers) if best_tracklist else None
        if number:
            used_numbers.add(number)
            assignments[read_path] = number
        else:
            leftovers.append(read_path)

    next_number = (max(used_numbers) + 1) if used_numbers else 1
    for read_path in sorted(leftovers, key=lambda p: os.path.basename(p).lower()):
        assignments[read_path] = next_number
        next_number += 1

    plan = []
    for read_path, plan_path, t in tagged:
        number = assignments[read_path]
        title = t.get("title") or os.path.splitext(os.path.basename(read_path))[0]
        ext = os.path.splitext(read_path)[1].lower()
        new_name = f"{number} - {sanitize_filename(title)}{ext}" if number else f"{sanitize_filename(title)}{ext}"
        new_path = os.path.join(dir_a, new_name)
        plan.append({
            "from": os.path.relpath(plan_path, dir_a),
            "to": os.path.relpath(new_path, dir_a),
            "track_number": number,
        })

        if dry_run:
            continue

        _write_album_identity(read_path, ref_artist, ref_album, ref_year, ref_genre, ref_artwork, number)

        if os.path.abspath(new_path) != os.path.abspath(read_path) and not os.path.exists(new_path):
            old_lrc = os.path.splitext(read_path)[0] + ".lrc"
            os.rename(read_path, new_path)
            if os.path.exists(old_lrc):
                os.rename(old_lrc, os.path.splitext(new_path)[0] + ".lrc")

    return plan


def plan_merge(dir_a: str, dir_b: str, dry_run: bool = True) -> dict:
    """Merges the contents of dir_b into dir_a (the keeper), recursing into
    matching subfolders (for an artist-level merge whose albums also need
    pairing up). On a duplicate track, dir_a's own copy always wins and
    dir_b's is dropped. Once files have settled into dir_a, .mp3/.m4a files directly in
    dir_a are tag-reconciled (see _reconcile_album).

    Returns {"moved": [[src, dst], ...], "kept_over_duplicate": [[keep, removed], ...],
    "ambiguous": [path, ...], "retagged": [{"from", "to", "track_number"}, ...]} --
    "ambiguous" lists anything left untouched because it couldn't be resolved
    automatically (e.g. a same-named non-audio file already present on both
    sides), for the user to handle by hand.
    """
    moved = []
    kept_over_duplicate = []
    ambiguous = []
    retagged = []

    dir_a_entries = _listdir_safe(dir_a)
    dir_b_entries = _listdir_safe(dir_b)
    dir_b_entries_set = set(dir_b_entries)
    # Names in dir_b already handled as part of resolving a specific audio
    # file (itself, or its own same-named .lrc) -- the generic leftover pass
    # at the end must skip these, or it can re-derive a path for an already-
    # moved file via fold-matching and stomp on the correct copy.
    consumed = set()

    # (read_path, plan_path) pairs for audio files that will directly reside
    # in dir_a once this level's merge completes -- used for tag
    # reconciliation at the end. plan_path is always the eventual dir_a
    # location; read_path is wherever the file can actually be read from
    # right now (see _reconcile_album's docstring for why they can differ
    # during a dry run). Starts with dir_a's own existing tracks (read_path
    # == plan_path, already there); entries added below as decisions are made.
    resident_audio = [
        (os.path.join(dir_a, e), os.path.join(dir_a, e)) for e in dir_a_entries
        if _track_key(e) is not None
    ]

    for entry in dir_b_entries:
        src = os.path.join(dir_b, entry)

        if os.path.isdir(src):
            key = _fold(entry)
            target_name = next(
                (e for e in dir_a_entries if os.path.isdir(os.path.join(dir_a, e)) and _fold(e) == key),
                None,
            )
            dst_dir = os.path.join(dir_a, target_name or entry)
            if not dry_run:
                os.makedirs(dst_dir, exist_ok=True)
            sub = plan_merge(dst_dir, src, dry_run=dry_run)
            moved += sub["moved"]
            kept_over_duplicate += sub["kept_over_duplicate"]
            ambiguous += sub["ambiguous"]
            retagged += sub["retagged"]
            consumed.add(entry)
            continue

        track_key = _track_key(entry)
        if track_key is None:
            continue  # handled in the sidecar/leftover pass below

        entry_lrc_name = os.path.splitext(entry)[0] + ".lrc"
        src_lrc = os.path.join(dir_b, entry_lrc_name) if entry_lrc_name in dir_b_entries_set else None
        consumed.add(entry)
        if src_lrc:
            consumed.add(entry_lrc_name)

        match_name = next((e for e in dir_a_entries if _track_key(e) == track_key), None)

        if match_name is None:
            # No collision -- move dir_b's copy (and its lyrics) into dir_a.
            dst = os.path.join(dir_a, entry)
            moved.append([src, dst])
            resident_audio.append((dst if not dry_run else src, dst))
            if not dry_run:
                shutil.move(src, dst)
            if src_lrc:
                dst_lrc = os.path.join(dir_a, entry_lrc_name)
                moved.append([src_lrc, dst_lrc])
                if not dry_run:
                    shutil.move(src_lrc, dst_lrc)
            continue

        # Collision -- dir_a's copy is the keeper, always. dir_b's copy is
        # dropped; its lyrics survive only if dir_a doesn't already have its
        # own for this track.
        keep = os.path.join(dir_a, match_name)
        kept_over_duplicate.append([keep, src])
        if not dry_run:
            keep_lrc = os.path.splitext(keep)[0] + ".lrc"
            if src_lrc and not os.path.exists(keep_lrc):
                shutil.move(src_lrc, keep_lrc)
            elif src_lrc and os.path.exists(src_lrc):
                os.remove(src_lrc)
            os.remove(src)

    # Anything left in dir_b that wasn't consumed above (folder art, an
    # orphaned lyrics file with no matching track, etc) -- move it over if
    # there's no name clash, otherwise leave it for manual attention.
    for entry in dir_b_entries:
        if entry in consumed:
            continue
        src = os.path.join(dir_b, entry)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(dir_a, entry)
        if os.path.exists(dst):
            ambiguous.append(src)
        else:
            moved.append([src, dst])
            if not dry_run:
                shutil.move(src, dst)

    retagged += _reconcile_album(dir_a, resident_audio, dry_run=dry_run)

    if not dry_run:
        _prune_empty(dir_b)

    return {"moved": moved, "kept_over_duplicate": kept_over_duplicate, "ambiguous": ambiguous, "retagged": retagged}


def _prune_empty(d: str):
    """Removes now-empty directories left behind by a completed merge,
    bottom-up. Leaves anything with real content behind untouched (e.g. an
    "ambiguous" file that couldn't be auto-resolved)."""
    for root, dirs, files in os.walk(d, topdown=False):
        if not dirs and not files:
            os.rmdir(root)
