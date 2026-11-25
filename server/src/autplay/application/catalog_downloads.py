"""Resumable owner-scoped previews and selection of exact catalogue recording sources."""

import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.catalogue_context import PostgresCatalogueContextRepository
from autplay.adapters.postgresql.models import CatalogDownloadRow, UserTrackRefRow
from autplay.adapters.postgresql.models.catalogue_context import (
    InternetCatalogueContextRow,
    InternetSearchContextRow,
)
from autplay.adapters.postgresql.models.internet_music import InternetAcquisitionRow
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.adapters.postgresql.vault_track_match import same_track
from autplay.application.catalogue_context import CatalogueContextService, catalogue_track_ref
from autplay.application.internet_music import InternetMusicService
from autplay.application.music_discovery import MusicDiscoveryService
from autplay.application.music_library import MusicError
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.application.vault_uploads import VaultNotFoundError
from autplay.domain.auth import Principal
from autplay.domain.catalogue_context import CatalogueContextEntity, CatalogueTrackCard


def _items(page: dict[str, object]) -> list[dict[str, Any]]:
    items = page.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise MusicError("music_catalogue_unavailable", "Invalid catalogue page.", 503)
    return items


_PERFORMANCE = re.compile(
    r"\b(?:cover|кавер|лайв|концертная версия|концертное исполнение|с концерта)\b"  # noqa: RUF001
    r"|\blive(?:$|[,;:]|\s+(?:at|in|from|version|performance|recording|session|on|acoustic|unplugged|\d{2,4})\b|\s+@)",
    re.I,
)
_VERSION_NOTE = re.compile(r"[\[(]([^\])]+)[\])]|[ \t]+[-\u2013\u2014][ \t]+(.+)$")


def _studio_recording(card: dict[str, Any]) -> bool:
    if card.get("performance_types") or _PERFORMANCE.search(card.get("disambiguation") or ""):
        return False
    title = card.get("recording_title") or card["title"]
    return not any(
        _PERFORMANCE.search(match[1] or match[2]) for match in _VERSION_NOTE.finditer(title)
    )


