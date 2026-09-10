"""Bounded provider orchestration for local TXT playlists."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import AcquiredArtifact, PlaylistItem, ProviderFailure, ProviderMiss
from .playlist import PlaylistParseError, normalize_numbered_collection, parse_playlist
from .providers.base import AcquisitionProvider

MAX_PLAYLIST_TRACKS = 500
MAX_WORKERS = 4


class PlaylistDownloadError(RuntimeError):
    """Stable top-level pipeline error."""


@dataclass(frozen=True, slots=True)
class TrackOutcome:
    row_number: int
    status: str
    provider: str | None
    fallback_used: bool
    error_code: str | None = None
    artifact_ref: str | None = None
    identity_version: str | None = None


@dataclass(frozen=True, slots=True)
class _Attempt:
    status: str
    provider: str
    error_code: str | None = None
    artifact: AcquiredArtifact | None = None


@dataclass(slots=True)
class _ProviderLane:
    """Serialize one provider while allowing different providers to overlap."""

    provider: AcquisitionProvider
    failure_threshold: int | None
    lock: threading.Lock = field(default_factory=threading.Lock)
    consecutive_failures: int = 0
    terminal_failures: int = 0
    circuit_open: bool = False

    def invoke(self, item: PlaylistItem, output_directory: Path) -> _Attempt:
        with self.lock:
            if self.circuit_open:
                return _Attempt("failure", self.provider.name, f"{self.provider.name}.circuit_open")
            try:
                artifact = self.provider.acquire(item, output_directory)
            except ProviderMiss as error:
                if error.provider != self.provider.name or error.code not in {
                    "exact_match_not_found",
                    "ambiguous_match",
                }:
                    return self._failure("result_invalid")
                self.consecutive_failures = 0
                return _Attempt("miss", self.provider.name, str(error))
            except ProviderFailure as error:
                code = error.code if error.provider == self.provider.name else "result_invalid"
                return self._failure(code)
            except OSError:
                return self._failure("operational_failure")
            if (
                artifact.provider != self.provider.name
                or re.fullmatch(r"sha256:[0-9a-f]{12}", artifact.artifact_ref) is None
            ):
                return self._failure("result_invalid")
            self.consecutive_failures = 0
            return _Attempt("downloaded", self.provider.name, artifact=artifact)

    def _failure(self, code: str) -> _Attempt:
        self.consecutive_failures += 1
        self.terminal_failures += 1
        if (
            self.failure_threshold is not None
            and self.consecutive_failures >= self.failure_threshold
        ):
            self.circuit_open = True
        return _Attempt("failure", self.provider.name, f"{self.provider.name}.{code}")


def _read_items(input_file: Path, *, normalize_numbered: bool) -> tuple[list[PlaylistItem], int]:
    try:
        payload = input_file.read_bytes()
        if normalize_numbered:
            payload, _stats = normalize_numbered_collection(payload)
        parsed = parse_playlist(payload)
    except OSError as error:
        raise PlaylistDownloadError("playlist_file_unavailable") from error
    except PlaylistParseError as error:
        raise PlaylistDownloadError(str(error)) from error
    return list(parsed.rows), parsed.malformed_count


def _download_item(
    item: PlaylistItem,
    output_directory: Path,
    lanes: tuple[_ProviderLane, ...],
    *,
    continue_on_provider_failure: bool,
) -> TrackOutcome:
    if item.error_code is not None:
        return TrackOutcome(item.row_number, "invalid_input", None, False, item.error_code)

    last_miss: _Attempt | None = None
    last_failure: _Attempt | None = None
    for index, lane in enumerate(lanes):
        attempt = lane.invoke(item, output_directory)
        if attempt.status == "miss":
            last_miss = attempt
            continue
        if attempt.status == "failure":
            last_failure = attempt
            if continue_on_provider_failure and index + 1 < len(lanes):
                continue
            return TrackOutcome(
                item.row_number,
                "failed",
                attempt.provider,
                index > 0,
                attempt.error_code,
            )
        assert attempt.artifact is not None
        return TrackOutcome(
            item.row_number,
            "downloaded",
            attempt.provider,
            index > 0,
            artifact_ref=attempt.artifact.artifact_ref,
            identity_version=attempt.artifact.identity_version,
        )

    if last_failure is not None:
        return TrackOutcome(
            item.row_number,
            "failed",
            last_failure.provider,
            len(lanes) > 1,
            last_failure.error_code,
        )
    assert last_miss is not None
    return TrackOutcome(
        item.row_number,
        "not_found",
        last_miss.provider,
        len(lanes) > 1,
        last_miss.error_code,
    )


def download_playlist(
    input_file: Path,
    output_directory: Path,
    *,
    providers: tuple[AcquisitionProvider, ...],
    rights_confirmed: frozenset[str] = frozenset(),
    normalize_numbered: bool = False,
    max_workers: int = 1,
    continue_on_provider_failure: bool = False,
    provider_failure_threshold: int = 3,
) -> dict[str, object]:
    """Acquire rows with ordered fallback and optional provider-lane parallelism."""

    if not providers:
        raise PlaylistDownloadError("providers_empty")
    if not 1 <= max_workers <= MAX_WORKERS:
        raise PlaylistDownloadError("max_workers_invalid")
    if not 1 <= provider_failure_threshold <= 100:
        raise PlaylistDownloadError("provider_failure_threshold_invalid")
    names = [provider.name for provider in providers]
    if len(names) != len(set(names)):
        raise PlaylistDownloadError("provider_names_duplicate")
    for provider in providers:
        if provider.requires_rights_confirmation and provider.name not in rights_confirmed:
            raise PlaylistDownloadError(f"{provider.name}_rights_confirmation_required")

    items, malformed_count = _read_items(input_file, normalize_numbered=normalize_numbered)
    if sum(item.error_code is None for item in items) > MAX_PLAYLIST_TRACKS:
        raise PlaylistDownloadError("playlist_track_limit_exceeded")

    threshold = provider_failure_threshold if continue_on_provider_failure else None
    lanes = tuple(_ProviderLane(provider, threshold) for provider in providers)
    if max_workers == 1:
        outcomes = [
            _download_item(
                item,
                output_directory,
                lanes,
                continue_on_provider_failure=continue_on_provider_failure,
            )
            for item in items
        ]
    else:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="acquisition") as pool:
            outcomes = list(
                pool.map(
                    lambda item: _download_item(
                        item,
                        output_directory,
                        lanes,
                        continue_on_provider_failure=continue_on_provider_failure,
                    ),
                    items,
                )
            )

    provider_counts = {
        name: sum(
            outcome.status == "downloaded" and outcome.provider == name for outcome in outcomes
        )
        for name in names
    }
    downloaded = sum(outcome.status == "downloaded" for outcome in outcomes)
    summary: dict[str, object] = {
        "requested": len(outcomes),
        "downloaded": downloaded,
        "failed": len(outcomes) - downloaded,
        "malformed": malformed_count,
        "provider_downloaded": provider_counts,
        "provider_terminal_failures": {
            lane.provider.name: lane.terminal_failures for lane in lanes
        },
        "provider_circuit_open": {lane.provider.name: lane.circuit_open for lane in lanes},
        "max_workers": max_workers,
        "continue_on_provider_failure": continue_on_provider_failure,
        "outcomes": [asdict(outcome) for outcome in outcomes],
    }
    summary.update({f"{name}_downloaded": count for name, count in provider_counts.items()})
    return summary
