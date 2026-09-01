"""Standalone bounded TXT playlist parsing and normalization."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import PlaylistItem

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 10_000
MAX_ROW_BYTES = 64 * 1024
MAX_FIELD_CHARS = 4_000
_NUMBERED_LINE = re.compile(r"^\s*[1-9][0-9]{0,6}[.)]\s+(?P<track>.+?)\s*$")
_SECTION_MARKER = re.compile(r"^={3,}.*={3,}$")


class PlaylistParseError(ValueError):
    """Stable outer-envelope error for a local TXT collection."""


@dataclass(frozen=True, slots=True)
class NormalizationStats:
    numbered_prefix_count: int
    section_marker_count: int
    unnumbered_line_count: int


@dataclass(frozen=True, slots=True)
class ParsedPlaylist:
    encoding: str
    rows: tuple[PlaylistItem, ...]

    @property
    def valid_count(self) -> int:
        return sum(row.error_code is None for row in self.rows)

    @property
    def malformed_count(self) -> int:
        return len(self.rows) - self.valid_count


def _decode(payload: bytes) -> tuple[str, str]:
    if not payload or len(payload) > MAX_IMPORT_BYTES:
        raise PlaylistParseError("import.input_size_invalid")
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise PlaylistParseError("import.encoding_unsupported")


def parse_playlist(payload: bytes) -> ParsedPlaylist:
    """Parse artist<TAB>title[<TAB>album] or artist - title with row isolation."""

    text, encoding = _decode(payload)
    rows: list[PlaylistItem] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if len(raw_line.encode("utf-8")) > MAX_ROW_BYTES:
            rows.append(PlaylistItem(number, "", "", error_code="import.row_too_large"))
            continue
        fields = [value.strip() for value in line.split("\t")]
        if number == 1 and [value.casefold() for value in fields] in (
            ["artist", "title"],
            ["artist", "title", "album"],
        ):
            continue
        album: str | None = None
        if len(fields) in {2, 3}:
            artist, title = fields[:2]
            album = fields[2] if len(fields) == 3 and fields[2] else None
        elif " - " in line:
            artist, title = (value.strip() for value in line.split(" - ", 1))
        else:
            rows.append(PlaylistItem(number, "", "", error_code="import.txt_row_malformed"))
            continue
        if not artist or not title:
            error = "import.identity_missing"
        elif (
            len(artist) > MAX_FIELD_CHARS
            or len(title) > MAX_FIELD_CHARS
            or (album is not None and len(album) > MAX_FIELD_CHARS)
        ):
            error = "import.field_too_large"
        else:
            error = None
        rows.append(PlaylistItem(number, artist, title, album, error))
    if len(rows) > MAX_IMPORT_ROWS:
        raise PlaylistParseError("import.row_limit_exceeded")
    if not rows:
        raise PlaylistParseError("import.txt_empty")
    return ParsedPlaylist(encoding, tuple(rows))


def normalize_numbered_collection(payload: bytes) -> tuple[bytes, NormalizationStats]:
    """Strip list ordinals and section markers after enforcing source bounds."""

    text, _encoding = _decode(payload)
    physical_rows = text.splitlines()
    if len(physical_rows) > MAX_IMPORT_ROWS:
        raise PlaylistParseError("import.row_limit_exceeded")
    if any(len(row.encode("utf-8")) > MAX_ROW_BYTES for row in physical_rows):
        raise PlaylistParseError("import.row_too_large")
    normalized: list[str] = []
    numbered = markers = unnumbered = 0
    for raw_line in physical_rows:
        line = raw_line.strip()
        if not line:
            continue
        if _SECTION_MARKER.fullmatch(line):
            markers += 1
            continue
        match = _NUMBERED_LINE.fullmatch(line)
        if match is None:
            normalized.append(raw_line)
            unnumbered += 1
        else:
            normalized.append(match.group("track"))
            numbered += 1
    result = ("\n".join(normalized) + "\n").encode("utf-8")
    return result, NormalizationStats(numbered, markers, unnumbered)


def _publish_new_file(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".local-music-playlist-", suffix=".part", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output_file:
            output_file.write(payload)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.link(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate or normalize a bounded TXT playlist.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--normalized-output", type=Path)
    parser.add_argument("--include-artists", action="store_true")
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    stats: NormalizationStats | None = None
    try:
        payload = options.input.read_bytes()
        if options.normalized_output is not None:
            if options.input.resolve() == options.normalized_output.resolve():
                raise OSError
            payload, stats = normalize_numbered_collection(payload)
        parsed = parse_playlist(payload)
        if options.normalized_output is not None:
            _publish_new_file(options.normalized_output, payload)
    except PlaylistParseError as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=__import__("sys").stderr)
        return 2
    except OSError:
        print(json.dumps({"error": "import.file_unavailable"}), file=__import__("sys").stderr)
        return 2
    artists: dict[str, int] = {}
    valid = [row for row in parsed.rows if row.error_code is None]
    for row in valid:
        artists[row.artist] = artists.get(row.artist, 0) + 1
    summary: dict[str, object] = {
        "encoding": parsed.encoding,
        "malformed_count": parsed.malformed_count,
        "row_count": len(parsed.rows),
        "valid_count": parsed.valid_count,
        "duplicate_valid_row_count": len(valid)
        - len({(row.artist, row.title, row.album) for row in valid}),
        "artist_count": len(artists),
        "normalization": asdict(stats) if stats else None,
    }
    if options.include_artists:
        summary["artists"] = [
            {"name": name, "track_count": count}
            for name, count in sorted(
                artists.items(), key=lambda item: (-item[1], item[0].casefold())
            )
        ]
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
