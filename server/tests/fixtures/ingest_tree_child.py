"""Bounded synthetic stuck ingest phase with a detached descendant; no network."""

import os
import subprocess
import sys
from pathlib import Path
from time import sleep
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
        sleep(30)
        return
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
    tag, payload = read_frame(source)
    assert tag == b"G"
    command = decode_document(payload)
    settings = command["settings"]
    assert isinstance(settings, dict) and isinstance(settings["root"], str)
    marker = Path(settings["root"]) / "ingest-descendant"
    subprocess.Popen(
        [sys.executable, "-I", __file__, "descendant", str(marker)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=os.name != "nt",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    write_frame(
        destination, b"R", encode_document({"execution_id": command["execution_id"], "ready": True})
    )
    assert read_frame(source)[0] == b"N"
    sleep(30)


if __name__ == "__main__":
    main()
