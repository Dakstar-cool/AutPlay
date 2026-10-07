"""The private IPC accepts optional bounded native metadata independently of bytes."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.provider_media import (
    PROVIDER_RESULT_BYTES,
    youtube_source_metadata,
)
from autplay.adapters.filesystem.vault_child import encode_document
from autplay.adapters.windows_process_tree import WindowsJobTree
from autplay.domain.vault import VaultLimits
from autplay.runtime.provider_io import ProviderIoExecutor
from autplay.runtime.vault_io import VaultIoCoordinator, VaultIoSession


@pytest.mark.parametrize("fault", ["none", "unicode", "missing", "invalid", "wrong_id", "oversize"])
def test_private_native_result_preserves_verified_audio(tmp_path: Path, fault: str) -> None:
    native = youtube_source_metadata(
        {"id": "abcdefghijk", "track": "Song", "artist": "Artist"}, "abcdefghijk"
    )
    assert native is not None
    document: dict[str, object] = {"byte_size": 12, "sha256": "a" * 64}
    if fault == "invalid":
        native = {"invalid": True}
    elif fault == "wrong_id":
        native["source_id"] = "different00"
    elif fault == "oversize":
        native["ignored"] = "x" * 16385
    elif fault == "unicode":
        native["fields"] = {
            key: "\u0416" * 500 for key in ("title", "artist", "album", "album_artist")
        }
    if fault != "missing":
        document["source_metadata"] = native
    payload = encode_document(document, maximum=PROVIDER_RESULT_BYTES)
    if fault == "unicode":
        assert len(payload) > 8192  # The old command bound cannot contain the result.
    sent: list[dict[str, object]] = []
    child = SimpleNamespace(
        ticket=SimpleNamespace(execution_id=uuid4()),
        go=sent.append,
        read_result=lambda: (b"R", payload),
    )
    executor = ProviderIoExecutor(
        cast(VaultIoCoordinator, object()),
        root=tmp_path,
        limits=VaultLimits(),
        tree_factory=WindowsJobTree,
    )
    result = executor._download(cast(VaultIoSession, SimpleNamespace(child=child)), "abcdefghijk")
    assert result.verified.byte_size == 12 and result.verified.sha256.hex == "a" * 64
    assert result.source_metadata == (native if fault in {"none", "unicode"} else None)
    assert sent[0]["candidate_id"] == "abcdefghijk"
