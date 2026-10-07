from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

_PATH = Path(__file__).resolve().parents[2] / "tools/acquisition_vault_bridge.py"
_SPEC = importlib.util.spec_from_file_location("acquisition_bridge_metadata_tests", _PATH)
assert _SPEC and _SPEC.loader
bridge = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bridge
_SPEC.loader.exec_module(bridge)


def evidence(album="Native Album"):
    return {
        "schema_version": 1,
        "provider": "JAMENDO",
        "source_id": "123",
        "fields": {
            "title": "Song (Live)",
            "artist": "Artist",
            "album": album,
            "release_date": "2020",
        },
        "external_ids": {"native_album_id": "44"},
        "artwork": [],
    }


def receipt(tmp_path, metadata=None):
    directory = tmp_path / "tracks/key/jamendo"
    directory.mkdir(parents=True, exist_ok=True)
    audio = directory / "audio.flac"
    if not audio.exists():
        audio.write_bytes(b"immutable fixture audio")
    payload = audio.read_bytes()
    doc = {
        "schema_version": 1,
        "key": "key",
        "provider": "jamendo",
        "filename": "audio.flac",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "duration_seconds": 12,
        "item": {"artist": "Artist", "title": "Song (Live)", "album": None},
    }
    if metadata is not None:
        doc["source_metadata"] = metadata
    path = directory.parent / "receipt.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return bridge.read_receipt(path, tmp_path)


def published(value):
    return {
        "state": "PUBLISHED",
        "signature": list(value.signature),
        "ref_id": str(uuid4()),
        "upload_id": str(uuid4()),
        "variant_id": str(uuid4()),
        "published_at": 1,
    }


def test_evidence_only_change_preserves_exact_audio_import_identity(tmp_path):
    old = receipt(tmp_path)
    native = receipt(tmp_path, evidence())
    changed = receipt(tmp_path, evidence("Different source edition"))
    assert old.identity == native.identity == changed.identity
    assert old.payload == native.payload == changed.payload
    assert old.signature == native.signature == changed.signature
    assert native.source_metadata == evidence()
    assert native.metadata_sha256 != changed.metadata_sha256


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        {"schema_version": True},
        {"schema_version": 1, "provider": "BANDCAMP", "source_id": "123", "fields": {}},
        {**evidence(), "fields": {"title": "x" * 20000}},
    ],
)
def test_bad_optional_metadata_does_not_reject_verified_audio(tmp_path, metadata):
    old = receipt(tmp_path)
    result = receipt(tmp_path, metadata)
    assert result.identity == old.identity
    assert result.source_metadata is None
    bridge.verify_audio(result)


def test_bad_optional_leaves_keep_native_album_and_strip_secret_urls(tmp_path):
    raw = evidence()
    raw["fields"]["release_date"] = "2020-99-99"
    raw["artwork"] = [
        {"kind": "album", "source_id": "44", "url": "https://user:secret@private.test/cover.jpg"}
    ]
    result = receipt(tmp_path, raw)
    assert result.source_metadata["fields"]["album"] == "Native Album"
    assert "release_date" not in result.source_metadata["fields"]
    assert result.source_metadata["artwork"] == []
    assert "secret" not in json.dumps(result.source_metadata)


def test_published_audio_never_retries_upload_when_metadata_service_fails(tmp_path, monkeypatch):
    value = receipt(tmp_path, evidence())
    previous = published(value)
    calls = []

    def fail(value, state):
        calls.append(value.identity)
        raise RuntimeError("provider token secret must not reach checkpoint")

    backend = SimpleNamespace(schedule_metadata=fail)
    monkeypatch.setattr(bridge.time, "time", lambda: 100)
    state = bridge.advance_checkpoint(backend, value, previous)
    assert state["state"] == "PUBLISHED"
    assert state["metadata_state"] == "PENDING"
    assert state["metadata_error"] == "RuntimeError"
    assert state["upload_id"] == previous["upload_id"]
    assert "secret" not in json.dumps(state)
    assert bridge.advance_checkpoint(backend, value, state) == state
    assert len(calls) == 1
    for _ in range(4):
        state["metadata_retry_at"] = 0
        state = bridge.advance_checkpoint(backend, value, state)
    assert state["metadata_state"] == "FAILED"
    assert state["state"] == "PUBLISHED"
    assert bridge.select_work({value.identity: value}, {value.identity: state}, 16) == []


def test_old_checkpoint_and_changed_evidence_backfill_without_audio_work(tmp_path):
    value = receipt(tmp_path)
    previous = published(value)
    calls = []
    backend = SimpleNamespace(schedule_metadata=lambda r, s: calls.append(r.metadata_sha256))
    state = bridge.advance_checkpoint(backend, value, previous)
    assert state["metadata_state"] == "SCHEDULED"
    assert bridge.advance_checkpoint(backend, value, state) == state
    native = receipt(tmp_path, evidence())
    updated = bridge.advance_checkpoint(backend, native, state)
    assert len(calls) == 2
    assert updated["metadata_sha256"] == native.metadata_sha256
    assert updated["variant_id"] == previous["variant_id"]
    assert updated["upload_id"] == previous["upload_id"]


def test_metadata_backfill_cannot_preempt_new_audio_or_block_other_metadata(tmp_path, monkeypatch):
    old = receipt(tmp_path)
    old_state = published(old)
    new = replace(old, identity="new", completed_at_ns=old.completed_at_ns - 10)
    receipts = {old.identity: old, new.identity: new}
    states = {old.identity: old_state}
    assert bridge.select_work(receipts, states, 1) == ["new"]
    monkeypatch.setattr(bridge.time, "time", lambda: 100)
    old_state.update(
        metadata_sha256=old.metadata_sha256, metadata_state="PENDING", metadata_retry_at=101
    )
    assert bridge.select_work(receipts, states, 10) == ["new"]


def test_deterministic_operation_is_replayed_after_lost_ack_and_concurrent_calls(
    tmp_path, monkeypatch
):
    import autplay.application.track_metadata as service_module

    value = receipt(tmp_path, evidence())
    state = published(value)
    operations = []

    class Service:
        def __init__(self, sessions):
            pass

        def accept_acquisition(self, principal, ref_id, *, operation_id, evidence):
            operations.append((operation_id, evidence))
            return {"state": "QUEUED"}

    monkeypatch.setattr(service_module, "TrackMetadataService", Service)
    backend = object.__new__(bridge.Backend)
    backend.owner = uuid4()
    backend.principal = object()
    backend.sessions = object()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: bridge.advance_checkpoint(backend, value, state), range(2)))
    assert len(operations) == 2
    assert operations[0] == operations[1]
    assert operations[0][1] == value.source_metadata
