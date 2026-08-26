from __future__ import annotations

import json
import traceback
from datetime import date
from pathlib import Path
from urllib.error import URLError
from uuid import UUID, uuid4

import pytest
from autplay.adapters import jamendo
from autplay.application.manual_discovery import ManualDiscoveryService
from autplay.domain.discovery import (
    DiscoveryCandidate,
    DiscoveryError,
    ProviderArtist,
    ProviderArtistTracks,
    ProviderTrackObservation,
    ProviderTrackPage,
)


def _candidate(*, allowed: bool = True) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        provider_track_id="10",
        provider_artist_id="20",
        title="Morning Light",
        artist="Open Artist",
        album="Open Album",
        duration_seconds=209,
        license_url="https://creativecommons.org/licenses/by-nc-sa/4.0/",
        share_url="https://www.jamendo.com/track/10",
        acquisition_allowed=allowed,
        download_url=(
            "https://prod-1.storage.jamendo.com/download/track/10/mp32/" if allowed else None
        ),
    )


def _response(*, allowed: bool, download_url: str = "") -> bytes:
    return json.dumps(
        {
            "headers": {"status": "success", "code": 0},
            "results": [
                {
                    "id": "10",
                    "name": "Morning Light",
                    "artist_name": "Open Artist",
                    "artist_id": "20",
                    "album_name": "Open Album",
                    "duration": "209",
                    "license_ccurl": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
                    "shareurl": "https://www.jamendo.com/track/10",
                    "audiodownload_allowed": allowed,
                    "audiodownload": download_url,
                }
            ],
        }
    ).encode()


def test_parser_keeps_discovery_but_separates_playable_permission() -> None:
    metadata_only = jamendo._parse_candidates(_response(allowed=False), limit=1)
    playable = jamendo._parse_candidates(
        _response(
            allowed=True,
            download_url="https://prod-1.storage.jamendo.com/download/track/10/mp32/",
        ),
        limit=1,
    )

    assert metadata_only[0].acquisition_allowed is False
    assert metadata_only[0].download_url is None
    assert playable[0].acquisition_allowed is True


def test_parser_rejects_non_allowlisted_download_origin() -> None:
    with pytest.raises(DiscoveryError, match="discovery_provider_response_invalid"):
        jamendo._parse_candidates(
            _response(allowed=True, download_url="https://attacker.example/track.mp3"),
            limit=1,
        )


def test_provider_failure_traceback_never_contains_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_id = "private-client-id"
    provider = jamendo.JamendoProvider(client_id)

    class _FailingOpener:
        def open(self, request: object, *, timeout: float) -> object:
            del request, timeout
            raise URLError(f"https://api.jamendo.com/?client_id={client_id}")

    monkeypatch.setattr(provider, "_opener", _FailingOpener())
    with pytest.raises(DiscoveryError) as exc_info:
        provider.search("Open Artist", limit=1)

    rendered = "".join(traceback.format_exception(exc_info.value))
    assert client_id not in rendered


class _Provider:
    def __init__(self, candidate: DiscoveryCandidate) -> None:
        self.candidate = candidate
        self.search_calls: list[str] = []
        self.lookup_calls: list[str] = []
        self.acquire_calls: list[str] = []
        self.artist_search_calls: list[str] = []
        self.top_track_calls: list[str] = []

    def search(self, query: str, *, limit: int) -> tuple[DiscoveryCandidate, ...]:
        assert limit == 20
        self.search_calls.append(query)
        return (self.candidate,)

    def lookup(self, provider_track_id: str) -> DiscoveryCandidate:
        self.lookup_calls.append(provider_track_id)
        return self.candidate

    def search_artists(self, name: str, *, limit: int) -> tuple[ProviderArtist, ...]:
        assert limit == 3
        self.artist_search_calls.append(name)
        if name == "Missing Artist":
            return ()
        if name == "Ambiguous Artist":
            return (
                ProviderArtist("20", name, "https://www.jamendo.com/artist/20"),
                ProviderArtist("21", name, "https://www.jamendo.com/artist/21"),
            )
        return (ProviderArtist("20", f"  {name.upper()}  ", "https://www.jamendo.com/artist/20"),)

    def top_tracks(self, provider_artist_id: str, *, limit: int) -> ProviderArtistTracks:
        self.top_track_calls.append(provider_artist_id)
        tracks = tuple(
            DiscoveryCandidate(
                provider_track_id=str(index),
                provider_artist_id=provider_artist_id,
                title=f"Track {index}",
                artist="Open Artist",
                album=None,
                duration_seconds=180,
                license_url="https://creativecommons.org/licenses/by/4.0/",
                share_url=f"https://www.jamendo.com/track/{index}",
                acquisition_allowed=False,
            )
            for index in range(1, min(limit, 5) + 1)
        )
        return ProviderArtistTracks(provider_artist_id, 9, tracks)

    def acquire(
        self,
        candidate: DiscoveryCandidate,
        destination: Path,
        *,
        max_bytes: int,
    ) -> int:
        assert max_bytes == 8_192
        self.acquire_calls.append(candidate.provider_track_id)
        payload = b"ID3" + (b"a" * 4_096)
        destination.write_bytes(payload)
        return len(payload)


