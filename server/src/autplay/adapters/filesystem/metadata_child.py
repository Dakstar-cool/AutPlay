"""Contained metadata bytes and media tools; private RPC never carries DB credentials."""

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import BinaryIO
from uuid import UUID, uuid4

from autplay.adapters.media.tools import ChromaprintTool, SubprocessExecutableRunner
from autplay.adapters.media.track_metadata import FfmpegMetadataReader
from autplay.adapters.public_track_metadata import PublicMetadataHttp
from autplay.domain.vault import MediaValidationError, OpaqueStorageKey, VaultError
from autplay.ports.track_metadata import MetadataProviderError

from .ingest_protocol import IngestChildSettings, parse_verified, string
from .ingest_storage import ExistingIngestStorage
from .metadata_protocol import read_packet, write_packet
from .vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)


def execute(command: dict[str, object], source: BinaryIO, destination: BinaryIO) -> None:
    if (
        set(command) != {"version", "execution_id", "settings", "audio"}
        or type(command["version"]) is not int
        or command["version"] != 1
    ):
        raise ChildProtocolError()
    execution = UUID(string(command["execution_id"]))
    config, audio = command["settings"], command["audio"]
    if not isinstance(config, dict) or (audio is not None and not isinstance(audio, dict)):
        raise ChildProtocolError()
    settings = IngestChildSettings.parse(config)
    storage, key, expected = None, None, None
    if audio is not None:
        if set(audio) != {
            "recording_id",
            "audio_variant_id",
            "vault_object_id",
            "storage_key",
            "byte_size",
            "sha256",
        }:
            raise ChildProtocolError()
        key = OpaqueStorageKey(string(audio["storage_key"]))
        expected = parse_verified(
            {"byte_size": audio["byte_size"], "sha256": audio["sha256"]},
            maximum=settings.limits.max_object_bytes,
        )
        if key.value != expected.sha256.hex:
            raise ChildProtocolError()
        storage = ExistingIngestStorage(settings.root, limits=settings.limits)

    @contextmanager
    def gate() -> Iterator[None]:
        payload = encode_document({"request_id": str(uuid4())})

        def exchange(tag: bytes) -> None:
            write_frame(destination, tag, payload)
            kind, acknowledgement = read_frame(source, maximum=8192)
            if kind != b"A" or acknowledgement != payload:
                raise ChildProtocolError()

        exchange(b"B")
        try:
            yield
        finally:
            exchange(b"C")

    http = PublicMetadataHttp(gate, proxy=os.environ.get("AUTPLAY_METADATA_PROXY"))
    reader = FfmpegMetadataReader(SubprocessExecutableRunner())
    write_frame(destination, b"R", encode_document({"ready": True, "execution_id": str(execution)}))
    try:
        while True:
            tag, envelope = read_frame(source, maximum=65536)
            if tag != b"N":
                raise ChildProtocolError()
            request, payload = read_packet(source, envelope)
            action = request.get("action")
            document: dict[str, object] = {}
            result: bytes | None = None
            try:
                if action == "FINISH" and set(request) == {"action"} and payload is None:
                    write_packet(destination, b"R", {"finished": True})
                    return
                if (
                    action in {"EMBEDDED", "FINGERPRINT"}
                    and set(request) == {"action"}
                    and payload is None
                ):
                    if storage is None or key is None or expected is None:
                        raise ChildProtocolError()
                    storage.check_namespace()
                    if storage.verify_object(key) != expected:
                        raise MetadataProviderError("metadata_audio_integrity", retryable=False)
                    path = storage._object_path(key)
                    if action == "EMBEDDED":
                        embedded = reader.read(path)
                        document, result = {"fields": embedded.fields}, embedded.artwork
                    else:
                        fingerprint = ChromaprintTool(
                            "fpcalc",
                            algorithm_version="1.6.1",
                            timeout_seconds=settings.tool_timeout_seconds,
                            max_output_bytes=settings.tool_max_output_bytes,
                        ).fingerprint(path)
                        document, result = (
                            {
                                "algorithm": fingerprint.algorithm,
                                "algorithm_version": fingerprint.algorithm_version,
                                "duration_ms": fingerprint.duration_ms,
                            },
                            fingerprint.payload,
                        )
                    storage.check_namespace()
                    if storage.verify_object(key) != expected:
                        raise MetadataProviderError("metadata_audio_integrity", retryable=False)
                elif action == "ARTWORK" and set(request) == {"action"} and payload is not None:
                    result = reader.normalize_artwork(payload)
                elif action == "HTTP" and set(request) == {"action", "url", "artwork", "post"}:
                    if (
                        type(request["artwork"]) is not bool
                        or type(request["post"]) is not bool
                        or request["post"] != (payload is not None)
                    ):
                        raise ChildProtocolError()
                    result = http.get(
                        string(request["url"], maximum=8192),
                        artwork=request["artwork"],
                        post=payload,
                    )
                else:
                    raise ChildProtocolError()
                write_packet(destination, b"R", document, result)
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
            except MediaValidationError:
                write_packet(
                    destination,
                    b"E",
                    {
                        "code": "metadata_media_unreadable",
                        "retryable": False,
                        "retry_after_seconds": 60,
                    },
                )
            except VaultError:
                write_packet(
                    destination,
                    b"E",
                    {
                        "code": "metadata_storage_unavailable",
                        "retryable": True,
                        "retry_after_seconds": 60,
                    },
                )
    finally:
        http.client.close()


def main() -> int:
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    try:
        write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
        tag, payload = read_frame(source, maximum=8192)
        if tag != b"G":
            raise ChildProtocolError()
        execute(decode_document(payload), source, destination)
        return 0
    except VaultError, ValueError, TypeError, KeyError, OverflowError, OSError:
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
