"""Bounded locators and private bytes for persisted native source replay.

These values carry no quality eligibility or approval. In particular, constructing
a proof in memory cannot substitute for re-reading its PostgreSQL authority.
"""

from dataclasses import dataclass
from uuid import UUID

MAX_NATIVE_SOURCE_REQUESTS = 64
MAX_NATIVE_SOURCE_BYTES = 32 * 1024 * 1024
MAX_NATIVE_SOURCE_RECORD_BYTES = 2 * 1024 * 1024
MAX_NATIVE_SOURCE_OUTCOMES = 5000
NATIVE_LABEL_WINDOW_MS = 7 * 86_400_000
NATIVE_RETAINED_SOURCE_KIND = "SONA_RETAINED_NATIVE_SOURCE_V1"


@dataclass(frozen=True, slots=True)
class NativeSourceSelection:
    """Externally pinned membership; never recovered from an untrusted proof."""

    request_ids: tuple[UUID, ...]
    artifact_generations: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if (
            not 1 <= len(self.request_ids) <= MAX_NATIVE_SOURCE_REQUESTS
            or any(not isinstance(value, UUID) for value in self.request_ids)
            or self.request_ids != tuple(sorted(set(self.request_ids)))
        ):
            raise ValueError("NATIVE_SOURCE_REQUEST_MEMBERSHIP_INVALID")
        if (
            not 1 <= len(self.artifact_generations) <= 32
            or self.artifact_generations != tuple(sorted(self.artifact_generations))
            or len({key for key, _ in self.artifact_generations}) != len(self.artifact_generations)
            or any(
                len(key) != 64
                or any(char not in "0123456789abcdef" for char in key)
                or type(generation) is not int
                or generation < 1
                for key, generation in self.artifact_generations
            )
        ):
            raise ValueError("NATIVE_SOURCE_LICENSE_PINS_INVALID")


@dataclass(frozen=True, slots=True)
class NativeSourceProof:
    """Private retained-source evidence, never a portable authorization token."""

    document: bytes
    sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.document, bytes)
            or not 1 <= len(self.document) <= MAX_NATIVE_SOURCE_BYTES
        ):
            raise ValueError("NATIVE_SOURCE_PROOF_BOUND")
