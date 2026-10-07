"""Source provenance must fail closed before an expensive frozen-model load."""

import hashlib
import json

import pytest
from audio_inputs import clip_plan, load_manifest


def sample(tmp_path):
    sources = {"tracks": [{"track_id": "track-000", "sha256": "a" * 64, "duration_ms": 30000}]}
    source_bytes = json.dumps(sources).encode()
    (tmp_path / "sources.private.json").write_bytes(source_bytes)
    manifest = {
        "schema_version": 1,
        "sample_rate": 16000,
        "encoding": "f32le",
        "track_count": 1,
        "full_source_decode_verified": True,
        "source_hashes_verified_before_and_after": True,
        "source_manifest_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "clips": [
            {
                "track_id": "track-000",
                "clip_index": index,
                "file": f"track-000-clip-{index}.f32le",
                "clip_start_ms": start,
                "samples": 192000,
                "pcm_sha256": "b" * 64,
            }
            for index, start in enumerate([0, 9000, 18000])
        ],
    }
    return manifest, sources


@pytest.mark.parametrize(
    "mutation",
    [
        "valid",
        "source_digest",
        "source_duplicate",
        "unknown_track",
        "duplicate_clip",
        "outside_source",
        "wrong_count",
        "wrong_schema",
        "path_escape",
    ],
)
def test_provenance_validation(tmp_path, mutation):
    manifest, sources = sample(tmp_path)
    if mutation == "source_digest":
        manifest["source_manifest_sha256"] = "f" * 64
    elif mutation == "source_duplicate":
        sources["tracks"].append(dict(sources["tracks"][0], track_id="track-001"))
        raw = json.dumps(sources).encode()
        (tmp_path / "sources.private.json").write_bytes(raw)
        manifest.update(track_count=2, source_manifest_sha256=hashlib.sha256(raw).hexdigest())
    elif mutation == "unknown_track":
        manifest["clips"][0]["track_id"] = "track-999"
    elif mutation == "duplicate_clip":
        manifest["clips"][1]["clip_index"] = 0
    elif mutation == "outside_source":
        manifest["clips"][2]["clip_start_ms"] = 30000
    elif mutation == "wrong_count":
        manifest["track_count"] = 2
    elif mutation == "wrong_schema":
        manifest["schema_version"] = 2
    elif mutation == "path_escape":
        manifest["clips"][0]["file"] = "../outside.f32le"
    (tmp_path / "clips.json").write_text(json.dumps(manifest))
    if mutation == "valid":
        assert load_manifest(tmp_path)[1] == manifest
    else:
        with pytest.raises(ValueError):
            load_manifest(tmp_path)


@pytest.mark.parametrize("source_duration_ms", [24_000, 30_000, 129_600, 3_600_000])
def test_clip_plan_varies_starts_and_lengths_within_source(source_duration_ms):
    plan = clip_plan("a" * 64, source_duration_ms)
    assert plan == clip_plan("a" * 64, source_duration_ms)
    assert [duration for _, duration in plan] == [10_000, 12_000, 14_000]
    starts = [start for start, _ in plan]
    assert starts == sorted(starts)
    assert len(set(starts)) == 3
    assert all(start + duration <= source_duration_ms - 1_000 for start, duration in plan)
    assert plan != clip_plan("b" * 64, source_duration_ms)


@pytest.mark.parametrize("mutation", ["valid", "start", "duration", "samples"])
def test_variable_clip_manifest(tmp_path, mutation):
    manifest, sources = sample(tmp_path)
    manifest["schema_version"] = 2
    plan = clip_plan(sources["tracks"][0]["sha256"], 30_000)
    for clip, (start, duration) in zip(manifest["clips"], plan, strict=True):
        clip["clip_start_ms"] = start
        clip["clip_duration_ms"] = duration
        clip["samples"] = duration * 16
    if mutation == "start":
        manifest["clips"][0]["clip_start_ms"] += 1
    elif mutation == "duration":
        manifest["clips"][1]["clip_duration_ms"] += 1_000
    elif mutation == "samples":
        manifest["clips"][2]["samples"] -= 16_000
    (tmp_path / "clips.json").write_text(json.dumps(manifest))
    if mutation == "valid":
        assert load_manifest(tmp_path)[1] == manifest
    else:
        with pytest.raises(ValueError, match="clip_metadata_invalid"):
            load_manifest(tmp_path)
