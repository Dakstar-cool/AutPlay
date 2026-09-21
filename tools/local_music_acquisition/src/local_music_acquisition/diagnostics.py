"""Comparable, redacted runtime and optional public-page latency probes. No downloads."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

PAGES = {
    "youtube": "https://www.youtube.com/",
    "soundcloud": "https://soundcloud.com/",
    "bandcamp": "https://bandcamp.com/",
}


def runtime_report() -> dict[str, object]:
    versions: dict[str, str] = {"python": platform.python_version()}
    for package in ("playwright", "yt-dlp", "requests"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    for executable, flag in (
        ("node", "--version"),
        ("chromium", "--version"),
        ("ffmpeg", "-version"),
    ):
        path = shutil.which(executable)
        versions[executable] = "unavailable"
        if path is not None:
            try:
                result = subprocess.run(
                    [path, flag], capture_output=True, text=True, timeout=5, check=False
                )
                version = re.search(r"\d+\.\d+(?:\.\d+)*", result.stdout)
                if result.returncode == 0 and version:
                    versions[executable] = version.group()
            except (OSError, subprocess.SubprocessError):
                pass
    report: dict[str, object] = {
        "schema_version": 1,
        "platform": platform.system(),
        "logical_cpus": os.cpu_count(),
        "affinity_cpus": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "versions": versions,
    }
    # Container-root cgroup v2 values only, never arbitrary host paths or full environments.
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().strip().split()
        report["cpu_quota"] = None if quota == "max" else int(quota) / int(period)
        stats = dict(
            line.split() for line in Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines()
        )
        report["cpu_throttling"] = {
            key: int(stats[key])
            for key in ("nr_periods", "nr_throttled", "throttled_usec")
            if key in stats
        }
    except (OSError, ValueError, ZeroDivisionError):
        report["cgroup_status"] = "unavailable"
    return report


def local_proxy(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "socks5h"
            and parsed.hostname == "127.0.0.1"
            and parsed.port is not None
            and not (
                parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
            )
        ):
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("expected a local socks5h proxy without credentials")


def page_probes(proxy_url: str | None = None) -> dict[str, object]:
    results: dict[str, object] = {}
    routes: dict[str, str | None] = {"direct": None}
    if proxy_url is not None:
        routes["proxy"] = local_proxy(proxy_url)
    with requests.Session() as session:
        session.trust_env = False
        for route, proxy in routes.items():
            for site, url in PAGES.items():
                started = time.monotonic()
                try:
                    with session.get(
                        url,
                        proxies={"http": proxy, "https": proxy} if proxy is not None else {},
                        timeout=(5, 5),
                        stream=True,
                        allow_redirects=False,
                    ) as response:
                        results[f"{route}.{site}"] = {
                            "http_status": response.status_code,
                            "headers_seconds": round(time.monotonic() - started, 3),
                        }
                except requests.RequestException:
                    results[f"{route}.{site}"] = {"error": "page_probe_failed"}
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", action="store_true", help="Probe public page headers only.")
    parser.add_argument("--proxy-url", type=local_proxy)
    options = parser.parse_args()
    report = runtime_report()
    if options.network:
        report["pages"] = page_probes(options.proxy_url)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
