"""Read embedded tags and decode artwork with bounded local CPU tools."""

import json
from pathlib import Path

from autplay.adapters.media.tools import SubprocessExecutableRunner
from autplay.domain.track_metadata import MetadataFields, validate_fields
from autplay.domain.vault import MediaValidationError
from autplay.ports.track_metadata import EmbeddedMetadata, MetadataProviderError
from autplay.ports.vault import ExecutableRunner

_TAG_FIELDS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "album_artist": "album_artist",
    "date": "release_date",
    "year": "release_date",
    "originaldate": "original_release_date",
    "originalyear": "original_release_date",
    "recordingdate": "recording_date",
    "publisher": "label",
    "label": "label",
    "musicbrainz_trackid": "mb_recording_id",
    "musicbrainz_albumid": "mb_release_id",
    "musicbrainz_releasegroupid": "mb_release_group_id",
}


def fields_from_tags(tags: dict[str, object]) -> MetadataFields:
    result: MetadataFields = {}
    # A legacy year tag must not erase a more precise DATE/ORIGINALDATE value.
    ordered = sorted(
        tags.items(), key=lambda item: item[0].casefold() not in {"year", "originalyear"}
    )
    for name, value in ordered:
        key = name.casefold().replace(" ", "_")
        target = _TAG_FIELDS.get(key)
        if key in {"track", "disc"}:
            target = f"{key}_number"
            try:
                value = int(str(value).split("/", 1)[0])
            except ValueError:
                continue
        if key == "genre":
            target, value = "genres", [str(value)]
        if target:
            try:
                result.update(validate_fields({target: value}))
            except ValueError:
                continue
    return result


class FfmpegMetadataReader:
    def __init__(self, runner: ExecutableRunner) -> None:
        self.runner = runner

    def read(self, path: Path) -> EmbeddedMetadata:
        probe = self.runner.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_entries",
                "format_tags:stream_tags:stream=index,codec_type,width,height:stream_disposition=attached_pic",
                "-of",
                "json",
                str(path),
            ],
            timeout_seconds=20,
            max_output_bytes=262144,
        )
        if probe.returncode != 0:
            raise MetadataProviderError("metadata_tags_unreadable", retryable=False)
        try:
            data = json.loads(probe.stdout)
            tags = dict(data.get("format", {}).get("tags", {}))
            streams = data.get("streams", [])
            for stream in streams:
                if stream.get("codec_type") == "audio":
                    tags.update(stream.get("tags", {}))
            picture = next(
                (
                    s
                    for s in streams
                    if s.get("disposition", {}).get("attached_pic") == 1
                    and 0 < s.get("width", 0) <= 4096
                    and 0 < s.get("height", 0) <= 4096
                ),
                None,
            )
            try:
                art = self._jpeg(path, int(picture["index"])) if picture else None
            except MetadataProviderError, MediaValidationError:
                art = None
            return EmbeddedMetadata(fields_from_tags(tags), art)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise MetadataProviderError("metadata_tags_invalid", retryable=False) from error

    def normalize_artwork(self, payload: bytes) -> bytes:
        if not 1 <= len(payload) <= 4194304:
            raise MetadataProviderError("metadata_artwork_size", retryable=False)
        if not isinstance(self.runner, SubprocessExecutableRunner):
            raise MetadataProviderError("metadata_artwork_decoder_unavailable", retryable=False)
        probe = self.runner.run_input(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "pipe",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                "pipe:0",
            ],
            payload,
            timeout_seconds=10,
            max_output_bytes=4096,
        )
        try:
            streams = json.loads(probe.stdout)["streams"]
            if (
                probe.returncode
                or len(streams) != 1
                or not all(0 < streams[0][axis] <= 4096 for axis in ("width", "height"))
            ):
                raise ValueError("dimensions")
        except (ValueError, TypeError, KeyError) as error:
            raise MetadataProviderError("metadata_artwork_invalid", retryable=False) from error
        result = self.runner.run_input(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "pipe",
                "-i",
                "pipe:0",
                "-map",
                "0:0",
                "-frames:v",
                "1",
                "-threads",
                "1",
                "-vf",
                "scale=500:500:force_original_aspect_ratio=decrease",
                "-c:v",
                "mjpeg",
                "-q:v",
                "3",
                "-f",
                "image2pipe",
                "pipe:1",
            ],
            payload,
            timeout_seconds=20,
            max_output_bytes=2097152,
        )
        if result.returncode or not result.stdout.startswith(b"\xff\xd8\xff"):
            raise MetadataProviderError("metadata_artwork_invalid", retryable=False)
        return result.stdout

    def _jpeg(self, path: Path, stream: int) -> bytes:
        result = self.runner.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-map",
                f"0:{stream}",
                "-frames:v",
                "1",
                "-threads",
                "1",
                "-vf",
                "scale=500:500:force_original_aspect_ratio=decrease",
                "-c:v",
                "mjpeg",
                "-q:v",
                "3",
                "-f",
                "image2pipe",
                "pipe:1",
            ],
            timeout_seconds=20,
            max_output_bytes=2097152,
        )
        if result.returncode or not result.stdout.startswith(b"\xff\xd8\xff"):
            raise MetadataProviderError("metadata_artwork_invalid", retryable=False)
        return result.stdout
