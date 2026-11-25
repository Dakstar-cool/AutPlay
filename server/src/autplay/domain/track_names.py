"""Display labels are derived from source evidence independently of recording identity."""

import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from autplay.domain.artist_credits import (
    RecordingCredit,
    declared_credit,
    display_credit,
    recording_credit,
    single_stage_credit,
    trim_repeated_credit,
)

_AUDIO = re.compile(r"\.(?:mp3|flac|m4a|aac|ogg|oga|opus|wav|aif|aiff|alac|wma|aifc)$", re.I)
_BAND = re.compile(r"(?<![\w])25\s*(?:[/‐‑–—-]|\s)\s*17(?![\w])")
_TYPOGRAPHY = str.maketrans("’‘‐‑–—", "''----")

_SEPARATOR = re.compile(r"\s+[-–—]\s+|_[-–—]_")
_SNAKE_WORD = re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*")
_NOTICE = r"(?:official (?:music video|audio|video|lyrics? video|visualizer)|mood video|официальный клип|официальное видео)"
_DETAIL = r"(?:19|20)\d{2}|remaster(?:ed)?(?: \d{4})?|hd|4k|with lyrics|feat\.? [^\[\]()]+"
_GROUP_NOTICE = re.compile(
    r"(?:\s+[|–—-]\s*)?([\[(])\s*"
    + _NOTICE
    + r"(?:\s*[-–—/|:,]\s*([^\[\]()]+?)|\s+("
    + _DETAIL
    + r"))?\s*!?\s*([\])])",
    re.I,
)
_GROUP_NOTICE_AFTER_DETAIL = re.compile(
    r"([\[(])\s*([^\[\]()]+?)\s*(?:[/|,]|\s[-–—]\s)\s*" + _NOTICE + r"\s*([\])])", re.I
)
_SEPARATE_NOTICE = re.compile(
    r"""(?:\s+[-–—]\s*|\s*\|\s*|(?<=[)\]"”»])\s+)"""
    + _NOTICE
    + r"(?:\s+((?:19|20)\d{2}))?(?:\s+(?:HD|4K))?(?:\s*\|(?=\s*$))?"
    + r"(?=\s*(?:$|[\[(|]|\s[-–—]\s))",
    re.I,
)
_DERIVED = {"FILENAME", "SOURCE_LABEL"}
_HCWW_VIDEO_CREDIT = re.compile(
    r"\s+[-–—|]\s*HC WORLDWIDE\s*\(OFFICIAL (?:FULL HD|HD|4K) VERSION HCWW\)\s*$", re.I
)
_POSSIBLE_DECORATION = re.compile(r'[_@|–—\[\]()"”»-]')
_VENUE_CREDIT = re.compile(r"\s+(?:live\s+@\s*([^@|()\[\]]{1,80})|@\s*(16\s+тонн))$", re.I)
_SESSION_CREDIT = re.compile(r"\s+[|–—-]\s*([^\W_][\w '&’.-]{0,60}\s+sessions)$", re.I)
DISPLAY_VERSION = "display-v7"


def performance_title(title: str) -> str:
    """Keep the venue or session as an edition qualifier on a provisional title."""
    title = _VENUE_CREDIT.sub(lambda m: f" (Live at {m[1] or m[2]})", title)
    return _SESSION_CREDIT.sub(r" (\1)", title)


def strip_video_credit(title: str) -> str:
    title = _HCWW_VIDEO_CREDIT.sub("", title) or title
    return re.sub(
        r"([\[(])(uncensored|censored|explicit|clean)\s+official\s+(?:music\s+)?video([\])])",
        r"\1\2\3",
        title,
        flags=re.I,
    )


