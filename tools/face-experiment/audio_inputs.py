"""Validate private source-to-clip provenance without importing a model runtime."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

CLIP_DURATIONS_MS = (10_000, 12_000, 14_000)


def clip_plan(source_sha256: str, source_duration_ms: int) -> tuple[tuple[int, int], ...]:
    """Choose reproducible, separated starts and varied lengths for one source."""
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("source_identity_invalid")
    if not 24_000 <= source_duration_ms <= 3_600_000:
        raise ValueError("source_duration_invalid")
    source_digest = bytes.fromhex(source_sha256)
    plan = []
    for index, clip_duration_ms in enumerate(CLIP_DURATIONS_MS):
        # Leave room for small probe/decode duration differences at the source tail.
        last_start_ms = source_duration_ms - clip_duration_ms - 1_000
        lower = last_start_ms * (2 * index) // 5
        upper = last_start_ms * (2 * index + 1) // 5
        offset = int.from_bytes(
            hashlib.sha256(source_digest + bytes([index])).digest()[:8], "big"
        ) % (upper - lower + 1)
        plan.append((lower + offset, clip_duration_ms))
    return tuple(plan)


def load_manifest(root: Path) -> tuple[bytes, dict]:
    """Bind sample counts, source identities, time offsets and exact PCM lengths."""
    raw = (root / "clips.json").read_bytes()
    manifest = json.loads(raw)
    sources_raw = (root / "sources.private.json").read_bytes()
    if hashlib.sha256(sources_raw).hexdigest() != manifest["source_manifest_sha256"]:
        raise ValueError("source_manifest_digest_mismatch")
    if (
        manifest["schema_version"] not in (1, 2)
        or manifest["sample_rate"] != 16000
        or manifest["encoding"] != "f32le"
        or manifest["full_source_decode_verified"] is not True
        or manifest["source_hashes_verified_before_and_after"] is not True
    ):
        raise ValueError("pcm_manifest_invalid")
    sources = json.loads(sources_raw)["tracks"]
    if not 1 <= len(sources) <= 32 or len(sources) != manifest["track_count"]:
        raise ValueError("track_inventory_invalid")
    by_id = {item["track_id"]: item for item in sources}
    if len(by_id) != len(sources) or len({item["sha256"] for item in sources}) != len(sources):
        raise ValueError("duplicate_source_identity")
    for item in sources:
        if (
            not re.fullmatch(r"track-\d{3}", item["track_id"])
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
            or not 24000 <= item["duration_ms"] <= 3600000
        ):
            raise ValueError("source_identity_invalid")
    clips = manifest["clips"]
    if len(clips) != len(sources) * 3 or len({item["file"] for item in clips}) != len(clips):
        raise ValueError("clip_inventory_invalid")
    expected = {(key, index) for key in by_id for index in range(3)}
    for clip in clips:
        identity = (clip["track_id"], clip["clip_index"])
        if identity not in expected:
            raise ValueError("clip_identity_invalid")
        expected.remove(identity)
        duration = by_id[clip["track_id"]]["duration_ms"]
        if manifest["schema_version"] == 1:
            starts = [0, (duration - 12000) // 2, duration - 12000]
            valid_timing = (
                clip["clip_start_ms"] == starts[clip["clip_index"]]
                and 160000 <= clip["samples"] <= 192000
            )
        else:
            start_ms, duration_ms = clip_plan(by_id[clip["track_id"]]["sha256"], duration)[
                clip["clip_index"]
            ]
            valid_timing = (
                clip["clip_start_ms"] == start_ms
                and clip.get("clip_duration_ms") == duration_ms
                and (duration_ms - 500) * 16 <= clip["samples"] <= (duration_ms + 100) * 16
            )
        if (
            not valid_timing
            or clip["file"] != f"{clip['track_id']}-clip-{clip['clip_index']}.f32le"
            or not re.fullmatch(r"[0-9a-f]{64}", clip["pcm_sha256"])
        ):
            raise ValueError("clip_metadata_invalid")
    return raw, manifest
