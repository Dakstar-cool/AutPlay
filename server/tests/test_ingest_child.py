"""Private ingest protocol and filesystem recovery preserve the only durable bytes."""

import hashlib
import io
import os
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.child_process import ingest_child_launch
from autplay.adapters.filesystem import ingest_child
from autplay.adapters.filesystem.ingest_process import ingest_reply
from autplay.adapters.filesystem.ingest_protocol import IngestChildSettings, analysis_document
from autplay.adapters.filesystem.ingest_storage import ExistingIngestStorage
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_child import (
    ChildProtocolError,
    encode_document,
    read_frame,
    write_frame,
)
from autplay.adapters.media.tools import ChromaprintTool, ValidatedMediaInspector
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    ImmutableObjectConflictError,
    OpaqueStorageKey,
    Sha256Digest,
    StorageOperationError,
    StorageSafetyError,
    VaultLimits,
    VerifiedStagedFile,
)

KEY = OpaqueStorageKey("ingest-stage")
PAYLOAD = b"synthetic uploaded bytes"
METADATA = AudioTechnicalMetadata("flac", "flac", 44100, 2, 12000, None, 16)
EVIDENCE = ChromaprintEvidence("chromaprint", "1.6.1", 12000, b"fingerprint" * 2000)


def setup_storage(root: Path) -> tuple[FilesystemVaultStorage, VerifiedStagedFile]:
    storage = FilesystemVaultStorage(root, limits=VaultLimits())
    storage.create_staging(KEY)
    storage.write_chunk(
        KEY,
        offset=0,
        payload=PAYLOAD,
        payload_sha256=Sha256Digest(hashlib.sha256(PAYLOAD).digest()),
    )
    return storage, storage.verify_staging(KEY)


def command(root: Path, *, expected: VerifiedStagedFile | None = None) -> dict[str, object]:
    return {
        "version": 1,
        "execution_id": str(uuid4()),
        "mode": "WORK" if expected is None else "CLEANUP",
        "key": KEY.value,
        "settings": IngestChildSettings(root).document(),
        "expected": None
        if expected is None
        else {"byte_size": expected.byte_size, "sha256": expected.sha256.hex},
    }


def requests(*actions: str) -> io.BytesIO:
    result = io.BytesIO()
    for action in actions:
        write_frame(result, b"N", encode_document({"action": action}))
    result.seek(0)
    return result


def test_full_work_protocol_retains_staging_and_large_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, verified = setup_storage(tmp_path)
    monkeypatch.setattr(ValidatedMediaInspector, "inspect", lambda self, path: METADATA)
    monkeypatch.setattr(ChromaprintTool, "fingerprint", lambda self, path: EVIDENCE)
    output = io.BytesIO()
    ingest_child.execute(
        command(tmp_path), requests("CAPACITY", "VERIFY", "ANALYZE", "PUBLISH", "FINISH"), output
    )
    output.seek(0)
    replies = [ingest_reply(*read_frame(output)) for _ in range(6)]
    assert replies[2] == {"byte_size": len(PAYLOAD), "sha256": verified.sha256.hex}
    assert replies[3] == analysis_document(METADATA, EVIDENCE)
    assert replies[4] == {"key": verified.sha256.hex, "already_present": False}
    assert replies[5] == {"finished": True}
    assert storage.verify_staging(KEY) == storage.verify_object(
        OpaqueStorageKey(verified.sha256.hex)
    )


@pytest.mark.parametrize("fault", ["version", "mode", "root", "key", "extra", "expected"])
def test_invalid_go_does_not_create_a_namespace(tmp_path: Path, fault: str) -> None:
    root = tmp_path / "not-mounted"
    value = command(root)
    if fault == "version":
        value["version"] = True
    elif fault == "mode":
        value["mode"] = ["WORK"]
    elif fault == "root":
        value["settings"] = IngestChildSettings(tmp_path).document() | {"root": "relative"}
    elif fault == "key":
        value["key"] = "../outside"
    elif fault == "extra":
        value["shell"] = "synthetic"
    else:
        value["expected"] = {"byte_size": 1, "sha256": "0" * 64}
    with pytest.raises((ValueError, StorageSafetyError, RuntimeError)):
        ingest_child.execute(value, requests("FINISH"), io.BytesIO())
    assert not root.exists()


