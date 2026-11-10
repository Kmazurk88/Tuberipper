"""Best-effort splitting of a raw YouTube title into (artist, title, confidence)."""

import re
from collections import Counter

NOISE_PATTERNS = [
    r"\(official\s*(music\s*)?video\)",
    r"\(official\s*audio\)",
    r"\(official\)",
    r"[\(\[][^\)\]]*\blyrics?\b[^\)\]]*[\)\]]",  # (Lyrics), (Lyrics/Testo), [Lyric Video]
    r"\(lyric\s*video\)",
    r"\(visualizer\)",
    r"\(audio\)",
    r"\(hd\)",
    r"\(4k\)",
    r"\(explicit\)",
    r"\(clean\)",
    r"\(remastered.*?\)",
    r"\[official\s*(music\s*)?video\]",
    r"\[official\s*audio\]",
    r"\[lyrics?\]",
    r"\[hd\]",
    r"\[4k\]",
    r"\bofficial\s*(music\s*)?video\b",
    r"\bofficial\s*audio\b",
    r"[\(\[]lyrics?\s+in\s+description[\)\]]",
    r"[\(\[]lyrics?\s+below[\)\]]",
    r"[\(\[]official\s*(hd\s*|4k\s*)?(music\s*|lyric\s*)?video(\s*hd)?[\)\]]",
    r"[\(\[](music|lyric)\s*video[\)\]]",
    r"[\(\[](hq|video|audio\s*only)[\)\]]",
    # Fan edits: searching iTunes with these in the title only finds other
    # fan edits/compilations, never the original album.
    r"[\(\[][^\)\]]*\b(slowed|reverb|sped\s*up|nightcore|8d\s*audio|bass\s*boosted)\b[^\)\]]*[\)\]]",
    r"\b(slowed\s*(\+|&|and)?\s*reverb|sped\s*up|nightcore)\b",
    # Glued-on junk like "Don't Stop The Music-Official Video HighDefinition"
    # (no spaces around the dash, so the patterns above leave "-" behind).
    r"\s*-\s*official\b.*$",
    r"\b(high\s*definition|official\s*hd(\s*video)?|hd\s*video|full\s*hd)\b",
    # "(Official Archive Video)", "(Official 4K Video)": any official ... video.
    r"[\(\[][^\)\]]*\bofficial\b[^\)\]]*\bvideo\b[^\)\]]*[\)\]]",
    # "(4K Remaster)", "(Remastered 2009)", "[2011 Remaster]".
    r"[\(\[][^\)\]]*\bremaster(ed)?\b[^\)\]]*[\)\]]",
    # Where/when a performance was filmed: "(Thommys Popshow extra
    # 17.12.1983)", "(Live at Wembley 1986)", "[Top of the Pops]". Looking
    # these up finds nothing (or a live album); the song is the same.
    r"[\(\[][^\)\]]*(\b(popshow|top of the pops|concert|festival|tv|show|live\s+(at|from|on|in))\b|\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b)[^\)\]]*[\)\]]",
    # "(Vorsicht Musik!, 1983)": a show name and year -- but not a version
    # label with a year ("(Remix, 2019)").
    r"[\(\[](?![^\)\]]*\b(remix|mix|edit|version|acoustic|demo|cover|instrumental)\b)[^\)\]]*,\s*(19|20)\d{2}\s*[\)\]]",
]

# "Artist - Song - The 2015 Nobel Peace Prize Concert": a third dash-part
# naming the performance. Only stripped when it's a *third* part, so
# "Oasis - Live Forever" keeps its title.
PERFORMANCE_TAIL = re.compile(
    r"^(.+?\s[-–—]\s.+?)\s+[-–—]\s+[^-–—]*\b(concert|festival|live|session|sessions|performance|tour|show|tv)\b[^-–—]*$",
    re.IGNORECASE,
)

# "Artist: Title" -- the label channels (Fueled By Ramen etc.) use a colon.
COLON_SPLIT = re.compile(r"^([^:]{2,40}):\s+(.+)$")

# " ft. X" / " feat. X" / " featuring X" not inside brackets. On the artist
# side it's dropped (iTunes credits the main artist and puts "(feat. X)" in
# the track name); on the title side a trailing one is dropped too.
FEAT_TAIL = re.compile(r"\s+(ft\.?|feat\.?|featuring)\s+[^\(\[]*$", re.IGNORECASE)

DASH_SPLIT = re.compile(r"\s[-–—]\s")


