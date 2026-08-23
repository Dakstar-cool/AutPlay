"""Validate and preview an AutPlay TXT track collection without network access."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from autplay.domain.import_identity import (
    ImportEnvelope,
    ImportEnvelopeError,
    ImportFormat,
    parse_import,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate artist<TAB>title[<TAB>album] or artist - title lines using the same "
            "bounded parser as the AutPlay server."
        )
    )
    parser.add_argument("input", type=Path, help="UTF-8 or CP1251 TXT collection.")
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        payload = options.input.read_bytes()
        parsed = parse_import(ImportEnvelope(ImportFormat.TXT, payload))
    except (OSError, ImportEnvelopeError) as error:
        code = str(error) if isinstance(error, ImportEnvelopeError) else "import.file_unavailable"
        print(json.dumps({"error": code}, sort_keys=True), file=sys.stderr)
        return 2
    artists: dict[str, int] = {}
    for row in parsed.rows:
        if row.valid:
            artists[row.artist] = artists.get(row.artist, 0) + 1
    print(
        json.dumps(
            {
                "encoding": parsed.encoding,
                "malformed_count": parsed.malformed_count,
                "row_count": len(parsed.rows),
                "valid_count": parsed.valid_count,
                "artists": [
                    {"name": name, "track_count": count}
                    for name, count in sorted(
                        artists.items(), key=lambda item: (-item[1], item[0].casefold())
                    )
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
