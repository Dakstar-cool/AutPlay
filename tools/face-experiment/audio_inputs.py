"""Validate private source-to-clip provenance without importing a model runtime."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def load_manifest(root: Path) -> tuple[bytes, dict]:
    """Bind sample counts, source identities, time offsets and exact PCM lengths."""
    raw = (root / "clips.json").read_bytes()
    manifest = json.loads(raw)
    sources_raw = (root / "sources.private.json").read_bytes()
    if hashlib.sha256(sources_raw).hexdigest() != manifest["source_manifest_sha256"]:
        raise ValueError("source_manifest_digest_mismatch")
    if (
        manifest["schema_version"] != 1
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
        starts = [0, (duration - 12000) // 2, duration - 12000]
        if (
            clip["clip_start_ms"] != starts[clip["clip_index"]]
            or clip["file"] != f"{clip['track_id']}-clip-{clip['clip_index']}.f32le"
            or not 160000 <= clip["samples"] <= 192000
            or not re.fullmatch(r"[0-9a-f]{64}", clip["pcm_sha256"])
        ):
            raise ValueError("clip_metadata_invalid")
    return raw, manifest