def _service(tmp_path: Path, provider: _Provider) -> ManualDiscoveryService:
    current = [0.0]

    def sleep(seconds: float) -> None:
        current[0] += seconds

    return ManualDiscoveryService(
        provider,
        staging_root=tmp_path,
        max_download_bytes=8_192,
        minimum_request_interval_seconds=1,
        monotonic_clock=lambda: current[0],
        sleeper=sleep,
    )


def test_request_gate_cannot_be_configured_below_one_second(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="discovery request interval is invalid"):
        ManualDiscoveryService(
            _Provider(_candidate()),
            staging_root=tmp_path,
            max_download_bytes=8_192,
            minimum_request_interval_seconds=0.99,
        )


def test_manual_acquisition_revalidates_and_is_filesystem_idempotent(tmp_path: Path) -> None:
    provider = _Provider(_candidate())
    service = _service(tmp_path, provider)
    owner_id = uuid4()
    operation_id = uuid4()

    first = service.acquire(owner_id, "10", operation_id=operation_id)
    repeated = service.acquire(owner_id, "10", operation_id=operation_id)

    assert provider.lookup_calls == ["10"]
    assert provider.acquire_calls == ["10"]
    assert first.duplicate is False and repeated.duplicate is True
    operation_root = tmp_path / str(owner_id) / "jamendo" / str(operation_id)
    evidence = json.loads((operation_root / first.attribution_name).read_text(encoding="utf-8"))
    assert evidence["acquisition_state"] == "STAGED_NOT_READY"
    assert evidence["provider_track_id"] == "10"
    assert (operation_root / first.audio_name).stat().st_size == first.byte_count


def test_manual_acquisition_blocks_revoked_permission_without_writing(tmp_path: Path) -> None:
    provider = _Provider(_candidate(allowed=False))
    service = _service(tmp_path, provider)

    with pytest.raises(DiscoveryError, match="discovery_not_eligible"):
        service.acquire(uuid4(), "10", operation_id=uuid4())

    assert provider.acquire_calls == []
    assert tuple(tmp_path.rglob("*.mp3")) == ()


def test_same_operation_cannot_be_replayed_for_another_provider_track(tmp_path: Path) -> None:
    provider = _Provider(_candidate())
    service = _service(tmp_path, provider)
    owner_id = uuid4()
    operation_id = uuid4()
    service.acquire(owner_id, "10", operation_id=operation_id)

    with pytest.raises(DiscoveryError, match="discovery_operation_conflict"):
        service.acquire(owner_id, "11", operation_id=operation_id)


def test_owner_scope_is_part_of_staging_location(tmp_path: Path) -> None:
    provider = _Provider(_candidate())
    service = _service(tmp_path, provider)
    operation_id = uuid4()
    first_owner = UUID("00000000-0000-0000-0000-000000000001")
    second_owner = UUID("00000000-0000-0000-0000-000000000002")

    first = service.acquire(first_owner, "10", operation_id=operation_id)
    second = service.acquire(second_owner, "10", operation_id=operation_id)

    assert first.duplicate is second.duplicate is False
    assert provider.acquire_calls == ["10", "10"]


