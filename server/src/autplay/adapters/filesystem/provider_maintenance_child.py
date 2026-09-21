"""One exact claimed retirement; no database, credentials or descendant processes."""

import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from autplay.application.orphan_object_retirement import OrphanObjectClaim
from autplay.application.provider_cleanup import ProviderCleanupClaim
from autplay.application.provider_scratch import ProviderScratchClaim
from autplay.application.upload_cleanup import UploadCleanupClaim
from autplay.domain.provider_maintenance import MaintenanceAction
from autplay.domain.vault import OpaqueStorageKey, VaultError

from .orphan_object_retirement import FilesystemOrphanObjectRetirement
from .provider_staging import FilesystemProviderStorage
from .upload_cleanup import FilesystemUploadCleanup
from .vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)


def execute_command(command: dict[str, object], destination: BinaryIO) -> None:
    action = MaintenanceAction(str(command.get("action")))
    if action == MaintenanceAction.INVENTORY:
        raise ChildProtocolError()
    target = (
        "storage_key"
        if action
        in (
            MaintenanceAction.ORPHAN_OBJECT,
            MaintenanceAction.ORPHAN_MISSING,
            MaintenanceAction.UPLOAD_CLEANUP,
        )
        else "provider_execution_id"
    )
    if set(command) != {"version", "root", target, "claim_id", "action"} or (
        type(command["version"]) is not int or command["version"] != 1
    ):
        raise ChildProtocolError()
    if not all(isinstance(command[key], str) for key in command if key != "version"):
        raise ChildProtocolError()
    root = Path(str(command["root"]))
    if not root.is_absolute():
        raise ChildProtocolError()
    claim_id = UUID(str(command["claim_id"]))
    if str(claim_id) != command["claim_id"]:
        raise ChildProtocolError()
    if action == MaintenanceAction.UPLOAD_CLEANUP:
        FilesystemUploadCleanup(root).retire(
            UploadCleanupClaim(claim_id, OpaqueStorageKey(str(command[target])))
        )
    elif action in (MaintenanceAction.ORPHAN_OBJECT, MaintenanceAction.ORPHAN_MISSING):
        orphan = OrphanObjectClaim(claim_id, OpaqueStorageKey(str(command[target])))
        storage = FilesystemOrphanObjectRetirement(root)
        if action == MaintenanceAction.ORPHAN_MISSING:
            storage.confirm_missing(orphan)
        else:
            storage.retire(orphan)
    else:
        execution = UUID(str(command[target]))
        if str(execution) != command[target]:
            raise ChildProtocolError()
        if action == MaintenanceAction.CLEANUP:
            cleanup = ProviderCleanupClaim(execution, claim_id)
            FilesystemProviderStorage(root).retire(cleanup)
        else:
            scratch = ProviderScratchClaim(execution, claim_id)
            FilesystemProviderStorage(root).retire_scratch(scratch)
    write_frame(
        destination,
        b"R",
        encode_document(
            {
                target: command[target],
                "claim_id": str(claim_id),
                "action": action.value,
            }
        ),
    )


def main() -> int:
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    try:
        write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
        tag, payload = read_frame(source, maximum=8192)
        if tag != b"G":
            raise ChildProtocolError()
        execute_command(decode_document(payload), destination)
        return 0
    except VaultError, ValueError, TypeError, KeyError, OverflowError, OSError:
        with suppress(ValueError, OSError):
            write_frame(destination, b"E", encode_document({"code": "maintenance_storage_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
