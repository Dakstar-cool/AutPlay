"""Compare FMA's historic CSV license labels with embedded MP3 CC URLs.

An exact match is only preliminary evidence. The artist's current, exact
written grant and operator review are still required to approve a fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from validate_fma_audio import _sha256, _tool

CC_URL = re.compile(
    r"https?://(?:www\.)?creativecommons\.org/(?:licenses|publicdomain)/[^\s<>\"']+", re.I
)


def _license_key(url: str) -> str:
    value = url.rstrip(".,;)]}/ ").lower()
    value = value.removeprefix("https://").removeprefix("http://")
    value = value.removeprefix("www.")
    return value


def audit(inventory_path: Path, acquisition_path: Path) -> dict[str, Any]:
    if inventory_path.is_symlink() or acquisition_path.is_symlink():
        raise ValueError("linked FMA license source")
    inventory_bytes = inventory_path.read_bytes()
    acquisition_bytes = acquisition_path.read_bytes()
    inventory = json.loads(inventory_bytes)
    acquisition = json.loads(acquisition_bytes)
    full_length = inventory.get("schema") == "autplay.face.fma-full-length-pool.v1"
    if full_length:
        expected_acquisition = "autplay.face.fma-full-length-acquisition.v1"
        acquired = acquisition["acquired"]
        audio_key = "path"
    elif inventory.get("schema") == "autplay.face.fma-development-candidates.v1":
        expected_acquisition = "autplay.face.fma-range-acquisition.v1"
        acquired = acquisition["candidates"]
        audio_key = "audio_path"
    else:
        raise ValueError("unknown FMA collection")
    if (
        acquisition.get("schema") != expected_acquisition
        or acquisition.get("inventory_sha256") != hashlib.sha256(inventory_bytes).hexdigest()
        or len(acquired) < 15
    ):
        raise ValueError("unbound FMA acquisition")
    candidates = {row["track_id"]: row for row in inventory["candidates"]}
    ffprobe, probe_hash, probe_version = _tool("ffprobe")
    directory = acquisition_path.parent.resolve(strict=True)
    rows: list[dict[str, Any]] = []
    for item in acquired:
        track_id = item["track_id"]
        source = candidates.get(track_id)
        target = directory / f"{track_id:06d}.mp3"
        if (
            source is None
            or target.is_symlink()
            or target.resolve(strict=True) != Path(item[audio_key])
            or _sha256(target) != item["sha256"]
        ):
            raise ValueError("FMA audio changed before license-tag audit")
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format_tags=copyright,comment",
                "-of",
                "json",
                str(target),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        tags = json.loads(result.stdout).get("format", {}).get("tags", {})
        text = "\n".join(str(value) for value in tags.values())
        embedded = tuple(sorted({_license_key(match.group()) for match in CC_URL.finditer(text)}))
        metadata = _license_key(source["license_url"])
        if not embedded:
            status = "NO_EMBEDDED_CC_URL"
        elif embedded == (metadata,):
            status = "EMBEDDED_MATCHES_HISTORIC_CSV"
        else:
            status = "EMBEDDED_CONFLICTS_WITH_HISTORIC_CSV"
        rows.append(
            {
                "track_id": track_id,
                "audio_sha256": item["sha256"],
                "historic_csv_license": metadata,
                "embedded_cc_urls": embedded,
                "status": status,
            }
        )
    return {
        "schema": "autplay.face.fma-license-tag-audit.v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "collection_role": "FINAL_SOURCE_POOL" if full_length else "DEVELOPMENT_CANDIDATES",
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "acquisition_sha256": hashlib.sha256(acquisition_bytes).hexdigest(),
        "ffprobe": {"path": ffprobe, "sha256": probe_hash, "version": probe_version},
        "status": "PRELIMINARY_TAG_COMPARISON_NOT_A_LICENSE_APPROVAL",
        "counts": dict(Counter(row["status"] for row in rows)),
        "candidates": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("acquisition", type=Path)
    args = parser.parse_args()
    output = args.acquisition.parent / "license-tag-audit.manifest.json"
    if output.exists():
        raise ValueError("refusing to replace license-tag audit")
    result = audit(args.inventory, args.acquisition)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{output}: {result['counts']}")


if __name__ == "__main__":
    main()