@pytest.mark.parametrize(
    "actions", [("PUBLISH",), ("ANALYZE",), ("VERIFY", "VERIFY"), ("CLEANUP",)]
)
def test_work_rejects_out_of_order_or_cleanup_without_touching_bytes(
    tmp_path: Path, actions: tuple[str, ...]
) -> None:
    storage, verified = setup_storage(tmp_path)
    with pytest.raises(ChildProtocolError):
        ingest_child.execute(command(tmp_path), requests(*actions), io.BytesIO())
    assert storage.verify_staging(KEY) == verified
    assert tuple((tmp_path / "objects").iterdir()) == ()


def test_cleanup_requires_matching_cas_and_replays_missing_leaf(tmp_path: Path) -> None:
    storage, verified = setup_storage(tmp_path)
    existing = ExistingIngestStorage(tmp_path, limits=VaultLimits())
    with pytest.raises(StorageSafetyError):
        existing.cleanup_finalized(KEY, verified)
    assert storage.verify_staging(KEY) == verified
    storage.commit_staging(KEY, verified)
    for _ in range(2):
        output = io.BytesIO()
        value = command(tmp_path, expected=verified)
        ingest_child.execute(value, requests("CLEANUP"), output)
        output.seek(0)
        assert ingest_reply(*read_frame(output))["ready"] is True
        assert ingest_reply(*read_frame(output)) == {
            "execution_id": value["execution_id"],
            "cleaned": True,
        }
    assert not (tmp_path / "staging" / KEY.value).exists()
    assert storage.verify_object(OpaqueStorageKey(verified.sha256.hex)) == verified


def test_cleanup_rejects_replaced_mount_and_changed_staging(tmp_path: Path) -> None:
    storage, verified = setup_storage(tmp_path)
    storage.commit_staging(KEY, verified)
    existing = ExistingIngestStorage(tmp_path, limits=VaultLimits())
    staged = tmp_path / "staging" / KEY.value
    staged.unlink()
    staged.write_bytes(b"replacement")
    with pytest.raises(ImmutableObjectConflictError):
        existing.cleanup_finalized(KEY, verified)
    assert staged.read_bytes() == b"replacement"
    (tmp_path / "staging").rename(tmp_path / "old-staging")
    (tmp_path / "staging").mkdir()
    with pytest.raises(StorageOperationError):
        existing.cleanup_finalized(KEY, verified)


def test_unmounted_root_is_not_created(tmp_path: Path) -> None:
    root = tmp_path / "not-mounted"
    with pytest.raises(StorageOperationError):
        ExistingIngestStorage(root, limits=VaultLimits())
    assert not root.exists()


def test_shard_symlink_is_rejected_before_any_external_write(tmp_path: Path) -> None:
    storage, verified = setup_storage(tmp_path / "vault")
    outside = tmp_path / "outside"
    outside.mkdir()
    first = tmp_path / "vault" / "objects" / verified.sha256.hex[:2]
    try:
        first.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable on this host")
    with pytest.raises(StorageSafetyError):
        storage.commit_staging(KEY, verified)
    assert tuple(outside.iterdir()) == ()
    assert storage.verify_staging(KEY) == verified


def test_publish_syncs_every_shard_on_first_publish_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, verified = setup_storage(tmp_path)
    synced: list[Path] = []
    monkeypatch.setattr(storage, "_fsync_parent", lambda path: synced.append(path.parent))
    expected = tmp_path / "objects" / verified.sha256.hex[:2] / verified.sha256.hex[2:4]
    for _ in range(2):
        synced.clear()
        storage.commit_staging(KEY, verified)
        assert synced == [expected, expected.parent, expected.parent.parent, tmp_path]


def test_launch_keeps_only_media_path_and_os_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AUTPLAY_DATABASE_URL",
        "AUTPLAY_MUSIC_PROXY",
        "AUTPLAY_JAMENDO_CLIENT_ID",
        "HTTP_PROXY",
    ):
        monkeypatch.setenv(name, "synthetic-secret")
    arguments, environment = ingest_child_launch()
    assert arguments[-2:] == ["-m", "autplay.adapters.filesystem.ingest_child"]
    assert "-I" in arguments and environment["PATH"] == os.environ.get("PATH", "")
    assert "synthetic-secret" not in environment.values()
