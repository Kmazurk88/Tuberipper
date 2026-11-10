"""Title/artist comparison helpers shared by the metadata sources."""

import re
from difflib import SequenceMatcher


def text_score(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower().strip(), (b or "").lower().strip()).ratio()


# Every bracketed part of a track name: "(feat. Akon)", "[Continuous Mix]",
# "(Remastered 2011)". iTunes puts version info in these, often after the
# feat. credit, so stripping only a trailing "(feat. X)" left
# "Sexy Bitch (feat. Akon) [Continuous Mix]" unmatchable against "Sexy Bitch".
BRACKETS_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
VERSION_RE = re.compile(
    r"\b(remix|mix|edit|rework|bootleg|vip|dub|live|acoustic|unplugged|instrumental|"
    r"karaoke|cover|in the style of|originally (made famous|performed)|tribute|lullaby|"
    r"sped up|slowed|nightcore|demo|a cappella|acapella|8d|8d audio|reverb|reverbed|bass boosted?|drumless|backing track|no vocals|minus one)\b",
    re.IGNORECASE,
)


def base_title(name: str) -> str:
    """Track name without any bracketed parts and dangling " - X Remix"
    suffix, for comparing "the same song" across versions."""
    t = BRACKETS_RE.sub("", name or "")
    t = re.sub(r"\s+-\s+[^-]*$", lambda m: "" if VERSION_RE.search(m.group(0)) else m.group(0), t)
    return t.strip() or (name or "").strip()


# Version labels that still mean "the normal song" (dance releases label
# the original "Original Mix", singles carry a "Radio Edit").
NOT_ALT_RE = re.compile(
    r"\b(radio edit|radio mix|radio version|original mix|original version|album version|"
    r"single version|single edit|clean( version)?|explicit( version)?|main( version)?)\b",
    re.IGNORECASE,
)


def is_alt_version(track_name: str, wanted_title: str = "") -> bool:
    """True for a remix/live/acoustic/karaoke/... version -- unless the user
    asked for that kind of version themselves."""
    found = {m.group(0).lower() for m in VERSION_RE.finditer(NOT_ALT_RE.sub("", track_name or ""))}
    wanted = {m.group(0).lower() for m in VERSION_RE.finditer(wanted_title or "")}
    return bool(found - wanted)


_STOP_WORDS = {"the", "and", "feat", "featuring", "ft", "with"}


def _name_tokens(name: str) -> set:
    s = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower().replace("&", " and "))
    return {t for t in s.split() if t not in _STOP_WORDS}


def artist_ok(wanted: str, candidate: str) -> bool:
    """The same artist written differently: "The Jackson 5" / "Jackson 5", "Daryl Hall & John Oates" / "Daryl Hall, John Oates"."""
    w, c = _name_tokens(wanted), _name_tokens(candidate)
    return bool(w and c) and (w <= c or c <= w)