class CatalogDownloads:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        discovery: MusicDiscoveryService,
        contexts: CatalogueContextService,
        sources: InternetMusicService,
    ) -> None:
        self.sessions, self.discovery, self.contexts, self.sources = (
            sessions,
            discovery,
            contexts,
            sources,
        )

    def preview(
        self, actor: Principal, operation: UUID, kind: str, target: UUID, alternatives: bool
    ) -> dict[str, Any]:
        scope = {"kind": kind, "id": str(target), "alternatives": alternatives}
        if kind not in {"artist", "release", "recording"}:
            raise MusicError("catalog_download_target_invalid", "Unknown catalogue target.", 422)
        with self.sessions.begin() as session:
            _acquire_sync_owner_publish_lock(session, actor.user_id)
            row = session.get(CatalogDownloadRow, operation)
            if row is None:
                active = session.scalar(
                    select(func.count())
                    .select_from(CatalogDownloadRow)
                    .where(
                        CatalogDownloadRow.user_id == actor.user_id,
                        CatalogDownloadRow.document["phase"].astext.not_in(["complete", "preview"]),
                    )
                )
                if active is not None and active >= 20:
                    raise MusicError(
                        "music_catalogue_busy",
                        "Finish an active download first.",
                        429,
                        retryable=True,
                    )
                state = {
                    "scope": scope,
                    "phase": "discovery",
                    "offset": 0,
                    "recordings": {},
                    "editions": {},
                    "pending_editions": [],
                    "step": "studio_recordings" if kind == "artist" else kind,
                    "poll": 0,
                }
                row = CatalogDownloadRow(
                    operation_id=operation, user_id=actor.user_id, document=state
                )
                session.add(row)
            if row.user_id != actor.user_id or row.document["scope"] != scope:
                raise MusicError("music_operation_conflict", "The operation conflicts.", 409)
            return self._view(row)

    def command(self, actor: Principal, operation: UUID, action: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            query = select(CatalogDownloadRow).where(
                CatalogDownloadRow.user_id == actor.user_id,
                CatalogDownloadRow.operation_id == operation,
            )
            row = session.scalar(query if action == "status" else query.with_for_update())
            if row is None:
                raise MusicError("catalog_download_missing", "The download is unavailable.", 404)
            if action == "status":
                return self._view(row)
            state = deepcopy(row.document)
            phase = state["phase"]
            if action == "start":
                if phase == "preview":
                    state["phase"] = "downloading"
                elif phase not in {"downloading", "complete"}:
                    raise MusicError(
                        "catalog_preview_incomplete", "Wait for the complete preview.", 409
                    )
            elif action == "advance":
                if phase == "discovery":
                    self._discover(actor, state)
                elif phase == "inspect":
                    self._inspect(actor, operation, state)
                elif phase == "downloading":
                    self._download(actor, operation, state)
            else:
                raise MusicError("catalog_download_action_invalid", "Unknown command.", 422)
            if state != row.document:
                row.document, row.updated_at = state, datetime.now(UTC)
            return self._view(row)

    def device_files(self, actor: Principal, operation: UUID, offset: int) -> dict[str, Any]:
        """Resolve a stable page of the completed selection for download on this device."""
        with self.sessions.begin() as session:
            _acquire_sync_owner_publish_lock(session, actor.user_id)
            row = session.get(CatalogDownloadRow, operation)
            if row is None or row.user_id != actor.user_id:
                raise MusicError("catalog_download_missing", "The download is unavailable.", 404)
            if row.document["phase"] != "complete":
                raise MusicError("catalog_download_incomplete", "Wait for the download.", 409)
            ready = sorted(
                (key, item)
                for key, item in row.document["recordings"].items()
                if item["state"] == "vault"
            )
            page = ready[offset : offset + 50]
            files = []
            unavailable = []
            vault = PostgresVaultRuntime(session)
            for mbid, item in page:
                if item.get("acquisition"):
                    source = session.get(InternetAcquisitionRow, UUID(item["acquisition"]))
                    ref = (
                        session.get(UserTrackRefRow, source.user_track_ref_id)
                        if source
                        and source.user_id == actor.user_id
                        and source.user_track_ref_id
                        and source.state == "READY"
                        else None
                    )
                else:
                    card = CatalogueTrackCard(
                        CatalogueContextEntity.RECORDING,
                        UUID(mbid),
                        UUID(mbid),
                        item["title"],
                        item["artist"],
                        duration_ms=item["duration_ms"],
                    )
                    ref = catalogue_track_ref(session, actor.user_id, card)
                if ref is None or ref.user_id != actor.user_id or ref.deleted_at is not None:
                    unavailable.append(item["title"])
                    continue
                try:
                    variant = vault.resolve_owner_playback_variant(
                        actor.user_id, ref.user_track_ref_id, include_catalog=True
                    )
                except VaultNotFoundError:
                    unavailable.append(item["title"])
                    continue
                files.append(
                    {
                        "user_track_ref_id": str(ref.user_track_ref_id),
                        "audio_variant_id": str(variant),
                        "row_version": ref.row_version,
                        "recording_id": str(ref.recording_id) if ref.recording_id else None,
                        "resolution_status": ref.resolution_status,
                        "title": ref.raw_title or item["title"],
                        "artist": ref.raw_artist,
                        "album": ref.raw_album,
                        "duration_ms": ref.raw_duration_ms,
                    }
                )
            end = offset + len(page)
            return {
                "files": files,
                "unavailable": unavailable,
                "next_offset": end if end < len(ready) else None,
            }

    @staticmethod
    def _add(state: dict[str, Any], card: dict[str, Any]) -> None:
        mbid = card.get("recording_id")
        if mbid:
            state["recordings"].setdefault(
                mbid,
                {
                    "title": card.get("recording_title") or card["title"],
                    "artist": card.get("artist"),
                    "duration_ms": card.get("duration_ms"),
                    "query": card.get("download_search_query"),
                    "state": "pending",
                },
            )

    def _discover(self, actor: Principal, state: dict[str, Any]) -> None:
        scope, step, offset = state["scope"], state["step"], state["offset"]
        target = UUID(scope["id"])
        if step == "recording":
            result = self.contexts.create(actor, CatalogueContextEntity.RECORDING, target)
            card = result["lookup_metadata"]
            if not isinstance(card, dict):
                raise MusicError("music_catalogue_unavailable", "Invalid catalogue receipt.", 503)
            self._add(
                state,
                {
                    **card,
                    "recording_id": str(target),
                    "download_search_query": (str(card["artist"]) + " " + str(card["title"]))[:200]
                    if card["artist"]
                    else None,
                },
            )
            state["phase"] = "inspect"
            return
        if step == "studio_recordings":
            page = self.discovery.artist_tracks(actor, target, limit=50, offset=offset)
            for card in _items(page):
                if _studio_recording(card):
                    self._add(state, card)
            if page["next_offset"] is None:
                state["phase"] = "inspect"
        elif step == "artist":
            # Resume previews created before the studio-discography scope was introduced.
            page = self.discovery.artist_releases(actor, target, limit=50, offset=offset)
            for card in _items(page):
                group = (
                    card["entity_id"]
                    if scope["alternatives"]
                    else card.get("release_group_id") or card["entity_id"]
                )
                edition = [card.get("release_date") or "9999", card["entity_id"]]
                if group not in state["editions"] or edition < state["editions"][group]:
                    state["editions"][group] = edition
            if page["next_offset"] is None:
                state["pending_editions"] = sorted(v[1] for v in state["editions"].values())
                state["step"] = "guests"
        elif step == "guests":
            page = self.discovery.artist_tracks(actor, target, limit=50, offset=offset)
            for card in _items(page):
                if str(target) in card.get("guest_artist_ids", []):
                    self._add(state, card)
            if page["next_offset"] is None:
                state["step"] = "editions"
        else:
            if step == "editions" and not state["pending_editions"]:
                state["phase"] = "inspect"
                return
            release = UUID(state["pending_editions"][0]) if step == "editions" else target
            page = self.discovery.release_tracks(actor, release, limit=50, offset=offset)
            for card in _items(page):
                self._add(state, card)
            if page["next_offset"] is None:
                if step == "editions":
                    state["pending_editions"].pop(0)
                else:
                    state["phase"] = "inspect"
        if page.get("truncated"):
            raise MusicError(
                "catalog_discography_incomplete",
                "The provider returned an incomplete discography.",
                503,
                retryable=True,
            )
        state["offset"] = page["next_offset"] or 0

    def _inspect(self, actor: Principal, operation: UUID, state: dict[str, Any]) -> None:
        pending = [
            (key, item) for key, item in state["recordings"].items() if item["state"] == "pending"
        ]
        if not pending:
            state["phase"] = "preview"
            return
        mbid, item = min(pending)
        cache = self.discovery.page_cache
        if cache and mbid in cache.available_recordings(actor.user_id, [mbid]):
            item["state"] = "vault"
            return
        if not item["query"] or not item["duration_ms"]:
            item.update(state="missing", reason="catalogue_match_evidence_missing")
            return
        attempt = item.get("attempt", 0)
        search_id = uuid5(operation, f"search:{mbid}:{attempt}")
        context_id = uuid5(operation, f"context:{mbid}:{attempt}")
        with self.sessions.begin() as session:
            if session.get(InternetCatalogueContextRow, context_id) is None:
                card = CatalogueTrackCard(
                    CatalogueContextEntity.RECORDING,
                    UUID(mbid),
                    UUID(mbid),
                    item["title"],
                    item["artist"],
                    duration_ms=item["duration_ms"],
                )
                PostgresCatalogueContextRepository(session).create(
                    actor.user_id, context_id, card, datetime.now(UTC)
                )
        try:
            result = self.sources.search(actor, item["query"], search_id, context_id)
        except MusicError as exc:
            if exc.code == "music_catalogue_context_expired":
                item["attempt"] = attempt + 1
                return
            if exc.retryable or exc.status_code in {401, 403, 429}:
                raise
            item.update(state="failed", reason=exc.code)
            return
        candidate = next((c for c in result["candidates"] if same_track(c, item)), None)
        if candidate is None:
            item.update(state="missing", reason="source_not_found")
            return
        item.update(state="available", search=str(search_id), candidate=candidate["candidate_id"])
        with self.sessions() as session:
            prior = session.scalars(
                select(InternetAcquisitionRow)
                .join(
                    InternetSearchContextRow,
                    InternetSearchContextRow.search_id == InternetAcquisitionRow.search_id,
                )
                .join(
                    InternetCatalogueContextRow,
                    InternetCatalogueContextRow.context_id == InternetSearchContextRow.context_id,
                )
                .where(
                    InternetCatalogueContextRow.user_id == actor.user_id,
                    InternetCatalogueContextRow.card["recording_mbid"].astext == mbid,
                    InternetAcquisitionRow.user_id == actor.user_id,
                    InternetAcquisitionRow.candidate_id == candidate["candidate_id"],
                    InternetAcquisitionRow.selected_snapshot["provider"].astext
                    == candidate["provider"],
                    InternetAcquisitionRow.state != "FAILED",
                )
                .order_by(InternetAcquisitionRow.created_at.desc())
                .limit(1)
            ).first()
            if prior:
                status = self.sources.status(actor, prior.acquisition_id)
                if status["state"] != "FAILED":
                    item.update(
                        state="vault" if status["state"] == "READY" else "queued",
                        acquisition=str(prior.acquisition_id),
                    )

    def _download(self, actor: Principal, operation: UUID, state: dict[str, Any]) -> None:
        items = state["recordings"].values()
        if any(item["state"] == "pending" for item in items):
            self._inspect(actor, operation, state)
            return
        available = next(
            ((key, it) for key, it in state["recordings"].items() if it["state"] == "available"),
            None,
        )
        if available is not None:
            source_mbid, item = available
            try:
                status = self.sources.select(
                    actor, UUID(item["search"]), item["candidate"], recording_mbid=UUID(source_mbid)
                )
            except MusicError as exc:
                if exc.code == "music_search_expired":
                    item.update(state="pending", attempt=item.get("attempt", 0) + 1)
                    return
                if exc.retryable or exc.status_code in {401, 403, 429}:
                    raise
                item.update(state="failed", reason=exc.code)
                return
        else:
            queued = [it for it in items if it["state"] == "queued"]
            if not queued:
                state["phase"] = "complete"
                return
            item = queued[state["poll"] % len(queued)]
            state["poll"] += 1
            status = self.sources.status(actor, UUID(item["acquisition"]))
        item.update(
            acquisition=status["acquisition_id"],
            reason=status.get("error_code"),
            state={"READY": "vault", "FAILED": "failed"}.get(status["state"], "queued"),
        )

    @staticmethod
    def _view(row: CatalogDownloadRow) -> dict[str, Any]:
        state = row.document
        items = state["recordings"]
        counts = dict.fromkeys(("pending", "vault", "queued", "available", "missing", "failed"), 0)
        for item in items.values():
            counts[item["state"]] += 1
        return {
            "operation_id": str(row.operation_id),
            "phase": state["phase"],
            "counts": counts,
            "total": len(items),
            "scope": state["scope"],
            "issues": [
                {"recording_id": key, "title": item["title"], "reason": item["reason"]}
                for key, item in items.items()
                if item["state"] in {"failed", "missing"}
            ],
        }
