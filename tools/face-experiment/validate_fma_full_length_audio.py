"""Decode and measure FMA full-length pool audio, without unsealing a final set."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from validate_fma_audio import _sha256, _tool


def validate_full_pool(acquisition_path: Path, inventory_path: Path) -> dict[str, Any]:
    if acquisition_path.is_symlink() or inventory_path.is_symlink():
        raise ValueError("linked FMA full-length evidence")
    acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
    inventory_bytes = inventory_path.read_bytes()
    inventory = json.loads(inventory_bytes)
    if (
        acquisition.get("schema") != "autplay.face.fma-full-length-acquisition.v1"
        or acquisition.get("inventory_sha256") != hashlib.sha256(inventory_bytes).hexdigest()
        or inventory.get("schema") != "autplay.face.fma-full-length-pool.v1"
        or len(acquisition.get("acquired", [])) < 15
    ):
        raise ValueError("invalid full-length acquisition ancestry")
    candidates = {row["track_id"]: row for row in inventory["candidates"]}
    ffprobe, probe_hash, probe_version = _tool("ffprobe")
    ffmpeg, decoder_hash, decoder_version = _tool("ffmpeg")
    directory = acquisition_path.parent.resolve(strict=True)
    verified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in acquisition["acquired"]:
        track_id = row["track_id"]
        source = candidates.get(track_id)
        target = directory / f"{track_id:06d}.mp3"
        if (
            source is None
            or target.is_symlink()
            or target.resolve(strict=True) != Path(row["path"])
            or row["source_url"] != source["file_url"]
            or target.stat().st_size != row["size_bytes"]
            or _sha256(target) != row["sha256"]
        ):
            raise ValueError("full-length source bytes or ancestry changed")
        try:
            probe = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_name,codec_type,sample_rate,channels,width,height:"
                    "stream_disposition=attached_pic:format=duration",
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
            audio = [stream for stream in streams if stream["codec_type"] == "audio"]
            artwork = [stream for stream in streams if stream["codec_type"] == "video"]
            if (
                len(audio) != 1
                or len(artwork) > 1
                or len(streams) != len(audio) + len(artwork)
                or any(
                    picture["disposition"]["attached_pic"] != 1
                    or picture["codec_name"] not in ("mjpeg", "png")
                    or not 1 <= int(picture["width"]) <= 4_096
                    or not 1 <= int(picture["height"]) <= 4_096
                    for picture in artwork
                )
            ):
                raise ValueError("unexpected full-length media streams")
            stream = audio[0]
            duration = float(details["format"]["duration"])
            if (
                stream["codec_name"] != "mp3"
                or not 8_000 <= int(stream["sample_rate"]) <= 192_000
                or int(stream["channels"]) not in (1, 2)
                or not 120.0 <= duration <= 900.0
                or abs(duration - source["metadata_duration_seconds"]) > 15.0
            ):
                raise ValueError("full-length source metadata outside bounds")
            subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(target),
                    "-map",
                    "0:a:0",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                check=True,
                timeout=120,
            )
            verified.append(
                {
                    "track_id": track_id,
                    "sha256": row["sha256"],
                    "duration_seconds_probe": duration,
                    "sample_rate": int(stream["sample_rate"]),
                    "channels": int(stream["channels"]),
                    "attached_cover_art": bool(artwork),
                    "full_audio_decode": "PASS",
                }
            )
            print(f"Decoded full-length lead {track_id}: {duration:.3f} s", flush=True)
        except (ValueError, KeyError, subprocess.SubprocessError) as error:
            rejected.append({"track_id": track_id, "reason": str(error)[:300]})
            print(f"Rejected full-length lead {track_id}: {type(error).__name__}", flush=True)
    return {
        "schema": "autplay.face.fma-full-length-decode.v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "acquisition_sha256": _sha256(acquisition_path),
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "ffprobe": {"path": ffprobe, "sha256": probe_hash, "version": probe_version},
        "ffmpeg": {"path": ffmpeg, "sha256": decoder_hash, "version": decoder_version},
        "status": "DECODE_VERIFIED_FINAL_SOURCE_POOL_LICENSE_AUTHORITY_PENDING",
        "verified": verified,
        "rejected": rejected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("acquisition", type=Path)
    parser.add_argument("inventory", type=Path)
    args = parser.parse_args()
    destination = args.acquisition.parent / "full-length-decode.manifest.json"
    if destination.exists():
        raise ValueError("refusing to replace full-length decoder evidence")
    result = validate_full_pool(args.acquisition, args.inventory)
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{destination}: {len(result['verified'])} decoded, {len(result['rejected'])} rejected")


if __name__ == "__main__":
    main()
