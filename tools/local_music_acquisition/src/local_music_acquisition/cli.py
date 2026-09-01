"""Command-line entry point for the portable local acquisition package."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .orchestrator import PlaylistDownloadError, download_playlist
from .providers.hitmo_provider import HitmoProvider
from .providers.jamendo_provider import JamendoProvider
from .providers.yt_dlp import YtDlpProvider


def _bounded_integer(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError("must be an integer") from error
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return parsed

    return parse


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download an authorized TXT playlist sequentially: Jamendo, Hitmo, yt-dlp."
    )
    parser.add_argument("input", type=Path, help="UTF-8 or CP1251 TXT playlist.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--jamendo-client-id-file", type=Path)
    parser.add_argument("--disable-jamendo", action="store_true")
    parser.add_argument("--jamendo-limit", type=_bounded_integer(1, 50), default=20)
    parser.add_argument("--jamendo-timeout", type=_bounded_integer(5, 15), default=15)
    parser.add_argument("--disable-hitmo", action="store_true")
    parser.add_argument("--hitmo-cdp-endpoint", default="http://127.0.0.1:9222")
    parser.add_argument("--hitmo-timeout", type=float, default=120.0)
    parser.add_argument("--hitmo-rights-confirmed", action="store_true")
    parser.add_argument("--disable-yt-dlp", action="store_true")
    parser.add_argument("--yt-dlp-timeout", type=float, default=300.0)
    parser.add_argument("--yt-dlp-rights-confirmed", action="store_true")
    parser.add_argument("--max-mib", type=_bounded_integer(1, 1024), default=200)
    parser.add_argument("--normalize-numbered", action="store_true")
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        options = _parser().parse_args(arguments)
        providers = []
        rights: set[str] = set()
        if not options.disable_jamendo:
            if options.jamendo_client_id_file is None:
                raise PlaylistDownloadError("jamendo_client_id_file_required")
            providers.append(
                JamendoProvider(
                    options.jamendo_client_id_file,
                    limit=options.jamendo_limit,
                    timeout_seconds=options.jamendo_timeout,
                    max_bytes=options.max_mib * 1024 * 1024,
                )
            )
        if not options.disable_hitmo:
            providers.append(
                HitmoProvider(
                    cdp_endpoint=options.hitmo_cdp_endpoint,
                    timeout_seconds=options.hitmo_timeout,
                )
            )
            if options.hitmo_rights_confirmed:
                rights.add("hitmo")
        if not options.disable_yt_dlp:
            providers.append(
                YtDlpProvider(
                    timeout_seconds=options.yt_dlp_timeout,
                    max_bytes=options.max_mib * 1024 * 1024,
                )
            )
            if options.yt_dlp_rights_confirmed:
                rights.add("yt_dlp")
        summary = download_playlist(
            options.input,
            options.output_dir,
            providers=tuple(providers),
            rights_confirmed=frozenset(rights),
            normalize_numbered=options.normalize_numbered,
        )
    except (PlaylistDownloadError, RuntimeError, ValueError) as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["failed"] == 0 else 1
