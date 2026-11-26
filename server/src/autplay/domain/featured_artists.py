"""Move explicit title credits as a pair so the guest is never lost."""

import re

_MARK = r"(?:feat(?:uring)?|ft)\.?\s+(?!of\b)"
_GUEST = r"[^()[\]|]+?"
_SEPARATOR = r"(?:\s*\|\s*|\s+\+\s*|\s+[-\u2013\u2014]\s+)"
_DETAIL = r"(?:(?:19|20)\d\d|prod\.\s+[^()[\]|]+?|(?:[^()[\]|]+?\s+)?(?:cover|remix))"
_MIXED = re.compile(rf"\(({_GUEST})[,;]?\s+{_MARK}({_GUEST})\)", re.I)
_WRAPPED = re.compile(rf"\[\s*({_GUEST})\s+{_MARK}({_GUEST})\s*\]", re.I)
_FEATURE = re.compile(
    rf"(?:{_SEPARATOR}?\s*([\[(])\s*{_MARK}({_GUEST})\s*([])]))"
    rf"|(?:(?:{_SEPARATOR}|\s+){_MARK}({_GUEST})(?=\s*(?:$|[\[(|]|\s#|\s[-\u2013\u2014]\s)))",
    re.I,
)
_JOIN = re.compile(r"\s*(?:[,;]|\s(?:feat(?:uring)?\.?|ft\.?|&|\u00d7|x)\s)\s*", re.I)
_SPACE_TAG = re.compile(r"(?:(?:&#x0*20;|&#0*32;|&nbsp;)\s*)+$", re.I)
_OPEN = re.compile(r"\s*[(\[](?=feat|ft)", re.I)
_IN_CREDIT = re.compile(r"\b(?:feat(?:uring)?|ft)\.?\s", re.I)


def _members(artist: str) -> list[str]:
    return [p.strip(" ()[]").casefold() for p in _JOIN.split(_OPEN.sub(" ", artist))]


def same_feature_credit(left: str, right: str) -> bool:
    return bool(
        (_IN_CREDIT.search(left) or _IN_CREDIT.search(right)) and _members(left) == _members(right)
    )


def primary_credit(artist: str) -> str:
    match = _IN_CREDIT.search(artist)
    return artist[: match.start()].rstrip(" ([") if match else artist


def featured_names(title: str, artist: str) -> tuple[str, str]:
    if not artist.strip() or artist.casefold() in {
        "<unknown>",
        "unknown artist",
        "неизвестный исполнитель",
    }:
        return title, artist
    parts: list[str] = []
    additions: list[str] = []
    if not re.search(r"f(?:eat|t)", title, re.I):
        return title, artist

    def separate_note(match: re.Match[str]) -> str:
        detail, guest = match.groups()
        if detail.casefold() == primary_credit(artist).casefold():
            return f"(feat. {guest})"
        if re.fullmatch(_DETAIL, detail, re.I):
            return f"({detail}) (feat. {guest})"
        return match[0]

    known = set(_members(artist))
    wrapped = _WRAPPED.fullmatch(title)
    # The source can wrap the entire song label. A repeated artist alone supplies no title.
    if (
        wrapped
        and wrapped[1].strip().casefold() not in known
        and not re.fullmatch(_DETAIL, wrapped[1], re.I)
    ):
        source = title[1:-1].strip()
    else:
        source = _MIXED.sub(separate_note, title)
    end = 0
    for match in _FEATURE.finditer(source):
        before = source[: match.start()].rstrip()
        if (
            not before
            or before.count("(") != before.count(")")
            or before.count("[") != before.count("]")
        ):
            continue
        opening, bracketed, close, bare = match.groups()
        if opening and {"(": ")", "[": "]"}[opening] != close:
            continue
        if before[-1].isdigit() and re.match(
            r"\s+ft\s+(?:under|tall|high|wide|long|deep|apart)\b", match[0], re.I
        ):
            continue
        guest = _SPACE_TAG.sub("", (bracketed or bare).strip()).strip()
        if not guest or not any(c.isalnum() for c in guest):
            continue
        names = [p for p in _JOIN.split(guest) if p]
        new = []
        for name in names:
            if name.casefold() not in known:
                known.add(name.casefold())
                new.append(name)
        if new:
            additions.append(guest if new == names else ", ".join(new))
        parts.append(source[end : match.start()] + " ")
        end = match.end()
    if not end:
        return title, artist
    clean = " ".join("".join([*parts, source[end:]]).split())
    join = ", " if _IN_CREDIT.search(artist) else " feat. "
    credit = artist + join + ", ".join(additions) if additions else artist
    return (clean, credit) if clean and len(credit) <= 500 else (title, artist)
