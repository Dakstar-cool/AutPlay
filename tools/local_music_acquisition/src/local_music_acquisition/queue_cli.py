"""Local operator controls; no provider/network calls for status, pause, or retry."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .orchestrator import PlaylistDownloadError
from .queue import enqueue, queue_status, retry_unsuccessful
from .queue_store import sync_directory, write_json


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect and control the file-only server queue.")
    parser.add_argument("action", choices=("init", "status", "pause", "resume", "retry"))
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--normalize-numbered", action="store_true")
    parser.add_argument("--normalization-catalog", type=Path)
    parser.add_argument("--include-not-found", action="store_true")
    options = parser.parse_args(arguments)
    try:
        if options.action == "init":
            if options.input is None or options.output_dir is None:
                raise PlaylistDownloadError("queue_input_and_output_required")
            summary = enqueue(
                options.input,
                options.queue_dir,
                options.output_dir,
                normalize=options.normalize_numbered,
                normalization_catalog=options.normalization_catalog
                or (
                    options.output_dir / "normalization-catalog.json"
                    if (options.output_dir / "normalization-catalog.json").is_file()
                    else None
                ),
            )
        else:
            summary = queue_status(options.queue_dir)
            if options.action == "pause":
                write_json(options.queue_dir / "pause", {"paused": True})
            elif options.action == "resume":
                (options.queue_dir / "pause").unlink(missing_ok=True)
                sync_directory(options.queue_dir)
            elif options.action == "retry":
                retry_unsuccessful(options.queue_dir, include_not_found=options.include_not_found)
            summary = queue_status(options.queue_dir)
    except (PlaylistDownloadError, OSError, ValueError) as error:
        code = "queue_filesystem_unavailable" if isinstance(error, OSError) else str(error)
        print(json.dumps({"error": code}), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0
