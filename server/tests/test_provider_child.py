"""Bounded private commands and actual provider file verification/copy."""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.child_process import provider_child_launch
from autplay.adapters.filesystem.provider_child import (
    PROVIDER_READ_BYTES,
    ProviderChildError,
    ProviderCommand,
    download_youtube,
    youtube_arguments,
)
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_child import ChildProtocolError, decode_document
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.adapters.windows_process_tree import WindowsJobTree
from autplay.domain.resource_execution import ExecutionState, ExecutionStatus
from autplay.domain.vault import (
    ImmutableObjectConflictError,
    OpaqueStorageKey,
    StorageSafetyError,
    UploadLimitError,
    VaultError,
    VaultLimits,
)
from autplay.runtime.resource_io_deadline import ResourceIoDeadline
from process_tree_support import provider_ticket, wait_tree_exit
from provider_download_support import local_provider


def command(root: Path, execution_id: UUID) -> dict[str, object]:
    return {
        "version": 1,
        "provider": "YOUTUBE",
        "candidate_id": "abcdefghijk",
        "execution_id": execution_id.hex,
        "root": str(root),
        "max_object_bytes": 1024**2,
        "max_chunk_bytes": 32768,
        "max_chunks": 32,
        "io_block_bytes": 8192,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("provider", "URL"),
        ("candidate_id", "../escape"),
        ("execution_id", "invalid"),
        ("root", "relative"),
        ("max_object_bytes", 2**33),
        ("max_chunk_bytes", 0),
        ("io_block_bytes", True),
        ("extra", "ignored"),
    ],
)
def test_invalid_command_is_rejected_before_storage(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    root = tmp_path / "absent"
    document = {**command(root, uuid4()), field: value}
    with pytest.raises(ValueError):
        ProviderCommand.parse(document)
    assert not root.exists()


def test_copy_hashes_source_and_independently_verifies_staging(tmp_path: Path) -> None:
    vault = FilesystemVaultStorage(tmp_path)
    storage = FilesystemProviderStorage(tmp_path)
    execution = uuid4()
    source = storage.create_workspace(execution) / "audio.test"
    payload = b"source bytes" * 25
    source.write_bytes(payload)
    result = storage.copy_verified_source(execution, source, limits=VaultLimits(max_chunk_bytes=17))
    assert result.byte_size == len(payload)
    assert result.sha256.hex == hashlib.sha256(payload).hexdigest()
    assert (
        vault.staging_path_for_media(OpaqueStorageKey(f"provider-{execution.hex}")).read_bytes()
        == payload
    )
    with pytest.raises(ImmutableObjectConflictError):
        storage.copy_verified_source(execution, source, limits=VaultLimits())
    assert source.read_bytes() == payload


@pytest.mark.parametrize("fault", ["outside", "empty", "oversize", "hardlink", "symlink"])
def test_copy_rejects_wrong_source_without_creating_staging(tmp_path: Path, fault: str) -> None:
    FilesystemVaultStorage(tmp_path)
    storage = FilesystemProviderStorage(tmp_path)
    execution = uuid4()
    workspace = storage.create_workspace(execution)
    source = workspace / "audio.test"
    source.write_bytes(b"original")
    expected: type[VaultError] = StorageSafetyError
    if fault == "outside":
        source = tmp_path / "outside"
        source.write_bytes(b"outside")
    elif fault == "empty":
        source.write_bytes(b"")
    elif fault == "oversize":
        source.write_bytes(b"x" * 65)
        expected = UploadLimitError
    elif fault == "hardlink":
        os.link(source, workspace / "alias")
    else:
        link = workspace / "link"
        try:
            link.symlink_to(source)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                pytest.skip("Windows symlink privilege unavailable; Linux proof required")
            raise
        source = link
    with pytest.raises(expected):
        storage.copy_verified_source(
            execution,
            source,
            limits=VaultLimits(max_object_bytes=64, max_chunk_bytes=16),
        )
    assert not (tmp_path / "staging" / f"provider-{execution.hex}").exists()


def test_changed_source_is_rejected_and_partial_copy_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FilesystemVaultStorage(tmp_path)
    storage = FilesystemProviderStorage(tmp_path)
    execution = uuid4()
    source = storage.create_workspace(execution) / "audio.test"
    source.write_bytes(b"original")
    original = FilesystemVaultStorage.write_chunk
    changed = False

    def changing(instance: FilesystemVaultStorage, *args: Any, **kwargs: Any) -> Any:
        nonlocal changed
        result = original(instance, *args, **kwargs)
        if not changed:
            with source.open("ab") as output:
                output.write(b"changed")
            changed = True
        return result

    monkeypatch.setattr(FilesystemVaultStorage, "write_chunk", changing)
    with pytest.raises(StorageSafetyError):
        storage.copy_verified_source(execution, source, limits=VaultLimits(max_chunk_bytes=4))
    assert (tmp_path / "staging" / f"provider-{execution.hex}").read_bytes() == b"originalchanged"


def test_fixed_youtube_command_and_environment_do_not_inherit_application_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTPLAY_DATABASE_URL", "synthetic-private-database")
    monkeypatch.setenv("PYTHONPATH", "synthetic-untrusted-modules")
    monkeypatch.setenv("AUTPLAY_MUSIC_PROXY", "http://synthetic-proxy.invalid:8080")
    monkeypatch.setenv("AUTPLAY_MUSIC_PO_TOKEN_URL", "http://music-po-token:4416")
    arguments, environment = provider_child_launch()
    assert arguments[-1] == "autplay.adapters.filesystem.provider_child"
    assert "AUTPLAY_DATABASE_URL" not in environment and "PYTHONPATH" not in environment
    assert environment["AUTPLAY_MUSIC_PROXY"] == "http://synthetic-proxy.invalid:8080"
    assert environment["AUTPLAY_MUSIC_PO_TOKEN_URL"] == "http://music-po-token:4416"
    arguments = youtube_arguments("abcdefghijk")
    assert arguments[-2:] == ["autplay.adapters.filesystem.provider_media", "abcdefghijk"]
    with pytest.raises(ChildProtocolError):
        youtube_arguments("https://example.invalid")


def test_downloader_output_is_read_with_a_hard_memory_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.BytesIO(b"x" * (PROVIDER_READ_BYTES * 3))
    reads: list[int] = []

    class Output(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            assert size is not None
            reads.append(size)
            return output.read(size)

    class Process:
        stdout = Output()
        killed = False

        def poll(self) -> int | None:
            return None

        def kill(self) -> None:
            self.killed = True

    process = Process()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    maximum = PROVIDER_READ_BYTES + 1024
    with pytest.raises(ProviderChildError, match="provider_download_too_large"):
        download_youtube("abcdefghijk", tmp_path, maximum)
    assert reads == [PROVIDER_READ_BYTES, 1025]
    assert (tmp_path / "audio.media").stat().st_size == PROVIDER_READ_BYTES
    assert process.killed and process.stdout.closed


@pytest.mark.skipif(os.name != "nt", reason="actual Windows provider tree integration")
def test_real_child_downloads_copies_and_verifies_only_after_go(tmp_path: Path) -> None:
    FilesystemVaultStorage(tmp_path)
    payload = b"loopback provider payload" * 4096
    with local_provider(payload) as provider:
        child = RetainedVaultProcess(
            provider_ticket(),
            ResourceIoDeadline(monotonic()),
            tree_factory=WindowsJobTree,
            launch=provider.launch,
        )
        identity = None
        try:
            identity = child.spawn()
            assert not provider.requested.is_set()
            assert not (tmp_path / "provider-work").exists()
            child.allow_go(
                ExecutionStatus(child.ticket, ExecutionState.RUNNING, None, identity, None)
            )
            child.go(command(tmp_path, child.ticket.execution_id))
            tag, data = child.read_result()
            assert tag == b"R" and decode_document(data) == {
                "byte_size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            assert provider.requested.is_set()
            assert wait_tree_exit(child, identity).exit_code == 0
            staged = tmp_path / "staging" / f"provider-{child.ticket.execution_id.hex}"
            assert staged.read_bytes() == payload
        finally:
            child.request_stop()
            wait_tree_exit(child, identity)
            child.close_pipes_after_worker_exit()
            child.close_tree_after_acknowledgement()
