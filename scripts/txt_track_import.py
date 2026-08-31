"""Validate and preview an AutPlay TXT track collection without network access."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from autplay.domain.import_identity import (
    ImportEnvelope,
    ImportEnvelopeError,
    ImportFormat,
    parse_import,
)

_NUMBERED_LINE = re.compile(r"^\s*[1-9][0-9]{0,6}[.)]\s+(?P<track>.+?)\s*$")
_SECTION_MARKER = re.compile(r"^={3,}.*={3,}$")


@dataclass(frozen=True, slots=True)
class NormalizationStats:
    """Bounded counters for an explicitly numbered collection rewrite."""

    numbered_prefix_count: int
    section_marker_count: int
    unnumbered_line_count: int


def normalize_numbered_collection(payload: bytes) -> tuple[bytes, NormalizationStats]:
    """Strip explicit list ordinals and section markers without interpreting track text."""

    # Apply the server's source size, row-size, row-count and encoding gates before any line can be
    # removed by this opt-in representation rewrite.
    parse_import(ImportEnvelope(ImportFormat.TXT, payload))
    encoding = ""
    text = ""
    for candidate in ("utf-8-sig", "cp1251"):
        try:
            text = payload.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    if not encoding:
        raise ImportEnvelopeError("import.encoding_unsupported")

    normalized: list[str] = []
    numbered_prefix_count = 0
    section_marker_count = 0
    unnumbered_line_count = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _SECTION_MARKER.fullmatch(line):
            section_marker_count += 1
            continue
        match = _NUMBERED_LINE.fullmatch(line)
        if match is None:
            normalized.append(raw_line)
            unnumbered_line_count += 1
            continue
        normalized.append(match.group("track"))
        numbered_prefix_count += 1

    normalized_payload = ("\n".join(normalized) + "\n").encode("utf-8")
    return normalized_payload, NormalizationStats(
        numbered_prefix_count=numbered_prefix_count,
        section_marker_count=section_marker_count,
        unnumbered_line_count=unnumbered_line_count,
    )


def _publish_new_file(destination: Path, payload: bytes) -> None:
    """Atomically publish a new file without replacing an existing destination."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".autplay-track-list-",
        suffix=".part",
        dir=destination.parent,
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
    parser = argparse.ArgumentParser(
        description=(
            "Validate artist<TAB>title[<TAB>album] or artist - title lines using the same "
            "bounded parser as the AutPlay server."
        )
    )
    parser.add_argument("input", type=Path, help="UTF-8 or CP1251 TXT collection.")
    parser.add_argument(
        "--normalized-output",
        type=Path,
        help=(
            "Write a new UTF-8 collection after stripping leading N./N) ordinals and === section "
            "markers. The input is never modified and an existing destination is never replaced."
        ),
    )
    parser.add_argument(
        "--include-artists",
        action="store_true",
        help="Include artist names in the JSON preview instead of count-only routine diagnostics.",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    normalization_stats: NormalizationStats | None = None
    try:
        payload = options.input.read_bytes()
        if options.normalized_output is not None:
            if options.input.resolve() == options.normalized_output.resolve():
                raise OSError("input and normalized output must differ")
            payload, normalization_stats = normalize_numbered_collection(payload)
        parsed = parse_import(ImportEnvelope(ImportFormat.TXT, payload))
        if options.normalized_output is not None:
            _publish_new_file(options.normalized_output, payload)
    except (OSError, ImportEnvelopeError) as error:
        code = str(error) if isinstance(error, ImportEnvelopeError) else "import.file_unavailable"
        print(json.dumps({"error": code}, sort_keys=True), file=sys.stderr)
        return 2
    artists: dict[str, int] = {}
    for row in parsed.rows:
        if row.valid:
            artists[row.artist] = artists.get(row.artist, 0) + 1
    summary: dict[str, object] = {
        "encoding": parsed.encoding,
        "malformed_count": parsed.malformed_count,
        "row_count": len(parsed.rows),
        "valid_count": parsed.valid_count,
        "duplicate_valid_row_count": parsed.valid_count
        - len({(row.artist, row.title, row.album) for row in parsed.rows if row.valid}),
        "artist_count": len(artists),
        "normalization": asdict(normalization_stats) if normalization_stats is not None else None,
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


if __name__ == "__main__":
    raise SystemExit(main())
