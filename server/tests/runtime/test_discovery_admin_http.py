from __future__ import annotations

import re
from typing import cast
from uuid import UUID, uuid4

from autplay.adapters.postgresql.discovery_runtime import BulkPreviewResult, BulkStartResult
from autplay.adapters.postgresql.import_runtime import ImportCollectionArtist, ImportStartResult
from autplay.domain.auth import AccountRole
from autplay.domain.discovery import (
    BulkArtistResolution,
    DiscoveryCandidate,
    ProviderArtist,
    ProviderArtistTracks,
)
from autplay.domain.web_admin import AuthenticatedWebSession, WebActor, WebAdminError
from autplay.entrypoints.admin_web_http import WebAdminHttp
from autplay.entrypoints.discovery_admin_http import (
    BulkDiscoveryHttp,
    CollectionImportHttp,
    ManualDiscoveryHttp,
    create_discovery_admin_router,
)
from autplay.web.renderer import AdminTemplateRenderer
from fastapi import FastAPI
from starlette.testclient import TestClient


class _Web:
    def __init__(self) -> None:
        self.actor = WebActor(uuid4(), uuid4(), uuid4(), AccountRole.OWNER, 0)

    def authenticate_safe_get(
        self, bearer: bytes, *, head: bool = False
    ) -> AuthenticatedWebSession:
        del head
        return self.authenticate(bearer, mutation=False)

    def authenticate(self, bearer: bytes, *, mutation: bool) -> AuthenticatedWebSession:
        del mutation
        if bearer != b"session":
            raise WebAdminError("authentication_required")
        return AuthenticatedWebSession(self.actor, b"c" * 32)

    def validate_csrf(self, actor: WebActor, csrf: bytes, operation_id: UUID) -> None:
        if actor != self.actor or csrf != b"c" * 32 or not isinstance(operation_id, UUID):
            raise WebAdminError("csrf_invalid")


class _Discovery:
    def __init__(self) -> None:
        self.searches: list[tuple[UUID, str]] = []
        self.lookups: list[str] = []
        self.artist_resolutions: list[tuple[tuple[str, int], ...]] = []
        self.track_previews: list[tuple[str, ...]] = []

    def search(
        self, owner_id: UUID, query: str, *, limit: int = 20
    ) -> tuple[DiscoveryCandidate, ...]:
        assert limit == 20
        self.searches.append((owner_id, query))
        return (
            DiscoveryCandidate(
                "10",
                "20",
                "Morning Light",
                "Open Artist",
                "Open Album",
                209,
                "https://creativecommons.org/licenses/by-nc-sa/4.0/",
                "https://www.jamendo.com/track/10",
                True,
                "https://prod-1.storage.jamendo.com/download/track/10/mp32/",
            ),
        )

    def lookup_for_acquisition(self, provider_track_id: str) -> DiscoveryCandidate:
        self.lookups.append(provider_track_id)
        return DiscoveryCandidate(
            provider_track_id,
            "20",
            "Morning Light",
            "Open Artist",
            "Open Album",
            209,
            "https://creativecommons.org/licenses/by-nc-sa/4.0/",
            "https://www.jamendo.com/track/10",
            True,
            "https://prod-1.storage.jamendo.com/download/track/10/mp32/",
        )

    def resolve_artists(
        self,
        owner_id: UUID,
        artists: tuple[tuple[str, int], ...],
    ) -> tuple[BulkArtistResolution, ...]:
        del owner_id
        self.artist_resolutions.append(artists)
        return tuple(
            BulkArtistResolution(
                name,
                count,
                "EXACT_MATCH",
                ProviderArtist(
                    "20" if name == "Open Artist" else "21",
                    name,
                    f"https://www.jamendo.com/artist/{20 if name == 'Open Artist' else 21}",
                ),
            )
            for name, count in artists
        )

    def preview_artist_tracks(
        self,
        owner_id: UUID,
        artists: tuple[ProviderArtist, ...],
        *,
        max_tracks_per_artist: int = 25,
        max_tracks_total: int = 200,
    ) -> tuple[ProviderArtistTracks, ...]:
        del owner_id
        assert max_tracks_per_artist == 25 and max_tracks_total == 200
        self.track_previews.append(tuple(artist.provider_artist_id for artist in artists))
        return (
            ProviderArtistTracks(
                artists[0].provider_artist_id,
                3,
                (
                    DiscoveryCandidate(
                        "10",
                        artists[0].provider_artist_id,
                        "Popular One",
                        artists[0].name,
                        None,
                        180,
                        "https://creativecommons.org/licenses/by/4.0/",
                        "https://www.jamendo.com/track/10",
                        True,
                        "https://prod-1.storage.jamendo.com/download/track/10/mp32/",
                    ),
                    DiscoveryCandidate(
                        "11",
                        artists[0].provider_artist_id,
                        "Popular Two",
                        artists[0].name,
                        None,
                        181,
                        "https://creativecommons.org/licenses/by/4.0/",
                        "https://www.jamendo.com/track/11",
                        False,
                    ),
                ),
            ),
        )


