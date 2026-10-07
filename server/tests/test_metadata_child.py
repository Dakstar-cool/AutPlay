"""Bounded metadata packets, actual pipe media and stable media failures."""

import io
import os
import sys
import wave
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem import metadata_child
from autplay.adapters.filesystem.ingest_protocol import IngestChildSettings
from autplay.adapters.filesystem.metadata_process import ProcessMetadataWork
from autplay.adapters.filesystem.metadata_protocol import (
    metadata_query_document,
    parse_metadata_query,
    read_packet,
    write_packet,
)
from autplay.adapters.filesystem.vault_child import ChildProtocolError, encode_document, read_frame
from autplay.adapters.media.tools import SubprocessExecutableRunner
from autplay.adapters.media.track_metadata import FfmpegMetadataReader
from autplay.domain.track_metadata import MetadataQuery, parse_source_metadata
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


@pytest.mark.skipif(os.name == "nt", reason="pinned image tools are in the Linux proof image")
def test_actual_embedded_reader_measures_audio_duration_without_date_inference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes(b"\0\0" * 55125)
    embedded = FfmpegMetadataReader(SubprocessExecutableRunner()).read(path)
    assert embedded.duration_ms == 1250 and embedded.fields == {} and embedded.artwork is None
    assert list(tmp_path.iterdir()) == [path]


def test_query_protocol_roundtrips_multilingual_acquisition_evidence_and_partial_dates() -> None:
    source = parse_source_metadata(
        {
            "schema_version": 1,
            "provider": "YANDEX",
            "source_id": "123",
            "fields": {
                "title": "Песня (Remix)",
                "album": "Альбом",
                "release_date": "2001-04",
                "genres": ["Новый жанр"],
            },
            "external_ids": {"native_album_id": "42"},
            "artwork": [
                {"kind": "thumbnail", "url": "https://avatars.yandex.net/a.jpg", "source_id": "42"}
            ],
        }
    )
    query = MetadataQuery(
        "Песня (Remix)", "Артист", "Альбом", 123456, release_date="2001-04", source_metadata=source
    )
    pipe = io.BytesIO()
    write_packet(pipe, b"N", metadata_query_document(query))
    pipe.seek(0)
    _, envelope = read_frame(pipe, maximum=65536)
    raw, payload = read_packet(pipe, envelope)
    assert payload is None and parse_metadata_query(raw) == query


@pytest.mark.parametrize(
    "patch",
    [
        {"title": "x" * 501},
        {"duration_ms": True},
        {"duration_ms": 3600001},
        {"track_number": 0},
        {"release_date": "2025-02-29"},
        {"recording_mbid": "invalid"},
        {
            "source_metadata": {
                "schema_version": 1,
                "provider": "YANDEX",
                "source_id": "1",
                "fields": {"title": "x" * 501},
            }
        },
    ],
)
def test_query_protocol_rejects_invalid_bounds_and_unsanitized_evidence(
    patch: dict[str, object],
) -> None:
    document = metadata_query_document(MetadataQuery("Song", "Artist"))
    with pytest.raises(ChildProtocolError):
        parse_metadata_query(document | patch)


def test_embedded_rpc_preserves_legacy_reply_and_measured_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = object.__new__(ProcessMetadataWork)
    monkeypatch.setattr(work, "exchange", lambda command: ({"fields": {"title": "Song"}}, None))
    assert work.read_audio().duration_ms is None
    monkeypatch.setattr(
        work,
        "exchange",
        lambda command: ({"fields": {"title": "Song"}, "duration_ms": 180321}, None),
    )
    assert work.read_audio().duration_ms == 180321
    monkeypatch.setattr(
        work, "exchange", lambda command: ({"fields": {}, "duration_ms": True}, None)
    )
    with pytest.raises(ChildProtocolError):
        work.read_audio()


def test_query_protocol_preserves_exact_occurrence_uuid_and_old_snapshot_shape() -> None:
    query = MetadataQuery(
        "Song",
        "Artist",
        "Album",
        175000,
        str(UUID(int=1)),
        release_mbid=str(UUID(int=10)),
        release_track_mbid=str(UUID(int=108)),
    )
    document = metadata_query_document(query)
    assert parse_metadata_query(document) == query
    del document["release_track_mbid"]
    restored = parse_metadata_query(document)
    assert restored.release_track_mbid is None and restored.recording_mbid == query.recording_mbid


@pytest.mark.parametrize("identity", ["invalid", 108, True, "x" * 501])
def test_query_protocol_rejects_malformed_occurrence_uuid(identity: object) -> None:
    document = metadata_query_document(MetadataQuery("Song", "Artist"))
    with pytest.raises(ChildProtocolError):
        parse_metadata_query(document | {"release_track_mbid": identity})
