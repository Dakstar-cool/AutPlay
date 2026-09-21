"""Bounded synthetic provider; no descendants or filesystem writes before GO."""

import os
import subprocess
import sys
from pathlib import Path
from time import sleep
from typing import BinaryIO, cast
from uuid import uuid4

from autplay.adapters.filesystem.vault_child import (
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "descendant":
        Path(sys.argv[2]).write_text("descendant running", encoding="ascii")
        sleep(30)  # Independent upper bound, even if the test supervisor fails.
        return
    source, destination = cast(BinaryIO, sys.stdin.buffer), cast(BinaryIO, sys.stdout.buffer)
    write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
    tag, payload = read_frame(source)
    assert tag == b"G"
    command = decode_document(payload)
    marker = command["marker"]
    assert isinstance(marker, str)
    descendant = subprocess.Popen(
        [sys.executable, "-I", __file__, "descendant", marker],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=os.name != "nt",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    write_frame(destination, b"R", encode_document({"descendant_pid": descendant.pid}))
    # Intentionally exit without waiting for the descendant.


if __name__ == "__main__":
    main()
