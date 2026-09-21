"""Bounded Jamendo framing, immutable partial ownership and metadata transport."""

import hashlib
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Never
from uuid import uuid4

import pytest
from autplay.adapters.child_process import discovery_child_launch
from autplay.adapters.filesystem.discovery_child import execute_command
from autplay.adapters.filesystem.discovery_protocol import decode_result, encode_result, metadata
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_child import ChildProtocolError
from autplay.domain.discovery import DiscoveryError
from autplay.domain.vault import Sha256Digest, VerifiedStagedFile
from discovery_download_support import CLIENT_ID, PAYLOAD, local_discovery, provider
from test_jamendo_discovery import _candidate


def command(root: Path, *, maximum: int = 1024**2) -> dict[str, object]:
    return {
        "version": 1,
        "provider": "JAMENDO",
        "candidate_id": "10",
        "provider_artist_id": "20",
        "execution_id": uuid4().hex,
        "root": str(root),
        "max_object_bytes": maximum,
        "max_chunk_bytes": maximum,
        "max_chunks": 100,
        "io_block_bytes": 1024,
    }


def test_owned_download_copy_and_fresh_metadata_exclude_private_url(tmp_path: Path) -> None:
    FilesystemVaultStorage(tmp_path)
    with local_discovery() as remote:
        message = command(tmp_path)
        verified, evidence = execute_command(message, lambda: provider(remote.base))
        payload = encode_result(verified, evidence)
        assert decode_result(payload, maximum=1024**2) == (verified, evidence)
        assert b"download_url" not in payload and CLIENT_ID.encode() not in payload
        assert (
            verified.byte_size == len(PAYLOAD)
            and verified.sha256.value == hashlib.sha256(PAYLOAD).digest()
        )
        assert len(remote.lookups) == 2 and remote.requested.is_set()
        assert (
            tmp_path / "staging" / f"provider-{message['execution_id']}"
        ).read_bytes() == PAYLOAD


@pytest.mark.parametrize(
    "mode",
    ["short", "206", "chunked_short", "json_short", "json_206", "artist_before", "artist_after"],
)
def test_invalid_framing_or_changed_artist_never_returns_success(tmp_path: Path, mode: str) -> None:
    FilesystemVaultStorage(tmp_path)
    with local_discovery(mode=mode) as remote:
        message = command(tmp_path)
        with pytest.raises(DiscoveryError) as caught:
            execute_command(message, lambda: provider(remote.base))
        assert CLIENT_ID not in "".join(traceback.format_exception(caught.value))
        if mode in {"json_short", "json_206", "artist_before"}:
            assert not remote.requested.is_set()
        if mode in {"short", "chunked_short", "artist_after"}:
            sources = tuple(tmp_path.rglob("audio.mp3"))
            assert len(sources) == 1
            retained = sources[0].read_bytes()
            if mode == "chunked_short":
                # IncompleteRead prevents the final partial read from being returned.
                assert 0 < len(retained) < len(PAYLOAD) and PAYLOAD.startswith(retained)
            else:
                assert retained == PAYLOAD
            if mode != "artist_after":
                assert not tuple((tmp_path / "staging").iterdir())


def test_unknown_length_stops_before_any_write_exceeds_limit(tmp_path: Path) -> None:
    FilesystemVaultStorage(tmp_path)
    with (
        local_discovery(mode="unframed") as remote,
        pytest.raises(DiscoveryError, match="discovery_response_too_large"),
    ):
        execute_command(command(tmp_path, maximum=1024), lambda: provider(remote.base))
    files = tuple(tmp_path.rglob("audio.mp3"))
    assert len(files) == 1 and files[0].stat().st_size <= 1024
    assert not tuple((tmp_path / "staging").iterdir())


@pytest.mark.parametrize(
    "mode", ["cl_duplicate", "te_cl", "te_duplicate", "te_unsupported", "cl_negative"]
)
def test_ambiguous_http_framing_is_rejected_before_writing(tmp_path: Path, mode: str) -> None:
    FilesystemVaultStorage(tmp_path)
    with (
        local_discovery(mode=mode) as remote,
        pytest.raises(DiscoveryError, match="discovery_provider_response_invalid"),
    ):
        execute_command(command(tmp_path), lambda: provider(remote.base))
    assert remote.requested.is_set()
    assert not tuple(tmp_path.rglob("audio.mp3"))
    assert not tuple((tmp_path / "staging").iterdir())


@pytest.mark.parametrize("key", ["candidate_id", "provider_artist_id"])
@pytest.mark.parametrize("identifier", [None, "", "\u0661\u0660", "10/20", "1" * 21])
def test_invalid_original_identity_prevents_provider_creation(
    tmp_path: Path, key: str, identifier: object
) -> None:
    def forbidden() -> Never:
        pytest.fail("invalid identity must not initialize a provider")

    message = command(tmp_path)
    message[key] = identifier
    with pytest.raises(ChildProtocolError):
        execute_command(message, forbidden)


@pytest.mark.parametrize("preserve", [False, True])
def test_preexisting_destination_is_never_deleted(tmp_path: Path, preserve: bool) -> None:
    target = tmp_path / "old.mp3"
    target.write_bytes(b"earlier owner")
    with local_discovery() as remote, pytest.raises(DiscoveryError):
        provider(remote.base).acquire(
            _candidate(), target, max_bytes=1024**2, preserve_partial=preserve
        )
    assert target.read_bytes() == b"earlier owner"


def test_unicode_metadata_is_bounded_and_not_truncated() -> None:
    evidence = metadata(
        replace(
            _candidate(),
            title="\U0001f3b5" * 500,
            artist="\U0001f3b5" * 500,
            album="\U0001f3b5" * 500,
        )
    )
    verified = VerifiedStagedFile(1024, Sha256Digest(b"a" * 32))
    encoded = encode_result(verified, evidence)
    assert len(encoded) > 8192
    assert decode_result(encoded, maximum=1024) == (verified, evidence)


def test_launcher_only_passes_explicit_provider_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTPLAY_DATABASE_URL", "must-not-leak")
    monkeypatch.setenv("AUTPLAY_MUSIC_PROXY", "must-not-leak")
    monkeypatch.setenv("AUTPLAY_JAMENDO_CLIENT_ID", "must-not-leak")
    arguments, environment = discovery_child_launch(CLIENT_ID)
    assert arguments[-1] == "autplay.adapters.filesystem.discovery_child"
    assert environment["AUTPLAY_JAMENDO_CLIENT_ID"] == CLIENT_ID
    assert "must-not-leak" not in str(environment) and "PATH" not in environment
