"""Bounded metadata RPC client; provider parsing in the parent performs no network I/O."""

import re
from collections.abc import Callable
from uuid import UUID

from autplay.adapters.public_track_metadata import PublicMetadataHttp
from autplay.domain.metadata_execution import MetadataExecutionTicket
from autplay.domain.track_metadata import validate_fields
from autplay.domain.vault import ChromaprintEvidence
from autplay.ports.track_metadata import EmbeddedMetadata, MetadataProviderError

from .ingest_protocol import IngestChildSettings
from .vault_child import ChildProtocolError, decode_document
from .vault_process import RetainedVaultProcess


class ProcessMetadataWork:
    def __init__(
        self,
        child: RetainedVaultProcess[MetadataExecutionTicket],
        settings: IngestChildSettings,
        begin_request: Callable[[UUID], None],
        end_request: Callable[[UUID], None],
    ) -> None:
        self.child, self.settings = child, settings
        self._begin, self._end = begin_request, end_request

    def begin(self) -> None:
        audio = self.child.ticket.audio
        self.child.go(
            {
                "version": 1,
                "execution_id": str(self.child.ticket.execution_id),
                "settings": self.settings.document(),
                "audio": None
                if audio is None
                else {
                    "recording_id": str(audio.recording_id),
                    "audio_variant_id": str(audio.audio_variant_id),
                    "vault_object_id": str(audio.vault_object_id),
                    "storage_key": audio.storage_key.value,
                    "byte_size": audio.expected.byte_size,
                    "sha256": audio.expected.sha256.hex,
                },
            }
        )
        tag, payload = self.child.read_result()
        if tag != b"R" or decode_document(payload) != {
            "ready": True,
            "execution_id": str(self.child.ticket.execution_id),
        }:
            raise ChildProtocolError()

    def exchange(
        self, command: dict[str, object], payload: bytes | None = None
    ) -> tuple[dict[str, object], bytes | None]:
        tag, document, result = self.child.metadata_exchange(
            command,
            payload,
            begin_request=self._begin,
            end_request=self._end,
        )
        if tag == b"E":
            code, retry, seconds = (
                document.get("code"),
                document.get("retryable"),
                document.get("retry_after_seconds"),
            )
            if (
                result is not None
                or set(document) != {"code", "retryable", "retry_after_seconds"}
                or not isinstance(code, str)
                or re.fullmatch(r"metadata_[a-z_]{1,80}", code) is None
                or type(retry) is not bool
                or type(seconds) is not int
                or not 1 <= seconds <= 86400
            ):
                raise ChildProtocolError()
            raise MetadataProviderError(code, retryable=retry, retry_after_seconds=seconds)
        if tag != b"R":
            raise ChildProtocolError()
        return document, result

    def read_audio(self) -> EmbeddedMetadata:
        document, artwork = self.exchange({"action": "EMBEDDED"})
        raw = document.get("fields")
        if (
            set(document) != {"fields"}
            or not isinstance(raw, dict)
            or (artwork is not None and len(artwork) > 2 * 1024 * 1024)
        ):
            raise ChildProtocolError()
        return EmbeddedMetadata(validate_fields(raw), artwork)

    def fingerprint(self) -> ChromaprintEvidence:
        document, payload = self.exchange({"action": "FINGERPRINT"})
        duration = document.get("duration_ms")
        if (
            set(document) != {"algorithm", "algorithm_version", "duration_ms"}
            or document["algorithm"] != "chromaprint"
            or document["algorithm_version"] != "1.6.1"
            or type(duration) is not int
            or not 0 < duration <= 3600000
            or payload is None
            or not payload
            or len(payload) > self.settings.tool_max_output_bytes
        ):
            raise ChildProtocolError()
        try:
            payload.decode("ascii")
        except UnicodeError as error:
            raise ChildProtocolError() from error
        return ChromaprintEvidence("chromaprint", "1.6.1", duration, payload)

    def normalize_artwork(self, payload: bytes) -> bytes:
        document, result = self.exchange({"action": "ARTWORK"}, payload)
        if (
            document
            or result is None
            or len(result) > 2 * 1024 * 1024
            or not result.startswith(b"\xff\xd8\xff")
        ):
            raise ChildProtocolError()
        return result

    def finish(self) -> None:
        if self.exchange({"action": "FINISH"}) != ({"finished": True}, None):
            raise ChildProtocolError()


class ProcessMetadataHttp(PublicMetadataHttp):
    def __init__(self, work: ProcessMetadataWork) -> None:
        # Deliberately create no HTTP client in the parent process.
        self.work = work

    def get(self, url: str, *, artwork: bool = False, post: bytes | None = None) -> bytes | None:
        document, payload = self.work.exchange(
            {
                "action": "HTTP",
                "url": url,
                "artwork": artwork,
                "post": post is not None,
            },
            post,
        )
        if document or (payload is not None and len(payload) > (4194304 if artwork else 1048576)):
            raise ChildProtocolError()
        return payload
