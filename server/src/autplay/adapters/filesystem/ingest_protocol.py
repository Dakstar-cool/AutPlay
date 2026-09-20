"""Bounded private ingest frames carry data, never executable commands."""

from __future__ import annotations

import base64
import binascii
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    Sha256Digest,
    VaultLimits,
    VerifiedStagedFile,
)

from .vault_child import ChildProtocolError

MAX_INGEST_REPLY = 512 * 1024


def integer(value: object, *, maximum: int = 2**53 - 1, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ChildProtocolError()
    return value


def string(value: object, *, maximum: int = 200) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ChildProtocolError()
    return value


def parse_verified(document: dict[str, object], *, maximum: int) -> VerifiedStagedFile:
    if set(document) != {"byte_size", "sha256"}:
        raise ChildProtocolError()
    digest = string(document["sha256"], maximum=64)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ChildProtocolError()
    return VerifiedStagedFile(
        integer(document["byte_size"], minimum=1, maximum=maximum),
        Sha256Digest(bytes.fromhex(digest)),
    )


@dataclass(frozen=True, slots=True)
class IngestChildSettings:
    root: Path
    limits: VaultLimits = field(default_factory=VaultLimits)
    tool_timeout_seconds: float = 30.0
    tool_max_output_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        integer(self.tool_max_output_bytes, minimum=1024, maximum=256 * 1024)
        for value in asdict(self.limits).values():
            integer(value, minimum=1)
        if (
            not self.root.is_absolute()
            or not math.isfinite(self.tool_timeout_seconds)
            or not 0 < self.tool_timeout_seconds <= 300
            or not 1024 <= self.tool_max_output_bytes <= 256 * 1024
            or not 1 <= self.limits.max_object_bytes <= 4 * 1024**3
            or not 1 <= self.limits.io_block_bytes <= 1024 * 1024
        ):
            raise ValueError("ingest_child_configuration_invalid")

    def document(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "limits": asdict(self.limits),
            "tool_timeout_seconds": self.tool_timeout_seconds,
            "tool_max_output_bytes": self.tool_max_output_bytes,
        }

    @classmethod
    def parse(cls, document: dict[str, object]) -> IngestChildSettings:
        if set(document) != {"root", "limits", "tool_timeout_seconds", "tool_max_output_bytes"}:
            raise ChildProtocolError()
        limits = document["limits"]
        if not isinstance(limits, dict) or set(limits) != {
            "max_object_bytes",
            "max_chunk_bytes",
            "max_chunks",
            "io_block_bytes",
        }:
            raise ChildProtocolError()
        timeout = document["tool_timeout_seconds"]
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise ChildProtocolError()
        return cls(
            Path(string(document["root"], maximum=4096)),
            VaultLimits(**{key: integer(value, minimum=1) for key, value in limits.items()}),
            float(timeout),
            integer(document["tool_max_output_bytes"]),
        )


def analysis_document(
    metadata: AudioTechnicalMetadata, evidence: ChromaprintEvidence
) -> dict[str, object]:
    return {
        "metadata": asdict(metadata),
        "evidence": {
            "algorithm": evidence.algorithm,
            "algorithm_version": evidence.algorithm_version,
            "duration_ms": evidence.duration_ms,
            "payload": base64.b64encode(evidence.payload).decode("ascii"),
        },
    }


def parse_analysis(
    document: dict[str, object],
) -> tuple[AudioTechnicalMetadata, ChromaprintEvidence]:
    if set(document) != {"metadata", "evidence"}:
        raise ChildProtocolError()
    metadata, evidence = document["metadata"], document["evidence"]
    if (
        not isinstance(metadata, dict)
        or set(metadata)
        != {
            "codec",
            "container",
            "sample_rate_hz",
            "channels",
            "duration_ms",
            "bitrate_bps",
            "bit_depth",
        }
        or not isinstance(evidence, dict)
        or set(evidence) != {"algorithm", "algorithm_version", "duration_ms", "payload"}
    ):
        raise ChildProtocolError()
    try:
        payload = base64.b64decode(
            string(evidence["payload"], maximum=MAX_INGEST_REPLY), validate=True
        )
    except (ValueError, binascii.Error) as error:
        raise ChildProtocolError() from error
    if not 1 <= len(payload) <= 256 * 1024 or evidence["algorithm"] != "chromaprint":
        raise ChildProtocolError()
    return (
        AudioTechnicalMetadata(
            string(metadata["codec"], maximum=100),
            string(metadata["container"], maximum=100),
            integer(metadata["sample_rate_hz"], minimum=1),
            integer(metadata["channels"], minimum=1, maximum=64),
            integer(metadata["duration_ms"], minimum=1),
            None
            if metadata["bitrate_bps"] is None
            else integer(metadata["bitrate_bps"], minimum=1),
            None if metadata["bit_depth"] is None else integer(metadata["bit_depth"], minimum=1),
        ),
        ChromaprintEvidence(
            "chromaprint",
            string(evidence["algorithm_version"], maximum=100),
            integer(evidence["duration_ms"], minimum=1),
            payload,
        ),
    )
