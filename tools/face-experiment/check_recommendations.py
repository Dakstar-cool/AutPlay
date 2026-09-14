"""Exercise the real CPU pipeline on private sample metadata without DB writes or likes."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from audio_inputs import load_manifest
from autplay.application.recommendations import (
    RecommendationPipelineRunner,
    baseline_pipeline_definition,
)
from autplay.domain.recommendations import (
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationSnapshotRef,
    RecommendationSurface,
    SnapshotTrack,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw, manifest = load_manifest(args.clips)
    sources = json.loads((args.clips / "sources.private.json").read_bytes())["tracks"]
    tracks = tuple(
        SnapshotTrack(
            recording_id=uuid5(NAMESPACE_URL, "autplay/private-test/" + item["sha256"]),
            user_track_ref_id=None,
            artist_key=item["artist_group"],
            release_key=None,
            metadata_tokens=(),
            availability="LOCAL",
            authorized=True,
            identity_status="ACTIVE",
            preference="NEUTRAL",
            excluded=False,
            play_count=0,
            organic_play_count=0,
            recommended_play_count=0,
            last_played_at_ms=None,
            added_at_ms=0,
            release_date_ordinal=None,
        )
        for item in sources
    )
    digest = hashlib.sha256(raw).hexdigest()
    snapshot = RecommendationInputSnapshot(
        RecommendationSnapshotRef(uuid5(NAMESPACE_URL, digest), digest, 0, 0, digest, digest),
        tracks,
        datetime(2099, 1, 1, tzinfo=UTC),
    )
    runner, pipeline = RecommendationPipelineRunner(), baseline_pipeline_definition()
    checks = 0
    result_sizes = []
    for context in ["GENERAL", "WORKOUT", "CYCLING", "WORK", "SLEEP", "PARTY"]:
        for seed in [0, 42, 20260911]:
            for limit in [1, 10, 24]:
                query = RecommendationQuery(
                    uuid5(NAMESPACE_URL, "autplay/private-test/user"),
                    RecommendationSurface.RECOMMENDATIONS,
                    context=context,
                    seed=seed,
                    limit=limit,
                )
                result = runner.run(query, snapshot, pipeline)
                assert result == runner.run(query, snapshot, pipeline), "replay_not_deterministic"
                ids = [item.recording_id for item in result]
                assert 1 <= len(ids) <= limit and len(ids) == len(set(ids)), "rank_bounds_invalid"
                assert set(ids) <= {item.recording_id for item in tracks}, "unknown_recording"
                assert max(Counter(item.artist_key for item in result).values()) <= 2, "artist_cap"
                try:
                    replace(snapshot, tracks=(*tracks, tracks[0]))
                except ValueError:
                    pass
                else:
                    raise AssertionError("duplicate_snapshot_accepted")
                result_sizes.append(len(result))
                checks += 1
    # Policy perturbations are synthetic checks on sample records, not observed user preferences.
    variants = [
        replace(tracks[0], authorized=False),
        replace(tracks[1], excluded=True),
        replace(tracks[2], preference="DISLIKED"),
        replace(tracks[3], identity_status="MERGED"),
        replace(tracks[4], availability="PENDING"),
    ]
    filtered = runner.run(query, replace(snapshot, tracks=tuple(variants) + tracks[5:]), pipeline)
    assert not {x.recording_id for x in variants} & {x.recording_id for x in filtered}, (
        "policy_filter"
    )
    assert filtered, "allowed_sample_disappeared"
    report = {
        "status": "CPU_PIPELINE_REAL_SAMPLE_CHECKS_PASS",
        "tracks": len(tracks),
        "input_manifest_sha256": digest,
        "source_manifest_sha256": manifest["source_manifest_sha256"],
        "deterministic_scenarios": checks,
        "synthetic_policy_exclusions": len(variants),
        "duplicate_snapshots_rejected": checks,
        "result_count_range": [min(result_sizes), max(result_sizes)],
        "components": "actual RecommendationPipelineRunner cpu-baseline v1; in-memory snapshot",
        "sample_preferences": "NEUTRAL; no playlist-to-Like inference; no playback history",
        "recording_ids": "experiment-only UUIDs; no identity merges or registry writes",
        "quality_measured": False,
        "sona_trained_or_evaluated": False,
        "production_database_modified": False,
        "models_activated": False,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
