"""One contained A1 download, copy, hash and fresh metadata check after durable GO."""

import os
import re
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import BinaryIO, cast
from uuid import uuid4

from autplay.adapters.jamendo import JamendoProvider
from autplay.domain.discovery import DiscoveryError, DiscoveryEvidence
from autplay.domain.vault import VaultError, VerifiedStagedFile

from .discovery_protocol import DISCOVERY_CHILD_ERRORS, encode_result, metadata
from .provider_child import PROVIDER_COMMAND_SECONDS, ProviderCommand
from .provider_staging import FilesystemProviderStorage
from .vault_child import (
    MAX_COMMAND_BYTES,
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)


def execute_command(
    document: dict[str, object], provider_factory: Callable[[], JamendoProvider]
) -> tuple[VerifiedStagedFile, DiscoveryEvidence]:
    artist = document.get("provider_artist_id")
    if not isinstance(artist, str) or re.fullmatch(r"[0-9]{1,20}", artist) is None:
        raise ChildProtocolError()
    command = ProviderCommand.parse(
        {key: value for key, value in document.items() if key != "provider_artist_id"},
        provider="JAMENDO",
    )
    if not 1024 <= command.limits.max_object_bytes <= 1024**3:
        raise ChildProtocolError()
    provider = provider_factory()
    first = provider.lookup(command.candidate_id)
    if not first.acquisition_allowed or (first.provider_track_id, first.provider_artist_id) != (
        command.candidate_id,
        artist,
    ):
        raise DiscoveryError("discovery_not_eligible")
    storage = FilesystemProviderStorage(command.root)
    workspace = storage.create_workspace(command.execution_id)
    source = workspace / "audio.mp3"
    count = provider.acquire(
        first, source, max_bytes=command.limits.max_object_bytes, preserve_partial=True
    )
    verified = storage.copy_verified_source(command.execution_id, source, limits=command.limits)
    if verified.byte_size != count:
        raise DiscoveryError("discovery_content_invalid")
    current = provider.lookup(command.candidate_id)
    if not current.acquisition_allowed or (
        current.provider_track_id,
        current.provider_artist_id,
    ) != (command.candidate_id, artist):
        raise DiscoveryError("discovery_not_eligible")
    return verified, metadata(current)


def _provider() -> JamendoProvider:
    return JamendoProvider(os.environ.get("AUTPLAY_JAMENDO_CLIENT_ID", ""))


def main(*, provider_factory: Callable[[], JamendoProvider] = _provider) -> int:
    source, destination = cast(BinaryIO, sys.stdin.buffer), cast(BinaryIO, sys.stdout.buffer)
    finished = threading.Event()

    def watchdog() -> None:
        if not finished.wait(PROVIDER_COMMAND_SECONDS):
            os._exit(124)

    try:
        write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
        tag, payload = read_frame(source, maximum=MAX_COMMAND_BYTES)
        if tag != b"G":
            raise ChildProtocolError()
        threading.Thread(target=watchdog, name="discovery-command-deadline", daemon=True).start()
        verified, evidence = execute_command(decode_document(payload), provider_factory)
        write_frame(destination, b"R", encode_result(verified, evidence))
        return 0
    except (
        DiscoveryError,
        VaultError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        OSError,
    ) as error:
        code = (
            error.code
            if isinstance(error, DiscoveryError) and error.code in DISCOVERY_CHILD_ERRORS
            else "discovery_acquisition_failed"
        )
        with suppress(ValueError, OSError):
            write_frame(destination, b"E", encode_document({"code": code}))
        return 2
    finally:
        finished.set()


if __name__ == "__main__":
    raise SystemExit(main())
