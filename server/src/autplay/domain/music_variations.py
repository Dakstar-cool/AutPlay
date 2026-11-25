"""Recognize qualified version labels without treating song titles as version tags."""

import re

KINDS = {"COVER", "REMIX", "LIVE"}
_LABEL = re.compile(r"(?:\s*[\[(]([^\[\]()]+)[])]|\s+[-\u2013\u2014]\s+([^\u2013\u2014-]+))\s*$")
_LIVE = re.compile(
    r"live(?: version| performance| session|\s+(?:at|in|from|on)\s+.+)?|"
    r"концертная(?: версия| запись)?|живое (?:исполнение|выступление)(?: (?:в|на) .+)?",
    re.I,
)
_LABELS = (
    ("COVER", re.compile(r"(?:.+\s+)?(?:cover|кавер)|(?:cover|кавер|originally by)\s+.+", re.I)),
    ("REMIX", re.compile(r"(?:.+\s+)?(?:remix|ремикс)", re.I)),
    ("LIVE", _LIVE),
)


def split_label(title: str) -> tuple[str, str] | None:
    match = _LABEL.search(title)
    if match:
        return title[: match.start()].rstrip(), (match[1] or match[2]).strip()
    return None


def variation_kind(
    role: str | None, live: bool, kind: str | None, version: str | None, title: str | None
) -> str | None:
    if role in {"REMIX", "COVER"}:
        return role
    if role == "VERSION" and live:
        return "LIVE"
    if kind in KINDS:
        return kind
    labels = [version or ""]
    base = (title or "").strip()
    labels.extend(part.strip() for part in base.split("|")[1:] if _LIVE.fullmatch(part.strip()))
    while part := split_label(base):
        base, suffix = part
        labels.append(suffix)
    return next((k for k, pattern in _LABELS if any(pattern.fullmatch(s) for s in labels)), None)