class _Imports:
    def __init__(self) -> None:
        self.import_job_id = uuid4()
        self.payloads: list[bytes] = []

    def start_for_web(
        self,
        actor: WebActor,
        *,
        payload: bytes,
        format_name: str = "TXT",
        schema_version: str = "1",
    ) -> ImportStartResult:
        del actor
        assert format_name == "TXT" and schema_version == "1"
        self.payloads.append(payload)
        return ImportStartResult(self.import_job_id, uuid4(), False)

    def collection_artists(
        self,
        actor: WebActor,
        import_job_id: UUID,
        *,
        limit: int = 100,
    ) -> tuple[ImportCollectionArtist, ...]:
        del actor
        assert import_job_id == self.import_job_id and limit == 100
        return (
            ImportCollectionArtist("Open Artist", 3),
            ImportCollectionArtist("Another Artist", 1),
        )


class _Bulk:
    def __init__(self) -> None:
        self.bulk_operation_id = uuid4()
        self.previews: list[tuple[UUID, UUID]] = []
        self.starts: list[tuple[UUID, UUID, UUID]] = []
        self.search_starts: list[tuple[UUID, UUID, str]] = []

    def save_preview(
        self,
        actor: WebActor,
        *,
        import_job_id: UUID,
        operation_id: UUID,
        resolutions: tuple[BulkArtistResolution, ...],
        pages: tuple[ProviderArtistTracks, ...],
    ) -> BulkPreviewResult:
        del actor
        assert resolutions and pages
        self.previews.append((import_job_id, operation_id))
        return BulkPreviewResult(self.bulk_operation_id, 2, 1, False)

    def start(
        self,
        actor: WebActor,
        *,
        bulk_operation_id: UUID,
        operation_id: UUID,
    ) -> BulkStartResult:
        assert bulk_operation_id == self.bulk_operation_id
        self.starts.append((actor.user_id, bulk_operation_id, operation_id))
        return BulkStartResult(bulk_operation_id, "QUEUED", 1, 0, 1, False)

    def start_search_acquisition(
        self,
        actor: WebActor,
        *,
        operation_id: UUID,
        evidence: DiscoveryCandidate,
    ) -> BulkStartResult:
        self.search_starts.append((actor.user_id, operation_id, evidence.provider_track_id))
        return BulkStartResult(self.bulk_operation_id, "QUEUED", 1, 0, 0, False)

    def status(self, actor: WebActor, *, bulk_operation_id: UUID) -> BulkStartResult:
        assert actor.user_id and bulk_operation_id == self.bulk_operation_id
        return BulkStartResult(bulk_operation_id, "RUNNING", 1, 0, 0, False)