def display(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def search_label(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).translate(_TYPOGRAPHY)
    return _BAND.sub("25/17", " ".join(value.split())).casefold()


def _unpack_words(label: str) -> str:
    if "_" not in label:
        return label
    words = label.split("_")
    if len(words) < 2 or not all(
        _SNAKE_WORD.fullmatch(word) or word.casefold() in {"ft.", "feat."} for word in words
    ):
        return label
    if len(words) == 2 and any(len(w) < 2 or not w.isalpha() or w.isupper() for w in words):
        return label
    if sum(any(c.isalpha() for c in word) for word in words) < 2:
        return label
    return " ".join(words)


def _remove_notice(match: re.Match[str]) -> str:
    opening, *details, closing = match.groups()
    if (opening, closing) not in {("(", ")"), ("[", "]")}:
        return match[0]
    detail = next((value.strip() for value in details if value), "")
    if detail and detail.casefold() not in {"hd", "4k", "with lyrics"}:
        return f" {opening}{detail}{closing} "
    return " "


def track_names(
    title: str | None,
    artist: str | None,
    *,
    filename: bool = True,
    source_id: str | None = None,
    catalog: RecordingCredit | None = None,
) -> tuple[str | None, str | None]:
    """Clean provisional source labels; a verified catalog title keeps its published spelling."""
    name = display(title) if title else title
    label = display(artist) if artist else artist
    linked = catalog if catalog and label and catalog.matches(label) else None
    credit = (
        linked.preferred
        if linked
        else display_credit(_BAND.sub("25/17", label))
        if label
        else label
    )
    if not filename or not name:
        return name, credit
    is_file = bool(_AUDIO.search(name))
    stage = (
        linked.single
        if linked
        else single_stage_credit(credit) or (declared_credit(label) if label else None)
    )
    if not stage and not is_file and not _POSSIBLE_DECORATION.search(name):
        return name, credit
    stem = _AUDIO.sub("", name) if is_file else name
    if (
        source_id
        and source_id.isascii()
        and source_id.isdecimal()
        and stem.endswith("_" + source_id)
    ):
        stem = stem[: -len(source_id) - 1] or stem
    if is_file:
        stem = display(stem.replace("_", " ")) or name
    elif re.search(r"_[-–—]_", stem):
        stem = " - ".join(_unpack_words(part) for part in re.split(r"_[-–—]_", stem))
    # First remove wrappers so 'Artist - Title - Official Video' retains the recording title.
    groups = _GROUP_NOTICE_AFTER_DETAIL.sub(
        _remove_notice, _GROUP_NOTICE.sub(_remove_notice, strip_video_credit(stem))
    )
    stem = display(_SEPARATE_NOTICE.sub(lambda m: f" ({m[1]}) " if m[1] else " ", groups)) or stem
    known = (
        search_label(credit) if credit and credit not in {"Unknown artist", "<unknown>"} else None
    )
    while (separator := _SEPARATOR.search(stem)) is not None:
        prefix, rest = stem[: separator.start()], stem[separator.end() :]
        matches = known and (
            (stage is not None and stage.key.fullmatch(prefix) is not None)
            or search_label(display_credit(prefix)) == known
            or search_label(display_credit(_unpack_words(prefix))) == known
        )
        infer = is_file and not known and not _SEPARATOR.search(rest)
        if not rest or not prefix or not (matches or infer):
            break
        if infer:
            credit = display_credit(_BAND.sub("25/17", prefix))
            stage = single_stage_credit(credit)
            known = search_label(credit)
        stem = rest
    if not is_file and search_label(stem) != known:
        stem = _unpack_words(stem)
    if stage:
        stem = stage.trim_title(stem)
    elif credit and known:
        stem = trim_repeated_credit(stem, credit)
    return performance_title(stem or name), credit


def project_names(
    document: dict[str, Any],
    raw_title: str | None,
    raw_artist: str | None,
    catalog: RecordingCredit | None = None,
) -> dict[str, Any]:
    fields = dict(document.get("fields", {}))
    proofs = dict(document.get("provenance", {}))
    confirmed = document.get("catalog_identity_v1") or {}
    canonical = confirmed.get("fields") or {}
    if not (
        canonical.get("mb_recording_id")
        and canonical["mb_recording_id"] == fields.get("mb_recording_id")
        and canonical.get("mb_release_id")
        and canonical["mb_release_id"] == fields.get("mb_release_id")
    ):
        canonical = {}
    if canonical:
        for kind in ("track_members", "recording_members"):
            linked = recording_credit(confirmed.get(kind))
            if linked and linked.matches(str(canonical.get("artist") or "")):
                catalog = linked
                break
    artist_proof = proofs.get("artist") or {}
    hint = fields.get("artist", raw_artist)
    if artist_proof.get("source") in _DERIVED:
        hint = raw_artist
    if (
        canonical.get("artist")
        and not artist_proof.get("locked")
        and artist_proof.get("source") != "USER"
        and not ("artist" in fields and fields["artist"] is None)
    ):
        hint = canonical["artist"]
    originals = dict(document.get("name_originals_v1", {}))
    # A normalized embedded tag may be the only place the two roles were declared.
    if (
        not canonical
        and isinstance(hint, str)
        and not artist_proof.get("locked")
        and artist_proof.get("source") in {"EMBEDDED", "SOURCE_NATIVE"}
        and artist_proof.get("normalization_version")
        in {"display-v3", "display-v4", "display-v5", "display-v6", DISPLAY_VERSION}
    ):
        original = (originals.get("artist") or {}).get("value")
        declared = declared_credit(original) if isinstance(original, str) else None
        if declared and declared.stage.casefold() == hint.casefold():
            hint = original
    native_id = (document.get("acquisition_evidence") or {}).get("source_id")
    title, artist = track_names(
        raw_title,
        hint,
        source_id=native_id if isinstance(native_id, str) else None,
        catalog=catalog,
    )
    changed = False
    for key, fallback, raw in (("artist", artist, raw_artist), ("title", title, raw_title)):
        old = fields.get(key)
        proof = proofs.get(key) or {}
        if proof.get("locked") or proof.get("source") == "USER" or (key in fields and old is None):
            continue
        if canonical.get(key):
            value = canonical[key]
            if key == "artist" and catalog and catalog.matches(value):
                value = catalog.preferred
            source, source_id = "MUSICBRAINZ", canonical["mb_recording_id"]
        elif key in fields and proof.get("source") not in _DERIVED:
            if not isinstance(old, str):
                continue
            value = display(old)
            if key == "artist":
                value = (
                    catalog.preferred
                    if catalog and catalog.matches(value)
                    else display_credit(_BAND.sub("25/17", value))
                )
            if key == "title" and proof.get("source") in {"EMBEDDED", "SOURCE_NATIVE"}:
                value = fallback if old == raw_title else track_names(old, hint, catalog=catalog)[0]
            if not proof or value == old:
                continue
            source, source_id = proof["source"], proof["source_id"]
        else:
            value = fallback
            source = "FILENAME" if raw_title and _AUDIO.search(raw_title) else "SOURCE_LABEL"
            source_id = "track-description-v7"
            if value == raw and key not in fields:
                continue
        if value == old and (source != "MUSICBRAINZ" or proof.get("source") == source):
            continue
        if value is None and proof.get("source") in _DERIVED:
            fields.pop(key)
            proofs.pop(key)
        elif isinstance(value, str) and 0 < len(value) <= 500 and all(ord(c) >= 32 for c in value):
            fields[key] = value
            proofs[key] = {
                "source": source,
                "source_id": source_id,
                "observed_at": proof.get("observed_at")
                if proof.get("source") == source
                else datetime.now(UTC).isoformat(),
                "locked": False,
                "normalization_version": DISPLAY_VERSION,
            }
        else:
            continue
        originals.setdefault(
            key,
            {
                "present": key in document.get("fields", {}),
                "value": old,
                "provenance": proof or None,
            },
        )
        changed = True
    if not changed:
        return document
    result = {**document, "fields": fields, "provenance": proofs, "name_originals_v1": originals}
    # Match the JSONB column's serialized size limit, including the original descriptive evidence.
    return result if len(json.dumps(result, ensure_ascii=False).encode()) <= 65536 else document
