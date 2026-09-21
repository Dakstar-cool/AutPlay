"""One scanner, no descendants or credentials, with a bounded page per command."""

import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from autplay.domain.vault import VaultError

from .inventory_protocol import MAX_INVENTORY_REPLY_BYTES, page_command, page_document
from .vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)
from .vault_inventory import FilesystemVaultInventoryCursor


def execute_command(command: dict[str, object], source: BinaryIO, destination: BinaryIO) -> None:
    if (
        set(command) != {"version", "root", "action", "claim_id"}
        or type(command["version"]) is not int
        or command["version"] != 1
        or command["action"] != "INVENTORY"
        or not isinstance(command["root"], str)
        or not isinstance(command["claim_id"], str)
    ):
        raise ChildProtocolError()
    root, claim_id = Path(command["root"]), UUID(command["claim_id"])
    if not root.is_absolute() or str(claim_id) != command["claim_id"]:
        raise ChildProtocolError()
    cursor = FilesystemVaultInventoryCursor(root)
    try:
        write_frame(
            destination,
            b"R",
            encode_document({"action": "INVENTORY", "claim_id": str(claim_id)}),
        )
        sequence = 0
        while True:
            tag, payload = read_frame(source, maximum=8192)
            request = decode_document(payload)
            sequence += 1
            maximum = request.get("maximum")
            if (
                type(maximum) is not int
                or type(request.get("sequence")) is not int
                or tag != b"N"
                or request != page_command(sequence, maximum)
            ):
                raise ChildProtocolError()
            page = cursor.next_page(maximum=maximum)
            write_frame(
                destination,
                b"R",
                encode_document(
                    page_document(claim_id, sequence, page), maximum=MAX_INVENTORY_REPLY_BYTES
                ),
            )
            if page.exhausted:
                return
    finally:
        cursor.close()


def main() -> int:
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    try:
        write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
        tag, payload = read_frame(source, maximum=8192)
        if tag != b"G":
            raise ChildProtocolError()
        execute_command(decode_document(payload), source, destination)
        return 0
    except VaultError, ValueError, TypeError, KeyError, OverflowError, OSError:
        with suppress(ValueError, OSError):
            write_frame(destination, b"E", encode_document({"code": "maintenance_storage_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