def _client(
    *, with_imports: bool = False, with_bulk: bool = False
) -> tuple[TestClient, _Web, _Discovery, _Imports, _Bulk]:
    web = _Web()
    discovery = _Discovery()
    imports = _Imports()
    bulk = _Bulk()
    app = FastAPI()
    app.include_router(
        create_discovery_admin_router(
            web=cast(WebAdminHttp, web),
            discovery=cast(ManualDiscoveryHttp, discovery),
            imports=cast(CollectionImportHttp, imports) if with_imports else None,
            bulk=cast(BulkDiscoveryHttp, bulk) if with_bulk else None,
            renderer=AdminTemplateRenderer(),
            origin="https://admin.test",
            token_secret=b"s" * 32,
        )
    )
    client = TestClient(app, base_url="https://admin.test")
    client.cookies.set("__Host-autplay_admin", "session")
    return client, web, discovery, imports, bulk


def _fields(html: str, action: str) -> dict[str, str]:
    match = re.search(
        rf'<form method="post" action="{re.escape(action)}"[^>]*>(.*?)</form>',
        html,
        re.DOTALL,
    )
    assert match is not None
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', match.group(1)))


def test_authenticated_manual_search_and_acquisition_are_separate() -> None:
    client, web, discovery, _, bulk = _client(with_bulk=True)
    page = client.get("/admin/discovery?lang=en")
    search_form = _fields(page.text, "/admin/discovery/search")
    search_form["query"] = "Open Artist Morning Light"

    search = client.post(
        "/admin/discovery/search?lang=en",
        data=search_form,
        headers={"Origin": "https://admin.test"},
    )

    assert search.status_code == 200
    assert "Morning Light" in search.text
    assert discovery.searches == [(web.actor.user_id, "Open Artist Morning Light")]
    assert discovery.lookups == []

    acquire_form = _fields(search.text, "/admin/discovery/acquire")
    acquire = client.post(
        "/admin/discovery/acquire?lang=en",
        data=acquire_form,
        headers={"Origin": "https://admin.test"},
    )

    assert acquire.status_code == 200
    assert "queued for verified Vault acquisition" in acquire.text
    assert discovery.lookups == ["10"]
    assert bulk.search_starts[0][0::2] == (web.actor.user_id, "10")
    status = client.get(f"/admin/discovery/operations/{bulk.bulk_operation_id}?lang=en")
    assert status.status_code == 200
    assert "RUNNING" in status.text
    assert "Refresh operation status" in status.text


def test_selection_token_is_owner_bound_and_origin_is_exact() -> None:
    client, web, discovery, _, bulk = _client(with_bulk=True)
    page = client.get("/admin/discovery")
    search_form = _fields(page.text, "/admin/discovery/search")
    search_form["query"] = "Morning Light"
    wrong_origin = client.post(
        "/admin/discovery/search",
        data=search_form,
        headers={"Origin": "https://wrong.test"},
    )
    assert wrong_origin.status_code == 403 and discovery.searches == []

    search = client.post(
        "/admin/discovery/search",
        data=search_form,
        headers={"Origin": "https://admin.test"},
    )
    acquire_form = _fields(search.text, "/admin/discovery/acquire")
    web.actor = WebActor(
        web.actor.server_instance_id,
        uuid4(),
        web.actor.web_session_id,
        web.actor.role,
        0,
    )
    rejected = client.post(
        "/admin/discovery/acquire",
        data=acquire_form,
        headers={"Origin": "https://admin.test"},
    )

    assert rejected.status_code == 403
    assert discovery.lookups == []
    assert bulk.search_starts == []


def test_txt_upload_is_bounded_authenticated_and_renders_sorted_artist_selection() -> None:
    client, _, _, imports, _ = _client(with_imports=True)
    page = client.get("/admin/discovery?lang=en")
    import_form = _fields(page.text, "/admin/discovery/import")

    response = client.post(
        "/admin/discovery/import?lang=en",
        data=import_form,
        files={"collection": ("collection.txt", b"Open Artist - Song\n", "text/plain")},
        headers={"Origin": "https://admin.test"},
    )

    assert response.status_code == 200
    assert imports.payloads == [b"Open Artist - Song\n"]
    assert response.text.index("Open Artist") < response.text.index("Another Artist")
    assert 'name="artist" value="Open Artist"' in response.text


