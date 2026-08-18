"""Deterministic in-image P06 evidence for the pinned CPU media toolchain."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.media.tools import (
    ChromaprintTool,
    FfmpegDecodeValidator,
    FfprobeInspector,
    SubprocessExecutableRunner,
    ValidatedMediaInspector,
)
from autplay.domain.vault import (
    ByteRange,
    MediaValidationError,
    OpaqueStorageKey,
    Sha256Digest,
    VaultLimits,
)


def main() -> int:
    """Generate a valid fixture and reject/quarantine two hostile fixtures."""

    runner = SubprocessExecutableRunner()
    with TemporaryDirectory(prefix="autplay-media-smoke-") as raw_root:
        root = Path(raw_root)
        fixture = root / "fixture.flac"
        generated = runner.run(
            (
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=12",
                "-c:a",
                "flac",
                str(fixture),
            ),
            timeout_seconds=30,
            max_output_bytes=64 * 1024,
        )
        if generated.returncode != 0:
            raise RuntimeError("media fixture generation failed")
        inspector = ValidatedMediaInspector(
            FfmpegDecodeValidator("ffmpeg"), FfprobeInspector("ffprobe")
        )
        fingerprints = ChromaprintTool("fpcalc", algorithm_version="1.6.1")
        storage = FilesystemVaultStorage(
            root / "vault",
            limits=VaultLimits(max_object_bytes=8 * 1024 * 1024, max_chunk_bytes=8 * 1024 * 1024),
        )
        clean = fixture.read_bytes()
        clean_path = _stage(storage, OpaqueStorageKey("clean"), clean)
        metadata = inspector.inspect(clean_path)
        evidence = fingerprints.fingerprint(clean_path)
        clean_verified = storage.verify_staging(OpaqueStorageKey("clean"))
        committed = storage.commit_staging(OpaqueStorageKey("clean"), clean_verified)
        recovered = storage.commit_staging(OpaqueStorageKey("clean"), clean_verified)
        if recovered.storage_key != committed.storage_key or not recovered.already_present:
            raise RuntimeError("idempotent Linux CAS recovery failed")
        verified_at = datetime.now(UTC)
        storage.cleanup_staging(OpaqueStorageKey("clean"))
        reader = storage.open_range(
            committed.storage_key,
            ByteRange(0, min(4095, len(clean) - 1)),
            expected_size=len(clean),
            verified_at=verified_at,
        )
        try:
            streamed_bytes = sum(len(part) for part in reader)
        finally:
            reader.close()
        if streamed_bytes < 1:
            raise RuntimeError("sealed Linux CAS range returned no bytes")
        rejected = 0
        hostile_payloads = (
            clean[:64],
            b'{"streams":[{"codec_type":"audio","codec_name":"flac"}],"format":{}}',
        )
        for index, payload in enumerate(hostile_payloads):
            key = OpaqueStorageKey(f"hostile-{index}")
            path = _stage(storage, key, payload)
            try:
                inspector.inspect(path)
            except MediaValidationError:
                storage.quarantine(key, OpaqueStorageKey(f"rejected-{index}"))
                rejected += 1
            else:
                raise RuntimeError("hostile media fixture was accepted")
        if (
            rejected != len(hostile_payloads)
            or len(storage.inventory().quarantine_keys) != rejected
        ):
            raise RuntimeError("media quarantine evidence mismatch")
        sys.stdout.write(
            json.dumps(
                {
                    "status": "ok",
                    "codec": metadata.codec,
                    "duration_ms": metadata.duration_ms,
                    "fingerprint_bytes": len(evidence.payload),
                    "rejected": rejected,
                    "quarantined": rejected,
                    "streamed_bytes": streamed_bytes,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
    return 0


def _stage(storage: FilesystemVaultStorage, key: OpaqueStorageKey, payload: bytes) -> Path:
    storage.create_staging(key)
    storage.write_chunk(
        key,
        offset=0,
        payload=payload,
        payload_sha256=Sha256Digest(hashlib.sha256(payload).digest()),
    )
    return storage.staging_path_for_media(key)


if __name__ == "__main__":
    raise SystemExit(main())
