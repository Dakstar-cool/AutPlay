"""One contained MusicBrainz GET after the parent commits an exact durable grant."""

import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import BinaryIO
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from autplay.adapters.public_track_metadata import PublicMetadataHttp
from autplay.ports.track_metadata import MetadataProviderError

from .metadata_protocol import write_packet
from .vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)


def execute(command: dict[str, object], destination: BinaryIO) -> None:
    if (
        set(command) != {"version", "execution_id", "request_id", "url"}
        or type(command["version"]) is not int
        or command["version"] != 1
    ):
        raise ChildProtocolError()
    for key in ("execution_id", "request_id"):
        identity = command[key]
        if not isinstance(identity, str):
            raise ChildProtocolError()
        UUID(identity)
    url = command["url"]
    if not isinstance(url, str) or not 1 <= len(url) <= 8192:
        raise ChildProtocolError()
    parsed = urlsplit(url)
    if (
        parsed.hostname != "musicbrainz.org"
        or re.fullmatch(
            r"/ws/2/(?:artist|recording|release|release/[0-9a-fA-F-]{36})",
            parsed.path,
        )
        is None
    ):
        raise MetadataProviderError("metadata_url_rejected", retryable=False)
    sent = False

    @contextmanager
    def single_grant() -> Iterator[None]:
        nonlocal sent
        if sent:
            raise ChildProtocolError()
        sent = True
        yield

    http = PublicMetadataHttp(single_grant, proxy=os.environ.get("AUTPLAY_METADATA_PROXY"))
    try:
        payload = http.get(url)
        write_packet(destination, b"R", {}, payload)
    finally:
        http.client.close()


def main() -> int:
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    try:
        write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
        tag, payload = read_frame(source, maximum=16384)
        if tag != b"G":
            raise ChildProtocolError()
        execute(decode_document(payload), destination)
        return 0
    except MetadataProviderError as error:
        write_packet(
            destination,
            b"E",
            {
                "code": error.code,
                "retryable": error.retryable,
                "retry_after_seconds": error.retry_after_seconds,
            },
        )
        return 0
    except ValueError, TypeError, KeyError, OverflowError, OSError:
        with suppress(OSError, ValueError):
            write_packet(
                destination,
                b"E",
                {
                    "code": "metadata_child_failed",
                    "retryable": True,
                    "retry_after_seconds": 60,
                },
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