def test_bulk_artist_resolution_requires_one_exact_normalized_identity(tmp_path: Path) -> None:
    provider = _Provider(_candidate())
    service = _service(tmp_path, provider)

    resolutions = service.resolve_artists(
        uuid4(),
        (("Open Artist", 3), ("Missing Artist", 2), ("Ambiguous Artist", 1)),
    )

    assert [item.state for item in resolutions] == ["EXACT_MATCH", "NOT_FOUND", "AMBIGUOUS"]
    assert resolutions[0].provider_artist is not None
    assert resolutions[0].provider_artist.provider_artist_id == "20"


def test_bulk_preview_keeps_ceil_half_within_per_artist_cap(tmp_path: Path) -> None:
    provider = _Provider(_candidate())
    service = _service(tmp_path, provider)

    pages = service.preview_artist_tracks(
        uuid4(),
        (ProviderArtist("20", "Open Artist", "https://www.jamendo.com/artist/20"),),
    )

    assert pages[0].total_count == 9
    assert [track.provider_track_id for track in pages[0].tracks] == ["1", "2", "3", "4", "5"]


def test_top_track_parser_requires_bounded_full_count(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = jamendo.JamendoProvider("test-client")
    payload = json.loads(_response(allowed=False))
    payload["headers"]["results_fullcount"] = "7"
    monkeypatch.setattr(
        provider, "_request_json", lambda url, parameters: json.dumps(payload).encode()
    )

    page = provider.top_tracks("20", limit=5)

    assert page.total_count == 7
    assert page.tracks[0].provider_artist_id == "20"


def _release_page(*, count: int = 25, artist_id: str = "20") -> bytes:
    results = []
    for index in range(count):
        track_id = str(1_000 - index)
        item = json.loads(
            _response(
                allowed=False,
            )
        )["results"][0]
        item.update(
            {
                "id": track_id,
                "artist_id": artist_id,
                "releasedate": f"2026-08-{25 - index:02d}",
                "shareurl": f"https://www.jamendo.com/track/{track_id}",
            }
        )
        results.append(item)
    return json.dumps({"headers": {"status": "success", "code": 0}, "results": results}).encode()


@pytest.mark.parametrize("offset", [0, 25])
def test_release_tracks_uses_exact_bounded_provider_page(
    monkeypatch: pytest.MonkeyPatch, offset: int
) -> None:
    provider = jamendo.JamendoProvider("test-client")
    calls: list[tuple[str, dict[str, str]]] = []

    def request(url: str, parameters: dict[str, str]) -> bytes:
        calls.append((url, parameters))
        return _release_page(count=25 if offset == 0 else 1)

    monkeypatch.setattr(provider, "_request_json", request)

    page = provider.release_tracks("20", offset=offset)

    assert calls == [
        (
            jamendo.JAMENDO_TRACKS_URL,
            {
                "format": "json",
                "artist_id": "20",
                "offset": str(offset),
                "limit": "25",
                "order": "releasedate_desc id_desc",
                "include": "licenses",
                "audiodlformat": "mp32",
                "type": "single albumtrack",
            },
        )
    ]
    assert len(page.observations) == (25 if offset == 0 else 1)
    assert page.next_offset == (25 if offset == 0 else None)
    assert page.checkpoint is not None
    assert "test-client" not in page.checkpoint


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda results: results.__setitem__(0, {**results[0], "artist_id": "21"}), "artist"),
        (
            lambda results: results.__setitem__(1, {**results[1], "id": results[0]["id"]}),
            "duplicate",
        ),
        (
            lambda results: results.__setitem__(0, {**results[0], "releasedate": "not-a-date"}),
            "date",
        ),
        (lambda results: results.reverse(), "ordering"),
    ],
)
def test_release_tracks_rejects_invalid_provider_schema(mutate: object, expected: str) -> None:
    raw = json.loads(_release_page())
    action = mutate
    assert callable(action)
    action(raw["results"])

    with pytest.raises(DiscoveryError, match="provider_schema_invalid"):
        jamendo._parse_release_track_page(
            json.dumps(raw).encode(), provider_artist_id="20", offset=0
        )


def test_provider_track_page_keeps_typed_utc_release_evidence() -> None:
    observation = ProviderTrackObservation(_candidate(allowed=False), date(2026, 8, 25), "UTC")
    page = ProviderTrackPage("20", 25, (observation,), None, "v1:2026-08-25:10")

    assert page.observations[0].release_date == date(2026, 8, 25)
