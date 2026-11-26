"""Recognize source edition suffixes without guessing word boundaries in song names."""

import re

SESSION_LABEL = r"(?:[^\W_][\w '&\u2019.-]{0,60}\s+)?(?:session|ssesion)s?"
_SESSION = re.compile(
    rf"(?:\s*(?:\||\u2013|\u2014)\s*|\s+-\s+|\s*([\[(])\s*)({SESSION_LABEL})\s*([])]?)$",
    re.I,
)


def session_label(value: str) -> str | None:
    if not re.fullmatch(SESSION_LABEL, value, re.I):
        return None
    return re.sub(
        r"\bssesions?$",
        lambda m: "Sessions" if m[0].lower().endswith("s") else "Session",
        value,
        flags=re.I,
    )


def session_parts(title: str) -> tuple[str, str | None]:
    match = _SESSION.search(title)
    if not match or not match.start() or {"(": ")", "[": "]"}.get(match[1], "") != match[3]:
        return title, None
    return title[: match.start()].rstrip(), session_label(match[2])


_EDIT = (
    r"(?:sp(?:ed|eed)[ -]?up|speed|nightcore|"
    r"(?:(?:super|ultra)\s+)?slow(?:ed)?(?:[ -]?down)?(?:\s*(?:\+|&|and)\s*reverb)?|"
    r"(?:(?:high[ -]quality|hq)\s+)?instrumental)(?:\s+version)?"
)
_SUFFIX = re.compile(
    rf"(\s*\(\s*|\s*\[\s*|\s*\|\s*|\s+[-\u2013\u2014]\s+|\s+)({_EDIT})(\s*[])])?$", re.I
)
_LABEL = re.compile(_EDIT, re.I)
_LITERAL = {"speed", "slow", "speed up", "slow down", "instrumental"}
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")


def edit_parts(title: str, *, search: bool = False) -> tuple[str, str | None]:
    suffix = _SUFFIX.search(title)
    if not suffix or not suffix.start() or _LABEL.fullmatch(title):
        return title, None
    opening, value, closing = suffix.groups()
    if {"(": ")", "[": "]"}.get(opening.strip(), "") != (closing or "").strip():
        return title, None
    base = title[: suffix.start()].rstrip()
    value = " ".join(value.casefold().split())
    # Bare English words can be lyrics, as in "Take It Slow". Their base is only a search hint.
    if not opening.strip() and value in _LITERAL and not search and not _CYRILLIC.search(base):
        return title, None
    return base, edition_label(value)


def edition_label(label: str) -> str | None:
    if not _LABEL.fullmatch(label):
        return None
    value = " ".join(label.casefold().split()).removesuffix(" version")
    if value.endswith("instrumental"):
        edition = "Instrumental"
    elif value.startswith("sp"):
        edition = "Sped Up"
    elif value == "nightcore":
        edition = "Nightcore"
    else:
        prefix = value.split()[0]
        edition = (prefix.title() + " " if prefix in {"super", "ultra"} else "") + "Slowed"
        if value.endswith("reverb"):
            edition += " + Reverb"
    return edition


def edit_title(title: str) -> str:
    base, edition = edit_parts(title)
    return f"{base} ({edition})" if edition else title


def compact_spelling(title: str) -> str | None:
    """A search alias for a whole phrase. Preserve numbers, punctuation and short names."""
    words = title.casefold().replace("\u2019", "'").split()
    if len(words) < 2 or not all(all(part.isalpha() for part in w.split("'")) for w in words):
        return None
    joined = "".join(words).replace("'", "")
    return joined if len(joined) >= 12 else None


def joined_title_matches(left: str, right: str) -> bool:
    """Only ignore spacing when one source actually supplied a single long word."""
    if left.isalpha() and len(left) >= 12:
        return left.casefold() == compact_spelling(right)
    return right.isalpha() and len(right) >= 12 and right.casefold() == compact_spelling(left)
