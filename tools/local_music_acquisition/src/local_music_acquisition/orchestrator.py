"""Strict sequential provider orchestration for local TXT playlists."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import PlaylistItem, ProviderFailure, ProviderMiss
from .playlist import PlaylistParseError, normalize_numbered_collection, parse_playlist
from .providers.base import AcquisitionProvider

MAX_PLAYLIST_TRACKS = 500


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


def download_playlist(
    input_file: Path,
    output_directory: Path,
    *,
    providers: tuple[AcquisitionProvider, ...],
    rights_confirmed: frozenset[str] = frozenset(),
    normalize_numbered: bool = False,
) -> dict[str, object]:
    """Acquire each row in order; only a genuine miss permits the next contour."""

    if not providers:
        raise PlaylistDownloadError("providers_empty")
    names = [provider.name for provider in providers]
    if len(names) != len(set(names)):
        raise PlaylistDownloadError("provider_names_duplicate")
    for provider in providers:
        if provider.requires_rights_confirmation and provider.name not in rights_confirmed:
            raise PlaylistDownloadError(f"{provider.name}_rights_confirmation_required")

    items, malformed_count = _read_items(input_file, normalize_numbered=normalize_numbered)
    if sum(item.error_code is None for item in items) > MAX_PLAYLIST_TRACKS:
        raise PlaylistDownloadError("playlist_track_limit_exceeded")

    outcomes: list[TrackOutcome] = []
    provider_counts = {name: 0 for name in names}
    for item in items:
        if item.error_code is not None:
            outcomes.append(
                TrackOutcome(item.row_number, "invalid_input", None, False, item.error_code)
            )
            continue
        last_miss: ProviderMiss | None = None
        for index, provider in enumerate(providers):
            try:
                artifact = provider.acquire(item, output_directory)
            except ProviderMiss as error:
                if error.provider != provider.name or error.code != "exact_match_not_found":
                    outcomes.append(
                        TrackOutcome(
                            item.row_number,
                            "failed",
                            provider.name,
                            index > 0,
                            f"{provider.name}.result_invalid",
                        )
                    )
                    break
                last_miss = error
                continue
            except ProviderFailure as error:
                code = (
                    str(error)
                    if error.provider == provider.name
                    else f"{provider.name}.result_invalid"
                )
                outcomes.append(
                    TrackOutcome(item.row_number, "failed", provider.name, index > 0, code)
                )
                break
            except OSError:
                outcomes.append(
                    TrackOutcome(
                        item.row_number,
                        "failed",
                        provider.name,
                        index > 0,
                        f"{provider.name}.operational_failure",
                    )
                )
                break
            if artifact.provider != provider.name or re.fullmatch(
                r"sha256:[0-9a-f]{12}", artifact.artifact_ref
            ) is None:
                outcomes.append(
                    TrackOutcome(
                        item.row_number,
                        "failed",
                        provider.name,
                        index > 0,
                        f"{provider.name}.result_invalid",
                    )
                )
                break
            provider_counts[provider.name] += 1
            outcomes.append(
                TrackOutcome(
                    item.row_number,
                    "downloaded",
                    provider.name,
                    index > 0,
                    artifact_ref=artifact.artifact_ref,
                )
            )
            break
        else:
            assert last_miss is not None
            outcomes.append(
                TrackOutcome(
                    item.row_number,
                    "not_found",
                    last_miss.provider,
                    len(providers) > 1,
                    str(last_miss),
                )
            )

    downloaded = sum(outcome.status == "downloaded" for outcome in outcomes)
    summary: dict[str, object] = {
        "requested": len(outcomes),
        "downloaded": downloaded,
        "failed": len(outcomes) - downloaded,
        "malformed": malformed_count,
        "provider_downloaded": provider_counts,
        "outcomes": [asdict(outcome) for outcome in outcomes],
    }
    summary.update({f"{name}_downloaded": count for name, count in provider_counts.items()})
    return summary
