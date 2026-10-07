"""Real contained pipe fixture; deterministic payloads and no external network."""

import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from autplay.adapters.filesystem.metadata_protocol import write_packet
from autplay.adapters.filesystem.vault_child import (
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)

source, destination = sys.stdin.buffer, sys.stdout.buffer
write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
tag, envelope = read_frame(source, maximum=16384)
assert tag == b"G"
command = decode_document(envelope)
assert set(command) == {"version", "execution_id", "request_id", "url"}
assert not any("DATABASE" in key or "AUTH" in key for key in os.environ)
marker = os.environ.get("CATALOG_TEST_MARKER")
if marker:
    request_id = command["request_id"]
    assert isinstance(request_id, str)
    Path(marker).write_text(request_id, encoding="ascii")
mode = os.environ.get("CATALOG_TEST_MODE", "reply")
if mode == "hang":
    time.sleep(60)
elif mode == "malformed":
    write_frame(destination, b"R", encode_document({"document": {}, "size": 1048577}))
elif mode == "error":
    write_packet(
        destination,
        b"E",
        {
            "code": "metadata_provider_busy",
            "retryable": True,
            "retry_after_seconds": 2,
        },
    )
else:
    time.sleep(0.1)
    write_packet(destination, b"R", {}, b'{"value":"' + b"x" * 150000 + b'"}')
