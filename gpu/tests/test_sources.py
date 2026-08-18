"""Verified Vault source resolution rejects path and byte substitution."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from autplay.domain.enrichment import EmbeddingJobTarget

from autplay_gpu.preprocessing import AudioPreprocessingError
from autplay_gpu.sources import PostgresFilesystemAudioSourceResolver


class _Result:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self._row = row

    def one_or_none(self) -> tuple[object, ...] | None:
        return self._row


class _Session:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self._row = row

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def execute(self, statement: object) -> _Result:
        del statement
        return _Result(self._row)


def _target() -> EmbeddingJobTarget:
    return EmbeddingJobTarget(
        uuid4(), "AUDIO_EMBEDDING", uuid4(), uuid4(), uuid4(), b"w" * 32, b"p" * 32
    )


def test_source_resolver_derives_cas_path_and_rehashes_bytes(tmp_path: Path) -> None:
    payload = b"immutable-audio"
    digest = hashlib.sha256(payload).digest()
    root = tmp_path.resolve()
    path = root / "objects" / digest.hex()[:2] / digest.hex()[2:4] / digest.hex()
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    factory = cast(Any, lambda: _Session((12_000, len(payload), digest, digest.hex())))
    resolver = PostgresFilesystemAudioSourceResolver(factory, root)

    source = resolver.resolve(_target())

    assert source.path == path
    assert source.sha256 == digest
    assert source.byte_size == len(payload)


def test_source_resolver_rejects_tamper_and_replica_key_mismatch(tmp_path: Path) -> None:
    payload = b"immutable-audio"
    digest = hashlib.sha256(payload).digest()
    root = tmp_path.resolve()
    path = root / "objects" / digest.hex()[:2] / digest.hex()[2:4] / digest.hex()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"tampered-audio")
    tampered_factory = cast(Any, lambda: _Session((12_000, len(payload), digest, digest.hex())))
    with pytest.raises(AudioPreprocessingError, match="bytes"):
        PostgresFilesystemAudioSourceResolver(tampered_factory, root).resolve(_target())

    mismatch_factory = cast(Any, lambda: _Session((12_000, len(payload), digest, "other")))
    with pytest.raises(AudioPreprocessingError, match="identity"):
        PostgresFilesystemAudioSourceResolver(mismatch_factory, root).resolve(_target())


def test_source_resolver_rejects_symlinked_cas_path(tmp_path: Path) -> None:
    payload = b"immutable-audio"
    digest = hashlib.sha256(payload).digest()
    root = tmp_path / "vault"
    outside = tmp_path / "outside"
    outside.mkdir()
    external_artifact = outside / digest.hex()[2:4] / digest.hex()
    external_artifact.parent.mkdir()
    external_artifact.write_bytes(payload)
    link = root / "objects" / digest.hex()[:2]
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable on this platform")
    factory = cast(Any, lambda: _Session((12_000, len(payload), digest, digest.hex())))

    with pytest.raises(AudioPreprocessingError, match="unavailable"):
        PostgresFilesystemAudioSourceResolver(factory, root.resolve()).resolve(_target())
