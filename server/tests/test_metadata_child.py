"""Bounded metadata packets, actual pipe media and stable media failures."""

import io
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem import metadata_child
from autplay.adapters.filesystem.ingest_protocol import IngestChildSettings
from autplay.adapters.filesystem.metadata_protocol import read_packet, write_packet
from autplay.adapters.filesystem.vault_child import ChildProtocolError, encode_document, read_frame
from autplay.adapters.media.tools import SubprocessExecutableRunner
from autplay.adapters.media.track_metadata import FfmpegMetadataReader
from autplay.domain.vault import MediaToolTimeoutError, MediaValidationError


def test_packets_preserve_optional_payload_and_enforce_declared_limit() -> None:
    pipe = io.BytesIO()
    payload = b"p" * (4 * 1024 * 1024)
    write_packet(pipe, b"R", {}, payload)
    pipe.seek(0)
    tag, envelope = read_frame(pipe, maximum=65536)
    assert tag == b"R" and read_packet(pipe, envelope) == ({}, payload)
    with pytest.raises(ChildProtocolError):
        read_packet(io.BytesIO(), encode_document({"document": {}, "size": len(payload) + 1}))


def test_child_media_failure_is_terminal_and_pipe_remains_framed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, destination = io.BytesIO(), io.BytesIO()
    write_packet(source, b"N", {"action": "ARTWORK"}, b"image")
    write_packet(source, b"N", {"action": "FINISH"})
    source.seek(0)

    def fail(self: FfmpegMetadataReader, payload: bytes) -> bytes:
        del self, payload
        raise MediaValidationError()

    monkeypatch.setattr(FfmpegMetadataReader, "normalize_artwork", fail)
    metadata_child.execute(
        {
            "version": 1,
            "execution_id": str(uuid4()),
            "audio": None,
            "settings": IngestChildSettings(tmp_path).document(),
        },
        source,
        destination,
    )
    destination.seek(0)
    assert read_frame(destination)[0] == b"R"
    tag, envelope = read_frame(destination)
    assert tag == b"E"
    assert read_packet(destination, envelope) == (
        {"code": "metadata_media_unreadable", "retryable": False, "retry_after_seconds": 60},
        None,
    )
    tag, envelope = read_frame(destination)
    assert tag == b"R" and read_packet(destination, envelope) == ({"finished": True}, None)


def test_tool_input_is_bounded_and_blocked_writer_is_terminated() -> None:
    runner = SubprocessExecutableRunner()
    payload = b"p" * (4 * 1024 * 1024)
    result = runner.run_input(
        [sys.executable, "-c", "import sys; print(len(sys.stdin.buffer.read()))"],
        payload,
        timeout_seconds=5,
        max_output_bytes=1024,
    )
    assert result.returncode == 0 and int(result.stdout) == len(payload)
    with pytest.raises(MediaToolTimeoutError):
        runner.run_input(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            payload,
            timeout_seconds=0.1,
            max_output_bytes=1024,
        )


@pytest.mark.skipif(os.name == "nt", reason="pinned image tools are in the Linux proof image")
def test_actual_artwork_normalization_uses_only_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    image = b"P6\n2 2\n255\n" + b"\xff\x00\x00" * 4
    result = FfmpegMetadataReader(SubprocessExecutableRunner()).normalize_artwork(image)
    assert result.startswith(b"\xff\xd8\xff")
    assert list(tmp_path.iterdir()) == []
