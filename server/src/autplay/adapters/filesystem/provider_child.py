"""One contained provider download/hash/copy/verification, only after durable GO.

The parent owns the entire OS process tree, authorization renewal and exact exit
acknowledgement. Errors never delete partial provider files or claim tree exit.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, cast
from uuid import UUID, uuid4

from autplay.adapters.child_process import provider_child_launch
from autplay.domain.vault import VaultError, VaultLimits, VerifiedStagedFile

from .provider_staging import FilesystemProviderStorage
from .vault_child import (
    MAX_COMMAND_BYTES,
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)

PROVIDER_READ_BYTES = 32 * 1024
PROVIDER_COMMAND_SECONDS = 240
type ProviderDownload = Callable[[str, Path, int], Path]


class ProviderChildError(ValueError):
    def __init__(self, code: str = "provider_download_failed") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProviderCommand:
    execution_id: UUID
    root: Path
    candidate_id: str
    limits: VaultLimits

    @classmethod
    def parse(
        cls, document: dict[str, object], *, provider: Literal["YOUTUBE", "JAMENDO"] = "YOUTUBE"
    ) -> ProviderCommand:
        if set(document) != {
            "version",
            "provider",
            "execution_id",
            "root",
            "candidate_id",
            "max_object_bytes",
            "max_chunk_bytes",
            "max_chunks",
            "io_block_bytes",
        }:
            raise ChildProtocolError()
        if type(document["version"]) is not int or document["version"] != 1:
            raise ChildProtocolError()
        if document["provider"] != provider:
            raise ChildProtocolError()
        identity, raw_root, candidate = (
            document["execution_id"],
            document["root"],
            document["candidate_id"],
        )
        if not all(isinstance(value, str) for value in (identity, raw_root, candidate)):
            raise ChildProtocolError()
        identity, raw_root, candidate = (
            cast(str, identity),
            cast(str, raw_root),
            cast(str, candidate),
        )
        execution_id, root = UUID(hex=identity), Path(raw_root)
        if (
            execution_id.hex != identity
            or not root.is_absolute()
            or ".." in root.parts
            or len(raw_root) > 4096
            or re.fullmatch(
                r"[0-9]{1,20}" if provider == "JAMENDO" else r"[A-Za-z0-9_-]{11}", candidate
            )
            is None
        ):
            raise ChildProtocolError()
        bounds: dict[str, int] = {}
        for name, maximum in (
            ("max_object_bytes", 2 * 1024**3),
            ("max_chunk_bytes", 1024**2),
            ("max_chunks", 2**53 - 1),
            ("io_block_bytes", 1024**2),
        ):
            value = document[name]
            if type(value) is not int or not 1 <= value <= maximum:
                raise ChildProtocolError()
            bounds[name] = value
        return cls(execution_id, root, candidate, VaultLimits(**bounds))


def youtube_arguments(candidate: str) -> list[str]:
    """Only a validated exact YouTube identity, never a provider-returned URL or command."""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) is None:
        raise ChildProtocolError()
    launcher, _ = provider_child_launch()
    return [
        launcher[0],
        "-I",
        "-m",
        "autplay.adapters.filesystem.provider_media",
        candidate,
    ]


def download_youtube(candidate: str, workspace: Path, maximum: int) -> Path:
    """Only this bounded writer creates media files; the helper streams to stdout."""
    _, environment = provider_child_launch()
    proxy = environment.pop("AUTPLAY_MUSIC_PROXY", None)
    if proxy:
        environment["ALL_PROXY"] = proxy
    for name in ("TMP", "TEMP", "TMPDIR"):
        environment[name] = str(workspace)
    process = subprocess.Popen(
        youtube_arguments(candidate),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        cwd=workspace,
        close_fds=True,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.stdout is not None
    try:
        source = workspace / "audio.media"
        with source.open("xb") as output:
            written = 0
            while block := process.stdout.read(min(PROVIDER_READ_BYTES, maximum + 1 - written)):
                if written + len(block) > maximum:
                    raise ProviderChildError("provider_download_too_large")
                output.write(block)
                written += len(block)
            output.flush()
            os.fsync(output.fileno())
        # The child's independent command watchdog also bounds a stuck wait/pipe.
        if process.wait() != 0 or written == 0:
            raise ProviderChildError()
        return source
    finally:
        process.stdout.close()
        if process.poll() is None:
            with suppress(OSError):
                process.kill()
        # No wait here: the parent retains the Job/cgroup and proves complete exit.


def execute_command(
    command: ProviderCommand, *, download: ProviderDownload = download_youtube
) -> VerifiedStagedFile:
    storage = FilesystemProviderStorage(command.root)
    workspace = storage.create_workspace(command.execution_id)
    source = download(command.candidate_id, workspace, command.limits.max_object_bytes)
    return storage.copy_verified_source(command.execution_id, source, limits=command.limits)


def main(*, download: ProviderDownload = download_youtube) -> int:
    source, destination = cast(BinaryIO, sys.stdin.buffer), cast(BinaryIO, sys.stdout.buffer)
    finished = threading.Event()

    def watchdog() -> None:
        if not finished.wait(PROVIDER_COMMAND_SECONDS):
            # Root death is not tree-exit proof. Parent must still stop all descendants.
            os._exit(124)

    try:
        write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
        tag, payload = read_frame(source, maximum=MAX_COMMAND_BYTES)
        if tag != b"G":
            raise ChildProtocolError()
        command = ProviderCommand.parse(decode_document(payload))
        threading.Thread(target=watchdog, name="provider-command-deadline", daemon=True).start()
        result = execute_command(command, download=download)
        write_frame(
            destination,
            b"R",
            encode_document(
                {
                    "byte_size": result.byte_size,
                    "sha256": result.sha256.hex,
                }
            ),
        )
        return 0
    except (VaultError, ValueError, TypeError, KeyError, OverflowError, OSError) as error:
        code = (
            error.code
            if isinstance(error, (VaultError, ProviderChildError))
            else "provider_command_failed"
        )
        with suppress(ValueError, OSError):
            write_frame(destination, b"E", encode_document({"code": code}))
        return 2
    finally:
        finished.set()


if __name__ == "__main__":
    raise SystemExit(main())
