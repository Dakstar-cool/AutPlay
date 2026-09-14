"""Command-line entry point for the portable local acquisition package."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from pathlib import Path

from .orchestrator import PlaylistDownloadError, download_playlist
from .providers.base import AcquisitionProvider
from .providers.hitmo_provider import HitmoProvider
from .providers.jamendo_provider import JamendoProvider
from .providers.music_sites import BandcampProvider, SoundCloudProvider
from .providers.yandex_provider import YandexProvider
from .providers.yt_dlp import YtDlpProvider
from .source_catalog import SourceCatalog
from .xray import XrayConfig, XrayError, XrayManager


def _bounded_integer(minimum: int, maximum: int) -> Callable[[str], int]:
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
            "and optional SoundCloud, Bandcamp and Yandex contours."
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
    for site in ("soundcloud", "bandcamp"):
        parser.add_argument(f"--enable-{site}", action="store_true")
        parser.add_argument(f"--{site}-timeout", type=float, default=120.0)
        parser.add_argument(f"--{site}-rights-confirmed", action="store_true")
    parser.add_argument("--soundcloud-client-id-file", type=Path)
    for provider in ("yt-dlp", "soundcloud", "bandcamp"):
        parser.add_argument(f"--{provider}-requires-proxy", action="store_true")
    parser.add_argument("--xray-binary", type=Path, default=Path("/usr/local/bin/xray"))
    parser.add_argument("--xray-config", type=Path, default=Path("/usr/local/etc/xray/config.json"))
    parser.add_argument("--xray-proxy-url", default="socks5h://127.0.0.1:10808")
    parser.add_argument("--xray-startup-timeout", type=float, default=15.0)
    parser.add_argument("--xray-idle-timeout", type=float, default=60.0)
    parser.add_argument("--xray-stop-timeout", type=float, default=5.0)
    parser.add_argument(
        "--yandex-token-file",
        type=Path,
        help="Enable the Yandex contour using a protected OAuth token file.",
    )
    parser.add_argument("--yandex-quality", choices=("low", "normal", "lossless"), default="normal")
    parser.add_argument("--yandex-timeout", type=_bounded_integer(5, 30), default=10)
    parser.add_argument("--yandex-rights-confirmed", action="store_true")
    parser.add_argument("--max-mib", type=_bounded_integer(1, 1024), default=200)
    parser.add_argument("--normalize-numbered", action="store_true")
    parser.add_argument(
        "--normalization-catalog",
        type=Path,
        help="Reviewed corrections; defaults to output-dir/normalization-catalog.json.",
    )
    parser.add_argument("--workers", type=_bounded_integer(1, 4), default=1)
    parser.add_argument(
        "--source-catalog",
        type=Path,
        help="Reviewed recording links; defaults to output-dir/source-catalog.json.",
    )
    parser.add_argument(
        "--continue-on-provider-failure",
        action="store_true",
        help="Continue to the next contour after a bounded terminal provider failure.",
    )
    parser.add_argument("--provider-failure-threshold", type=_bounded_integer(1, 100), default=2)
    parser.add_argument("--queue-dir", type=Path, help="Use a durable per-track server queue.")
    parser.add_argument("--queue-max-attempts", type=_bounded_integer(1, 10), default=3)
    parser.add_argument("--queue-retry-seconds", type=_bounded_integer(1, 3600), default=60)
    parser.add_argument(
        "--check-runtime",
        action="store_true",
        help="Check local prerequisites without downloading.",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if arguments and arguments[0] == "soundcloud-client-id":
        from .source_client import main as client_main

        return client_main(arguments[1:])
    if arguments and arguments[0] == "queue":
        from .queue_cli import main as queue_main

        return queue_main(arguments[1:])
    resources = ExitStack()
    cleanup_failed = False
    try:
        options = _parser().parse_args(arguments)
        catalog = options.normalization_catalog
        if catalog is None and (options.output_dir / "normalization-catalog.json").is_file():
            catalog = options.output_dir / "normalization-catalog.json"
        source_path = options.source_catalog
        if source_path is None and (options.output_dir / "source-catalog.json").is_file():
            source_path = options.output_dir / "source-catalog.json"
        sources = SourceCatalog(source_path)
        if options.check_runtime:
            from .preflight import check_runtime

            report = check_runtime(options)
            report["source_catalog"] = sources.summary()
            print(json.dumps(report, sort_keys=True))
            return 0
        providers: list[AcquisitionProvider] = []
        rights: set[str] = set()
        xray = None
        if any(
            getattr(options, f"{name}_requires_proxy")
            for name in ("yt_dlp", "soundcloud", "bandcamp")
        ):
            xray = XrayManager(
                XrayConfig(
                    binary=options.xray_binary,
                    config=options.xray_config,
                    proxy_url=options.xray_proxy_url,
                    startup_timeout=options.xray_startup_timeout,
                    idle_timeout=options.xray_idle_timeout,
                    stop_timeout=options.xray_stop_timeout,
                )
            )
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
                    max_bytes=options.max_mib * 1024 * 1024,
                )
            )
            if options.hitmo_rights_confirmed:
                rights.add("hitmo")
        if not options.disable_yt_dlp:
            providers.append(
                YtDlpProvider(
                    timeout_seconds=options.yt_dlp_timeout,
                    max_bytes=options.max_mib * 1024 * 1024,
                    source_catalog=sources,
                    requires_proxy=options.yt_dlp_requires_proxy,
                    xray=xray,
                )
            )
            if options.yt_dlp_rights_confirmed:
                rights.add("yt_dlp")
        for site, provider_type in (
            ("soundcloud", SoundCloudProvider),
            ("bandcamp", BandcampProvider),
        ):
            if getattr(options, f"enable_{site}"):
                providers.append(
                    provider_type(
                        timeout_seconds=getattr(options, f"{site}_timeout"),
                        max_bytes=options.max_mib * 1024 * 1024,
                        source_catalog=sources,
                        client_id_file=options.soundcloud_client_id_file
                        if site == "soundcloud"
                        else None,
                        requires_proxy=getattr(options, f"{site}_requires_proxy"),
                        xray=xray,
                    )
                )
                if getattr(options, f"{site}_rights_confirmed"):
                    rights.add(site)
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
        stop = threading.Event()
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous = signal.signal(signum, lambda *_args: stop.set())
                resources.callback(signal.signal, signum, previous)
        if xray is not None:
            resources.callback(xray.close)
        if options.queue_dir is not None:
            from .queue import enqueue, run_queue

            enqueue(
                options.input,
                options.queue_dir,
                options.output_dir,
                normalize=options.normalize_numbered,
                normalization_catalog=catalog,
            )
            summary = run_queue(
                options.queue_dir,
                providers=tuple(providers),
                rights_confirmed=frozenset(rights),
                max_workers=options.workers,
                max_attempts=options.queue_max_attempts,
                retry_seconds=options.queue_retry_seconds,
                max_bytes=options.max_mib * 1024 * 1024,
                stop=stop,
            )
        else:
            summary = download_playlist(
                options.input,
                options.output_dir,
                providers=tuple(providers),
                rights_confirmed=frozenset(rights),
                normalize_numbered=options.normalize_numbered,
                normalization_catalog=catalog,
                max_workers=options.workers,
                continue_on_provider_failure=options.continue_on_provider_failure,
                provider_failure_threshold=options.provider_failure_threshold,
                stop=stop,
            )
    except (PlaylistDownloadError, RuntimeError, ValueError, OSError) as error:
        code = "queue_filesystem_unavailable" if isinstance(error, OSError) else str(error)
        print(json.dumps({"error": code}, sort_keys=True), file=sys.stderr)
        return 2
    finally:
        try:
            resources.close()
        except XrayError as error:
            print(json.dumps({"error": str(error)}), file=sys.stderr)
            cleanup_failed = True
    if cleanup_failed:
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if options.queue_dir is not None:
        if summary["state"] != "finished":
            return 75
        return (
            1
            if any(summary[key] for key in ("failed", "not_found", "needs_review", "malformed"))
            else 0
        )
    return 0 if summary["failed"] == 0 else 1
