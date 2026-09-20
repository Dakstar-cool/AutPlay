"""Bounded metadata messages; private download addresses never cross the pipe."""

from dataclasses import asdict

from autplay.domain.discovery import DiscoveryCandidate, DiscoveryEvidence
from autplay.domain.vault import Sha256Digest, VerifiedStagedFile

from .vault_child import ChildProtocolError, decode_document, encode_document

MAX_DISCOVERY_RESULT_BYTES = 64 * 1024
DISCOVERY_CHILD_ERRORS = frozenset(
    {
        "discovery_not_eligible",
        "discovery_target_not_found",
        "discovery_content_invalid",
        "discovery_response_too_large",
        "discovery_acquisition_failed",
        "discovery_adapter_unavailable",
        "discovery_provider_response_invalid",
    }
)


def metadata(candidate: DiscoveryCandidate) -> DiscoveryEvidence:
    return DiscoveryEvidence(
        candidate.provider_track_id,
        candidate.provider_artist_id,
        candidate.title,
        candidate.artist,
        candidate.album,
        candidate.duration_seconds,
        candidate.license_url,
        candidate.share_url,
        candidate.acquisition_allowed,
    )


def encode_result(verified: VerifiedStagedFile, evidence: DiscoveryEvidence) -> bytes:
    return encode_document(
        {
            "byte_size": verified.byte_size,
            "sha256": verified.sha256.hex,
            "evidence": asdict(evidence),
        },
        maximum=MAX_DISCOVERY_RESULT_BYTES,
    )


def decode_result(payload: bytes, *, maximum: int) -> tuple[VerifiedStagedFile, DiscoveryEvidence]:
    document = decode_document(payload, maximum=MAX_DISCOVERY_RESULT_BYTES)
    if set(document) != {"byte_size", "sha256", "evidence"}:
        raise ChildProtocolError()
    size, digest, raw = document["byte_size"], document["sha256"], document["evidence"]
    if (
        type(size) is not int
        or not 1 <= size <= maximum
        or not isinstance(digest, str)
        or len(digest) != 64
        or not isinstance(raw, dict)
    ):
        raise ChildProtocolError()
    names = {
        "provider_track_id",
        "provider_artist_id",
        "title",
        "artist",
        "album",
        "duration_seconds",
        "license_url",
        "share_url",
        "acquisition_allowed",
    }
    if set(raw) != names:
        raise ChildProtocolError()
    for name in (
        "provider_track_id",
        "provider_artist_id",
        "title",
        "artist",
        "license_url",
        "share_url",
    ):
        if not isinstance(raw[name], str) or len(raw[name]) > 2000:
            raise ChildProtocolError()
    if (
        (raw["album"] is not None and not isinstance(raw["album"], str))
        or type(raw["duration_seconds"]) is not int
        or raw["acquisition_allowed"] is not True
    ):
        raise ChildProtocolError()
    try:
        evidence = DiscoveryEvidence(
            raw["provider_track_id"],
            raw["provider_artist_id"],
            raw["title"],
            raw["artist"],
            raw["album"],
            raw["duration_seconds"],
            raw["license_url"],
            raw["share_url"],
            True,
        )
        sha256 = Sha256Digest(bytes.fromhex(digest))
    except (ValueError, TypeError) as error:
        raise ChildProtocolError() from error
    if sha256.hex != digest:
        raise ChildProtocolError()
    return VerifiedStagedFile(size, sha256), evidence
