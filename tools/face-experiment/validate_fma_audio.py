"""Verify downloaded FMA development candidates decode as bounded MP3 audio.

This checks bytes and decoder behavior only. It does not establish per-track
license authority, human labels, model quality, or a qualified fixture set.
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tool(name: str) -> tuple[str, str, str]:
    command = shutil.which(name)
    if command is None:
        raise ValueError(f"{name} is unavailable")
    path = Path(command).resolve(strict=True)
    output = subprocess.run(
        [str(path), "-version"], capture_output=True, text=True, check=True, timeout=15
    )
    return str(path), _sha256(path), output.stdout.splitlines()[0]


def validate_audio(acquisition_path: Path) -> dict[str, Any]:
    if acquisition_path.is_symlink() or acquisition_path.name != "range-acquisition.manifest.json":
        raise ValueError("invalid acquisition manifest path")
    acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
    candidates = acquisition.get("candidates")
    if (
        acquisition.get("schema") != "autplay.face.fma-range-acquisition.v1"
        or acquisition.get("status") != "QUARANTINED_AUDIO_NOT_LICENSE_OR_DECODER_APPROVED"
        or type(candidates) is not list
        or len(candidates) != 70
        or len({row.get("track_id") for row in candidates}) != 70
    ):
        raise ValueError("unexpected FMA acquisition evidence")
    ffprobe, probe_hash, probe_version = _tool("ffprobe")
    ffmpeg, decoder_hash, decoder_version = _tool("ffmpeg")
    directory = acquisition_path.parent.resolve(strict=True)
    decoded: list[dict[str, Any]] = []
    for row in candidates:
        track_id = row["track_id"]
        target = directory / f"{track_id:06d}.mp3"
        if target.is_symlink() or target.resolve(strict=True) != Path(row["audio_path"]):
            raise ValueError("candidate path differs from acquisition")
        if target.stat().st_size != row["size_bytes"] or _sha256(target) != row["sha256"]:
            raise ValueError("candidate bytes differ from acquisition")
        probe = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,sample_rate,channels,duration:format=duration",
                "-of",
                "json",
                str(target),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        details = json.loads(probe.stdout)
        streams = details["streams"]
        if len(streams) != 1:
            raise ValueError("candidate stream count differs")
        stream = streams[0]
        duration = float(details["format"]["duration"])
        if (
            stream["codec_name"] != "mp3"
            or not 8_000 <= int(stream["sample_rate"]) <= 192_000
            or int(stream["channels"]) not in (1, 2)
            or not 25.0 <= duration <= 32.0
        ):
            raise ValueError("candidate audio metadata outside development limits")
        # Decode the whole compressed member; -xerror makes corruption fatal.
        subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", str(target), "-f", "null", "-"],
            capture_output=True,
            check=True,
            timeout=60,
        )
        decoded.append(
            {
                "track_id": track_id,
                "sha256": row["sha256"],
                "duration_seconds_probe": duration,
                "codec": stream["codec_name"],
                "sample_rate": int(stream["sample_rate"]),
                "channels": int(stream["channels"]),
                "full_decode": "PASS",
            }
        )
    return {
        "schema": "autplay.face.fma-development-decode.v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "acquisition_sha256": _sha256(acquisition_path),
        "ffprobe": {"path": ffprobe, "sha256": probe_hash, "version": probe_version},
        "ffmpeg": {"path": ffmpeg, "sha256": decoder_hash, "version": decoder_version},
        "status": "DECODE_VERIFIED_LICENSE_AUTHORITY_PENDING",
        "candidates": decoded,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("acquisition", type=Path)
    args = parser.parse_args()
    destination = args.acquisition.parent / "decoded-audio.manifest.json"
    if destination.exists():
        raise ValueError("refusing to replace existing decoder evidence")
    result = validate_audio(args.acquisition)
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{destination}: {len(result['candidates'])} fully decoded candidates")


if __name__ == "__main__":
    main()
