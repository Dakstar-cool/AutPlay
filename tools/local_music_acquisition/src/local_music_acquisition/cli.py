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
from .providers.yandex_provider import YandexProvider
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
        description=(
            "Download an authorized TXT playlist through Jamendo, Hitmo, yt-dlp, "
            "and optional Yandex contours."
        )
    )
    parser.add_argument("input", type=Path, help="UTF-8 or CP1251 TXT playlist.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--jamendo-client-id-file", type=Path)
    parser.add_argument("--disable-jamendo", action="store_true")
    parser.add_argument("--jamendo-limit", type=_bounded_integer(1, 50), default=10)
    parser.add_argument("--jamendo-timeout", type=_bounded_integer(5, 15), default=10)
    parser.add_argument("--disable-hitmo", action="store_true")
    parser.add_argument("--hitmo-cdp-endpoint", default="http://127.0.0.1:9222")
    parser.add_argument("--hitmo-timeout", type=float, default=45.0)
    parser.add_argument("--hitmo-rights-confirmed", action="store_true")
    parser.add_argument("--disable-yt-dlp", action="store_true")
    parser.add_argument("--yt-dlp-timeout", type=float, default=90.0)
    parser.add_argument("--yt-dlp-rights-confirmed", action="store_true")
    parser.add_argument(
        "--yandex-token-file",
        type=Path,
        help="Enable the fourth contour using a protected OAuth token file.",
    )
    parser.add_argument("--yandex-quality", choices=("low", "normal", "lossless"), default="normal")
    parser.add_argument("--yandex-timeout", type=_bounded_integer(5, 30), default=10)
    parser.add_argument("--yandex-rights-confirmed", action="store_true")
    parser.add_argument("--max-mib", type=_bounded_integer(1, 1024), default=200)
    parser.add_argument("--normalize-numbered", action="store_true")
    parser.add_argument("--workers", type=_bounded_integer(1, 4), default=1)
    parser.add_argument(
        "--continue-on-provider-failure",
        action="store_true",
        help="Continue to the next contour after a bounded terminal provider failure.",
    )
    parser.add_argument("--provider-failure-threshold", type=_bounded_integer(1, 100), default=2)
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
        if options.yandex_token_file is not None:
            providers.append(
                YandexProvider(
                    options.yandex_token_file,
                    quality=options.yandex_quality,
                    timeout_seconds=options.yandex_timeout,
                    max_bytes=options.max_mib * 1024 * 1024,
                )
            )
            if options.yandex_rights_confirmed:
                rights.add("yandex")
        summary = download_playlist(
            options.input,
            options.output_dir,
            providers=tuple(providers),
            rights_confirmed=frozenset(rights),
            normalize_numbered=options.normalize_numbered,
            max_workers=options.workers,
            continue_on_provider_failure=options.continue_on_provider_failure,
            provider_failure_threshold=options.provider_failure_threshold,
        )
    except (PlaylistDownloadError, RuntimeError, ValueError) as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["failed"] == 0 else 1