def clean_title(raw: str) -> str:
    # "Artist - Song (Official Video) | Album Name": the part after " | " is
    # the album/channel/series, never part of the song title.
    if re.search(r"\s[-–—]\s", raw):
        raw = re.split(r"\s+[|｜]\s+", raw)[0]
    # "( Official Video )" -> "(Official Video)" so the bracket patterns match.
    cleaned = re.sub(r"([\(\[])\s+", r"\1", raw)
    cleaned = re.sub(r"\s+([\)\]])", r"\1", cleaned)
    # Two passes: removing "Official Video" from "(Official Video Remastered)"
    # leaves "( Remastered)", which the second pass then recognizes.
    for _ in range(2):
        for pattern in NOISE_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"([\(\[])\s+", r"\1", cleaned)
        cleaned = re.sub(r"\s+([\)\]])", r"\1", cleaned)
    cleaned = PERFORMANCE_TAIL.sub(r"\1", cleaned)
    cleaned = re.sub(r"[\(\[]\s*[\)\]]", "", cleaned)  # brackets emptied above
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–—|").strip()
    return cleaned


def _strip_feat(text: str) -> str:
    return FEAT_TAIL.sub("", text).strip() or text.strip()


def parse(raw_title: str):
    """Returns (artist, title, confidence) where confidence is 'high' or 'low'."""
    cleaned = clean_title(raw_title)

    parts = DASH_SPLIT.split(cleaned, maxsplit=1)
    if len(parts) == 2:
        artist, title = _strip_feat(parts[0]), _strip_feat(parts[1])
        if artist and title and len(artist) < 60:
            return artist, title, "high"

    colon = COLON_SPLIT.match(cleaned)
    if colon:
        artist, title = _strip_feat(colon.group(1)), _strip_feat(colon.group(2))
        if artist and title:
            return artist, title, "high"

    by_match = re.search(r"^(.*)\bby\b(.*)$", cleaned, flags=re.IGNORECASE)
    if by_match:
        title, artist = by_match.group(1).strip(), by_match.group(2).strip()
        if artist and title:
            return artist, title, "high"

    return "", cleaned, "low"


def parse_batch(raw_titles: list[str]):
    """Parses a whole playlist's raw titles at once, then corrects two
    failure modes plain parse() can't see on its own, both of which lead to
    a metadata lookup matching a completely unrelated artist:

    1. Reversed order. Most uploaders format a video title "Artist - Song",
       but some (typically third-party lyric-video channels) format it
       backwards, "Song - Artist". "Flowers In Your Hair - The Lumineers
       (Lyrics)" parses as artist="Flowers In Your Hair", title="The
       Lumineers" -- garbage that matched Mumford & Sons in one real case.

    2. No artist at all. Some videos in an otherwise consistent playlist
       have no "Artist - " prefix, e.g. just "This Must Be the Place (Naive
       Melody)". parse() correctly leaves the artist blank (confidence
       "low"), but an artist-blind metadata lookup on a title that a cover
       band also released under the same name can match *that* artist
       instead -- confirmed matching "Gloria" instead of The Lumineers.

    Across a whole playlist though, the real artist usually shows up as the
    parsed artist on most tracks, so that majority is used to fix both: (1)
    any track whose parsed *title* exactly matches the majority artist had
    its fields swapped -- flip them back; (2) any track with no parsed
    artist gets the majority artist filled in as a default, the same as if
    the user had used "apply default artist" by hand -- except this also
    covers jobs that skip the manual review step entirely (e.g. started
    straight from Search).

    Returns a list of (artist, title, confidence) in the same order as
    raw_titles."""
    parsed = [parse(t) for t in raw_titles]

    display_names: dict[str, str] = {}
    counts: Counter = Counter()
    for artist, _, _ in parsed:
        if not artist:
            continue
        key = artist.strip().lower()
        counts[key] += 1
        display_names.setdefault(key, artist.strip())

    if not counts:
        return parsed

    majority_key, majority_count = counts.most_common(1)[0]
    if majority_count < 2:
        return parsed  # not enough agreement to trust as "the" playlist artist
    majority_artist = display_names[majority_key]

    # Filling in a blank artist is a broader, riskier move than the swap
    # fix below (which only ever fires on an exact title match), since it
    # touches every blank-artist track regardless of whether it's really by
    # the majority artist. A genuinely mixed-artist batch with a plurality
    # (not majority) leader is exactly the case this must NOT fire on -- a
    # 2-of-4 "Radiohead" plurality wrongly claiming a blank-artist Coldplay
    # track was caught in testing. Require a real supermajority.
    total = sum(counts.values())
    fill_blanks = majority_count >= 3 and majority_count / total > 0.7

    fixed = []
    for artist, title, confidence in parsed:
        if not artist:
            if fill_blanks:
                artist = majority_artist
        elif artist.strip().lower() != majority_key and title.strip().lower() == majority_key:
            artist, title = title, artist
        fixed.append((artist, title, confidence))
    return fixed