def test_txt_upload_rejects_wrong_extension_before_import() -> None:
    client, _, _, imports, _ = _client(with_imports=True)
    page = client.get("/admin/discovery")
    form = _fields(page.text, "/admin/discovery/import")

    rejected = client.post(
        "/admin/discovery/import",
        data=form,
        files={"collection": ("collection.exe", b"Open Artist - Song\n", "text/plain")},
        headers={"Origin": "https://admin.test"},
    )

    assert rejected.status_code == 403
    assert imports.payloads == []


def test_bulk_preview_requeries_owner_collection_and_resolves_provider_ids() -> None:
    client, web, discovery, imports, bulk = _client(with_imports=True, with_bulk=True)
    page = client.get("/admin/discovery")
    import_form = _fields(page.text, "/admin/discovery/import")
    imported = client.post(
        "/admin/discovery/import?lang=en",
        data=import_form,
        files={"collection": ("collection.txt", b"Open Artist - Song\n", "text/plain")},
        headers={"Origin": "https://admin.test"},
    )
    preview_form = _fields(imported.text, "/admin/discovery/bulk-preview")
    preview_form["artist"] = "Open Artist"

    preview = client.post(
        "/admin/discovery/bulk-preview?lang=en",
        data=preview_form,
        headers={"Origin": "https://admin.test"},
    )

    assert preview.status_code == 200
    assert discovery.artist_resolutions == [(("Open Artist", 3),)]
    assert discovery.track_previews == [("20",)]
    assert "Provider identity check" in preview.text
    assert "Popular One" in preview.text and "Popular Two" in preview.text
    assert "Start expansion" in preview.text
    assert str(imports.import_job_id) in preview.text
    assert bulk.previews and bulk.previews[0][0] == imports.import_job_id

    start_form = _fields(preview.text, "/admin/discovery/bulk-start")
    started = client.post(
        "/admin/discovery/bulk-start?lang=en",
        data=start_form,
        headers={"Origin": "https://admin.test"},
    )

    assert started.status_code == 200
    assert "Expansion queued" in started.text
    assert bulk.starts[0][0:2] == (web.actor.user_id, bulk.bulk_operation_id)


def test_bulk_preview_rejects_artist_not_in_owner_collection() -> None:
    client, _, discovery, _, _ = _client(with_imports=True)
    page = client.get("/admin/discovery")
    import_form = _fields(page.text, "/admin/discovery/import")
    imported = client.post(
        "/admin/discovery/import",
        data=import_form,
        files={"collection": ("collection.txt", b"Open Artist - Song\n", "text/plain")},
        headers={"Origin": "https://admin.test"},
    )
    form = _fields(imported.text, "/admin/discovery/bulk-preview")
    form["artist"] = "Injected Artist"

    rejected = client.post(
        "/admin/discovery/bulk-preview",
        data=form,
        headers={"Origin": "https://admin.test"},
    )

    assert rejected.status_code == 403
    assert discovery.artist_resolutions == []


def test_bulk_start_token_is_owner_bound() -> None:
    client, web, _, _, bulk = _client(with_imports=True, with_bulk=True)
    page = client.get("/admin/discovery")
    import_form = _fields(page.text, "/admin/discovery/import")
    imported = client.post(
        "/admin/discovery/import",
        data=import_form,
        files={"collection": ("collection.txt", b"Open Artist - Song\n", "text/plain")},
        headers={"Origin": "https://admin.test"},
    )
    preview_form = _fields(imported.text, "/admin/discovery/bulk-preview")
    preview_form["artist"] = "Open Artist"
    preview = client.post(
        "/admin/discovery/bulk-preview",
        data=preview_form,
        headers={"Origin": "https://admin.test"},
    )
    start_form = _fields(preview.text, "/admin/discovery/bulk-start")
    web.actor = WebActor(
        web.actor.server_instance_id,
        uuid4(),
        web.actor.web_session_id,
        web.actor.role,
        0,
    )

    rejected = client.post(
        "/admin/discovery/bulk-start",
        data=start_form,
        headers={"Origin": "https://admin.test"},
    )

    assert rejected.status_code == 403
    assert bulk.starts == []
