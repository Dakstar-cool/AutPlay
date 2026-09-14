"""Prepare a private, hash-bound real-music sample using an offline FFmpeg runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run(command: list[str], timeout: int = 120) -> bytes:
    result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise ValueError("audio_command_failed")
    if len(result.stderr) > 16384 or len(result.stdout) > 1_000_000:
        raise ValueError("audio_command_output_bound")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--music", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    candidates, rejected = [], []
    inventory = json.loads(args.inventory.read_bytes())
    if not 24 <= len(inventory) <= 5000:
        raise ValueError("inventory_count_invalid")
    seen_hashes = set()
    for item in inventory:
        if item["sha256"] in seen_hashes:
            continue
        seen_hashes.add(item["sha256"])
        path = args.music / item["file_name"]
        if path.name != item["file_name"] or path.is_symlink():
            raise ValueError("source_path_invalid")
        try:
            probe = json.loads(
                run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "json",
                        str(path),
                    ],
                    15,
                )
            )
            duration = float(probe["format"]["duration"])
            if not math.isfinite(duration) or not 24 <= duration <= 3600:
                raise ValueError("duration_out_of_bounds")
            if not 1 <= path.stat().st_size <= 200 * 1024 * 1024:
                raise ValueError("source_size_out_of_bounds")
            artist = item["row"].split("\t")[0].split(" - ")[0].strip().casefold()
            candidates.append(
                dict(
                    item,
                    duration_ms=round(duration * 1000),
                    artist_group=hashlib.sha256(artist.encode()).hexdigest(),
                )
            )
        except ValueError, KeyError, subprocess.TimeoutExpired:
            rejected.append({"playlist_index": item["playlist_index"], "error": "probe_rejected"})
    # Eight tracks per duration tercile, preferring distinct artists within the whole sample.
    candidates.sort(key=lambda item: (item["duration_ms"], item["sha256"]))
    selected, artists = [], set()
    for index in range(3):
        bucket = candidates[index * len(candidates) // 3 : (index + 1) * len(candidates) // 3]
        for _ in range(8):
            available = [item for item in bucket if item not in selected]
            diverse = [item for item in available if item["artist_group"] not in artists]
            if not available:
                raise ValueError("insufficient_sample_candidates")
            chosen = min(diverse or available, key=lambda item: item["sha256"])
            selected.append(chosen)
            artists.add(chosen["artist_group"])
    clips, private_tracks = [], []
    for index, item in enumerate(selected):
        path = args.music / item["file_name"]
        if path.stat().st_size != item["size_bytes"] or sha256(path) != item["sha256"]:
            raise ValueError("source_hash_mismatch")
        # Decode the whole source before extracting any model input; never infer on a .part file.
        run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            120,
        )
        track_id = f"track-{index:03d}"
        starts = sorted({0, (item["duration_ms"] - 12000) // 2, item["duration_ms"] - 12000})
        for segment_index, start in enumerate(starts):
            pcm = run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-ss",
                    f"{start / 1000:.3f}",
                    "-t",
                    "12",
                    "-i",
                    str(path),
                    "-map",
                    "0:a:0",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-acodec",
                    "pcm_f32le",
                    "-f",
                    "f32le",
                    "pipe:1",
                ]
            )
            if len(pcm) % 4 or not 16000 * 10 * 4 <= len(pcm) <= 16000 * 12 * 4:
                raise ValueError("pcm_length_invalid")
            name = f"{track_id}-clip-{segment_index}.f32le"
            (args.output / name).write_bytes(pcm)
            clips.append(
                {
                    "track_id": track_id,
                    "clip_index": segment_index,
                    "file": name,
                    "clip_start_ms": start,
                    "samples": len(pcm) // 4,
                    "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                }
            )
        if sha256(path) != item["sha256"]:
            raise ValueError("source_changed_during_decode")
        private_tracks.append(dict(item, track_id=track_id))
    private = {"tracks": private_tracks, "probe_rejected": rejected}
    private_bytes = (json.dumps(private, ensure_ascii=False, indent=2) + "\n").encode()
    (args.output / "sources.private.json").write_bytes(private_bytes)
    manifest = {
        "schema_version": 1,
        "sample_rate": 16000,
        "encoding": "f32le",
        "source_manifest_sha256": hashlib.sha256(private_bytes).hexdigest(),
        "selection": "8 per duration tercile; distinct artists preferred; SHA256 tiebreak",
        "available_tracks": len(inventory),
        "available_unique_sources": len(seen_hashes),
        "probe_rejected_count": len(rejected),
        "track_count": len(selected),
        "artist_groups": len(artists),
        "duration_ms_range": [
            min(x["duration_ms"] for x in selected),
            max(x["duration_ms"] for x in selected),
        ],
        "full_source_decode_verified": True,
        "source_hashes_verified_before_and_after": True,
        "ffmpeg_version": run(["ffmpeg", "-version"]).decode().splitlines()[0],
        "clips": clips,
    }
    (args.output / "clips.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"status": "AUDIO_SAMPLE_PASS", "tracks": len(selected), "clips": len(clips)}))


if __name__ == "__main__":
    main()
