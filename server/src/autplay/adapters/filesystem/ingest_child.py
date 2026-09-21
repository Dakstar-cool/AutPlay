"""Fixed contained ingest phases; all filesystem/media work starts after durable GO."""

import os
import sys
from contextlib import suppress
from typing import BinaryIO
from uuid import UUID, uuid4

from autplay.adapters.media.tools import (
    ChromaprintTool,
    FfmpegDecodeValidator,
    FfprobeInspector,
    ValidatedMediaInspector,
)
from autplay.domain.vault import OpaqueStorageKey, VaultError

from .ingest_protocol import (
    MAX_INGEST_REPLY,
    IngestChildSettings,
    analysis_document,
    parse_verified,
    string,
)
from .ingest_storage import ExistingIngestStorage
from .vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)


def execute(command: dict[str, object], source: BinaryIO, destination: BinaryIO) -> None:
    if (
        set(command) != {"version", "execution_id", "mode", "key", "settings", "expected"}
        or command["version"] != 1
        or type(command["version"]) is not int
    ):
        raise ChildProtocolError()
    execution = UUID(string(command["execution_id"]))
    if str(execution) != command["execution_id"]:
        raise ChildProtocolError()
    mode, key = command["mode"], OpaqueStorageKey(string(command["key"]))
    settings_document = command["settings"]
    if (
        not isinstance(mode, str)
        or mode not in {"WORK", "CLEANUP"}
        or not isinstance(settings_document, dict)
    ):
        raise ChildProtocolError()
    settings = IngestChildSettings.parse(settings_document)
    expected = None
    if mode == "CLEANUP":
        value = command["expected"]
        if not isinstance(value, dict) or set(value) != {"byte_size", "sha256"}:
            raise ChildProtocolError()
        expected = parse_verified(value, maximum=settings.limits.max_object_bytes)
    elif command["expected"] is not None:
        raise ChildProtocolError()
    storage = ExistingIngestStorage(settings.root, limits=settings.limits)
    verified = None
    analyzed = published = False
    write_frame(destination, b"R", encode_document({"execution_id": str(execution), "ready": True}))
    while True:
        tag, payload = read_frame(source, maximum=8192)
        request = decode_document(payload)
        if tag != b"N" or set(request) != {"action"}:
            raise ChildProtocolError()
        action = request["action"]
        reply: dict[str, object]
        storage.check_namespace()
        if mode == "CLEANUP":
            if action != "CLEANUP" or expected is None:
                raise ChildProtocolError()
            storage.cleanup_finalized(key, expected)
            write_frame(
                destination,
                b"R",
                encode_document({"execution_id": str(execution), "cleaned": True}),
            )
            return
        if action == "CAPACITY":
            reply = {"available_bytes": storage.available_bytes()}
        elif action == "VERIFY" and verified is None:
            verified = storage.verify_staging(key)
            reply = {"byte_size": verified.byte_size, "sha256": verified.sha256.hex}
        elif action == "ANALYZE" and verified is not None and not analyzed:
            path = storage.staging_path_for_media(key)
            media = ValidatedMediaInspector(
                FfmpegDecodeValidator(
                    "ffmpeg",
                    timeout_seconds=settings.tool_timeout_seconds,
                    max_output_bytes=settings.tool_max_output_bytes,
                ),
                FfprobeInspector(
                    "ffprobe",
                    timeout_seconds=settings.tool_timeout_seconds,
                    max_output_bytes=settings.tool_max_output_bytes,
                ),
            )
            fingerprint = ChromaprintTool(
                "fpcalc",
                algorithm_version="1.6.1",
                timeout_seconds=settings.tool_timeout_seconds,
                max_output_bytes=settings.tool_max_output_bytes,
            )
            reply = analysis_document(media.inspect(path), fingerprint.fingerprint(path))
            analyzed = True
        elif action == "PUBLISH" and verified is not None and analyzed and not published:
            result = storage.commit_staging(key, verified)
            reply = {"key": result.storage_key.value, "already_present": result.already_present}
            published = True
        elif action == "FINISH":
            write_frame(destination, b"R", encode_document({"finished": True}))
            return
        else:
            raise ChildProtocolError()
        storage.check_namespace()
        write_frame(destination, b"R", encode_document(reply, maximum=MAX_INGEST_REPLY))


def main() -> int:
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    try:
        write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
        tag, payload = read_frame(source, maximum=8192)
        if tag != b"G":
            raise ChildProtocolError()
        execute(decode_document(payload), source, destination)
        return 0
    except (VaultError, ValueError, TypeError, KeyError, OverflowError, OSError) as error:
        with suppress(OSError, ValueError):
            code = error.code if isinstance(error, VaultError) else "vault_storage_unavailable"
            write_frame(destination, b"E", encode_document({"code": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
