"""Fetch Commons music leads by pinned MediaWiki SHA-1 into quarantine.

No downloaded audio is approved for a Face fixture by this transfer alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_BYTES = 50_000_000
ALLOWED_SUFFIXES = frozenset({".ogg", ".oga", ".opus", ".mp3", ".flac", ".wav"})


class CommonsRateLimitError(RuntimeError):
    """The upstream requested that acquisition stop for now."""


def _hashes(path: Path) -> tuple[int, str, str]:
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
    return size, sha1.hexdigest(), sha256.hexdigest()


def fetch(inventory_path: Path, destination: Path) -> dict[str, Any]:
    if inventory_path.is_symlink():
        raise ValueError("linked Commons inventory")
    inventory_bytes = inventory_path.read_bytes()
    inventory = json.loads(inventory_bytes)
    candidates = inventory.get("candidates")
    if (
        inventory.get("schema") != "autplay.face.commons-music-discovery.v1"
        or inventory.get("status") != "API_METADATA_LEADS_NOT_LICENSE_REVIEWED_OR_AUDIO_VERIFIED"
        or type(candidates) is not list
        or not 15 <= len(candidates) <= 100
        or len({row["page_id"] for row in candidates}) != len(candidates)
    ):
        raise ValueError("invalid Commons inventory")
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("linked Commons destination")
    curl_command = shutil.which("curl.exe")
    if curl_command is None:
        raise ValueError("curl.exe unavailable")
    curl_path = Path(curl_command).resolve(strict=True)
    acquired: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    upstream_halt = False
    for row in candidates:
        page_id = row["page_id"]
        parsed = urllib.parse.urlsplit(row["file_url"])
        suffix = Path(urllib.parse.unquote(parsed.path)).suffix.lower()
        if (
            type(page_id) is not int
            or page_id <= 0
            or parsed.scheme != "https"
            or parsed.hostname != "upload.wikimedia.org"
            or suffix not in ALLOWED_SUFFIXES
            or type(row["size_bytes"]) is not int
            or not 128_000 <= row["size_bytes"] <= MAX_BYTES
            or not str(row["description_url"]).startswith(
                "https://commons.wikimedia.org/wiki/File:"
            )
        ):
            raise ValueError("invalid Commons media authority")
        target = destination / f"{page_id}{suffix}"
        temporary = target.with_suffix(target.suffix + ".part")
        if target.is_symlink() or temporary.is_symlink() or temporary.exists():
            raise ValueError("linked or unfinished Commons media")
        try:
            if not target.exists():
                response = subprocess.run(
                    [
                        str(curl_path),
                        "--fail",
                        "--location",
                        "--silent",
                        "--show-error",
                        "--retry",
                        "3",
                        "--retry-max-time",
                        "180",
                        "--max-time",
                        "120",
                        "--max-filesize",
                        str(MAX_BYTES),
                        "--proto",
                        "=https",
                        "--proto-redir",
                        "=https",
                        "--output",
                        str(temporary),
                        row["file_url"],
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=330,
                )
                if response.returncode != 0 and "429" in response.stderr:
                    raise CommonsRateLimitError(
                        "Commons media rate limit remained after Retry-After"
                    )
                if response.returncode != 0 or not temporary.exists():
                    raise ValueError(f"curl exit {response.returncode}: {response.stderr[:120]}")
                size, source_sha1, sha256 = _hashes(temporary)
                if size != row["size_bytes"] or source_sha1 != row["upstream_sha1"]:
                    raise CommonsRateLimitError("Commons response differs from pinned API bytes")
                temporary.replace(target)
                method = "FETCHED_AND_UPSTREAM_SHA1_MATCHED"
                time.sleep(10)
            else:
                size, source_sha1, sha256 = _hashes(target)
                if size != row["size_bytes"] or source_sha1 != row["upstream_sha1"]:
                    raise ValueError("existing Commons media differs from pinned API bytes")
                method = "EXISTING_UPSTREAM_SHA1_MATCHED"
            acquired.append(
                {
                    "page_id": page_id,
                    "path": str(target.resolve(strict=True)),
                    "size_bytes": size,
                    "sha1": source_sha1,
                    "sha256": sha256,
                    "description_url": row["description_url"],
                    "license_code_api": row["license_code"],
                    "transfer_status": method,
                }
            )
            print(f"Verified Commons lead {page_id}: {size} bytes", flush=True)
        except (OSError, ValueError, subprocess.SubprocessError, CommonsRateLimitError) as error:
            if temporary.exists():
                temporary.unlink()
            failures.append({"page_id": page_id, "reason": str(error)[:300]})
            print(f"Unavailable Commons lead {page_id}: {type(error).__name__}", flush=True)
            if isinstance(error, CommonsRateLimitError):
                upstream_halt = True
                break
    return {
        "schema": "autplay.face.commons-music-acquisition.v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "curl_path": str(curl_path),
        "curl_sha256": _hashes(curl_path)[2],
        "status": "QUARANTINED_AUDIO_NOT_LICENSE_OR_DECODER_APPROVED",
        "upstream_halt": upstream_halt,
        "acquired": acquired,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    output = args.destination / "commons-acquisition.manifest.json"
    if output.exists():
        raise ValueError("refusing to replace Commons acquisition evidence")
    result = fetch(args.inventory, args.destination)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{output}: {len(result['acquired'])} acquired, {len(result['failures'])} unavailable")


if __name__ == "__main__":
    main()
