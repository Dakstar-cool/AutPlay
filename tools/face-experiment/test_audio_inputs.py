"""Source provenance must fail closed before an expensive frozen-model load."""

import hashlib
import json

import pytest
from audio_inputs import load_manifest


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
