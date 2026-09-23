"""Acquire an isolated FMA full-length source pool without unsealing it.

Downloads official file URLs from a verified metadata-derived inventory. The
result remains quarantined until per-track license and provenance review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MAX_AUDIO_BYTES = 50_000_000
PREFIX = "https://files.freemusicarchive.org/storage-freemusicarchive-org/music/"


def _sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def fetch_pool(inventory_path: Path, destination: Path) -> dict[str, Any]:
    if inventory_path.is_symlink():
        raise ValueError("linked full-length inventory")
    inventory_bytes = inventory_path.read_bytes()
    inventory = json.loads(inventory_bytes)
    candidates = inventory.get("candidates")
    if (
        inventory.get("schema") != "autplay.face.fma-full-length-pool.v1"
        or inventory.get("status") != "QUARANTINED_FINAL_SOURCE_POOL_LICENSE_AND_DECODER_UNREVIEWED"
        or type(candidates) is not list
        or not 15 <= len(candidates) <= 100
        or len({row["track_id"] for row in candidates}) != len(candidates)
    ):
        raise ValueError("untrusted full-length inventory")
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("linked full-length destination")
    curl_command = shutil.which("curl.exe")
    if curl_command is None:
        raise ValueError("curl.exe is unavailable")
    curl_path = Path(curl_command).resolve(strict=True)
    curl_version = subprocess.run(
        [str(curl_path), "--version"], capture_output=True, text=True, check=True, timeout=15
    ).stdout.splitlines()[0]
    acquired: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for row in candidates:
        track_id = row["track_id"]
        url = row["file_url"]
        if (
            type(track_id) is not int
            or not 1 <= track_id <= 999_999
            or type(url) is not str
            or not url.startswith(PREFIX)
            or urlsplit(url).query
            or urlsplit(url).fragment
            or any(part in ("", ".", "..") for part in url[len(PREFIX) :].split("/"))
        ):
            raise ValueError("invalid full-length source URL")
        target = destination / f"{track_id:06d}.mp3"
        temporary = target.with_suffix(".mp3.part")
        if target.is_symlink() or temporary.is_symlink() or temporary.exists():
            raise ValueError("linked or unfinished full-length candidate")
        if target.exists():
            size, digest = _sha256(target)
            if not 128_000 <= size <= MAX_AUDIO_BYTES:
                raise ValueError("existing full-length candidate outside byte bounds")
            acquired.append(
                {
                    "track_id": track_id,
                    "path": str(target.resolve(strict=True)),
                    "size_bytes": size,
                    "sha256": digest,
                    "source_url": url,
                    "download_status": "EXISTING_REHASHED",
                }
            )
            continue
        try:
            response = subprocess.run(
                [
                    str(curl_path),
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    "120",
                    "--max-filesize",
                    str(MAX_AUDIO_BYTES),
                    "--proto",
                    "=https",
                    "--proto-redir",
                    "=https",
                    "--output",
                    str(temporary),
                    "--write-out",
                    "%{http_code} %{url_effective}",
                    url,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=130,
            )
            parts = response.stdout.split(" ", 1)
            if (
                response.returncode != 0
                or len(parts) != 2
                or parts[0] != "200"
                or urlsplit(parts[1]).hostname != "files.freemusicarchive.org"
            ):
                raise ValueError(
                    f"curl exit {response.returncode}, status {parts[0] if parts else 'unknown'}"
                )
            size, digest = _sha256(temporary)
            if not 128_000 <= size <= MAX_AUDIO_BYTES:
                raise ValueError("incomplete full-length source")
            temporary.replace(target)
            acquired.append(
                {
                    "track_id": track_id,
                    "path": str(target.resolve(strict=True)),
                    "size_bytes": size,
                    "sha256": digest,
                    "source_url": url,
                    "download_status": "FETCHED",
                }
            )
            print(f"Fetched full-length lead {track_id}: {size} bytes", flush=True)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            if temporary.exists():
                temporary.unlink()
            failures.append({"track_id": track_id, "source_url": url, "error": str(error)[:300]})
            print(f"Unavailable full-length lead {track_id}: {type(error).__name__}", flush=True)
    return {
        "schema": "autplay.face.fma-full-length-acquisition.v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "download_client": {
            "path": str(curl_path),
            "sha256": _sha256(curl_path)[1],
            "version": curl_version,
        },
        "status": "QUARANTINED_POOL_NOT_FINAL_SET_OR_LICENSE_APPROVED",
        "acquired": acquired,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    manifest = args.destination / "full-length-acquisition.manifest.json"
    if manifest.exists():
        raise ValueError("refusing to replace full-length acquisition evidence")
    result = fetch_pool(args.inventory, args.destination)
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{manifest}: {len(result['acquired'])} acquired, {len(result['failures'])} unavailable")


if __name__ == "__main__":
    main()
