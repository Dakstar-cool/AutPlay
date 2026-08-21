"""P09 server-side, transaction-owned sync use cases.

The transport remains at-least-once.  This module deliberately stores a terminal ACK
with each inbox row, so a timeout after commit can never reapply an intent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import rfc8785
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models import (
    ArtistCreditNameRow,
    ArtistCreditRow,
    ArtistRow,
    BootstrapSessionRow,
    BootstrapSnapshotItemRow,
    DeviceEventInboxRow,
    DeviceRow,
    DeviceSyncCursorRow,
    IdempotencyRecordRow,
    LibraryEntryRow,
    ListeningEventRow,
    MediumRow,
    PlaylistEntryRow,
    PlaylistRow,
    RecommendationItemRow,
    RecommendationRequestRow,
    RecordingRedirectRow,
    RecordingRow,
    ReleaseRow,
    ReleaseTrackRow,
    SyncEventRow,
    TombstoneRow,
    UserInteractionEventRow,
    UserTrackPreferenceRow,
    UserTrackRefRow,
)
from autplay.domain.auth import Principal

_MAX_PAYLOAD_BYTES: Final = 262_144
_MAX_BATCH: Final = 100
_MAX_PULL: Final = 500
_EVENT_INSERT_BATCH: Final = 500
_OWNER_RECORDING_PAGE_SIZE: Final = 100
CATALOG_ARTIST_ID_V1: Final = "CATALOG_ARTIST_ID_V1"
_CATALOG_AGGREGATES: Final = frozenset(
    {"ARTIST", "ARTIST_CREDIT", "RECORDING_ARTIST_CREDIT", "RELEASE_ARTIST_CREDIT"}
)
_MISSING: Final = object()
_SAFE_KEY = __import__("re").compile(r"^[a-z][a-z0-9_]{0,99}$")
_FORBIDDEN = __import__("re").compile(
    r"(^|_)(access_token|refresh_token|token|authorization|password|credential|private_url|"
    r"base_url|filesystem_path|absolute_path|raw_path|raw_search_query|search_query|"
    r"raw_model_features|model_features|feature_vector|debug_text|personal_debug)(_|$)"
)
_GENERIC_EVENT_TYPES: Final = {
    "USER_TRACK_REF_CREATED": "USER_TRACK_REF",
    "USER_TRACK_REF_PATCHED": "USER_TRACK_REF",
    "LIBRARY_ENTRY_UPSERTED": "LIBRARY_ENTRY",
    "USER_TRACK_PREFERENCE_SET": "USER_TRACK_PREFERENCE",
    "PLAYLIST_CREATED": "PLAYLIST",
    "PLAYLIST_METADATA_PATCHED": "PLAYLIST",
    "PLAYLIST_ENTRY_UPSERTED": "PLAYLIST_ENTRY",
    "PLAYLIST_ENTRY_MOVED": "PLAYLIST_ENTRY",
}


class SyncError(ValueError):
    """Stable, non-disclosing protocol error."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code, self.retryable = code, retryable
        super().__init__(code)


class _ProjectionConflict(Exception):
    """An owner-scoped optimistic write lost to a newer server version."""


class _AttributionNotFound(Exception):
    """Non-disclosing missing or foreign recommendation pointer."""


@dataclass(frozen=True)
class CatalogArtistProjection:
    """Typed, owner-scoped catalog projection emitted only to capable sync clients."""

    aggregate_type: str
    aggregate_id: UUID
    row_version: int
    payload: dict[str, Any]


class CatalogArtistSyncPublisher:
    """Publish deterministic catalog closure events without deriving identity from names."""

    def publish(self, session: Session, owner_user_id: UUID) -> int:
        resolved_refs = list(
            session.scalars(
                select(UserTrackRefRow)
                .where(
                    UserTrackRefRow.user_id == owner_user_id,
                    UserTrackRefRow.deleted_at.is_(None),
                    UserTrackRefRow.recording_id.is_not(None),
                )
                .order_by(UserTrackRefRow.user_track_ref_id)
            )
        )
        candidates: list[dict[str, Any]] = []
        for ref in resolved_refs:
            payload = {
                "recording_id": str(ref.recording_id),
                "title": ref.raw_title,
                "artist": ref.raw_artist,
                "album": ref.raw_album,
                "duration_ms": ref.raw_duration_ms,
                "resolution_status": ref.resolution_status,
            }
            payload_digest = hashlib.sha256(_canonical_payload_bytes(payload)).hexdigest()
            event_id = uuid5(
                NAMESPACE_URL,
                f"autplay:user-track-ref-projection-v1:{owner_user_id}:"
                f"{ref.user_track_ref_id}:{ref.row_version}:{payload_digest}",
            )
            candidates.append(
                {
                    "event_id": event_id,
                    "user_id": owner_user_id,
                    "origin_device_id": None,
                    "event_type": "USER_TRACK_REF_PATCHED",
                    "schema_version": 1,
                    "aggregate_type": "USER_TRACK_REF",
                    "aggregate_id": ref.user_track_ref_id,
                    "server_row_version": ref.row_version,
                    "operation": "UPSERT",
                    "payload": payload,
                }
            )
        projections = _catalog_artist_projections(session, owner_user_id)
        for projection in projections:
            # A release's source row_version can stay unchanged while this owner's
            # reachable recording closure changes.  Bind event identity to the full
            # canonical payload so that newly resolved tracks produce a new fact.
            payload_digest = hashlib.sha256(
                _canonical_payload_bytes(projection.payload)
            ).hexdigest()
            event_id = uuid5(
                NAMESPACE_URL,
                f"autplay:catalog-artist-v1:{owner_user_id}:{projection.aggregate_type}:"
                f"{projection.aggregate_id}:{projection.row_version}:{payload_digest}",
            )
            candidates.append(
                {
                    "event_id": event_id,
                    "user_id": owner_user_id,
                    "origin_device_id": None,
                    "event_type": {
                        "ARTIST": "CATALOG_ARTIST_UPSERTED",
                        "ARTIST_CREDIT": "CATALOG_ARTIST_CREDIT_UPSERTED",
                        "RECORDING_ARTIST_CREDIT": "CATALOG_RECORDING_CREDIT_LINK_UPSERTED",
                        "RELEASE_ARTIST_CREDIT": "CATALOG_RELEASE_CREDIT_LINK_UPSERTED",
                    }[projection.aggregate_type],
                    "schema_version": 1,
                    "aggregate_type": projection.aggregate_type,
                    "aggregate_id": projection.aggregate_id,
                    "server_row_version": projection.row_version,
                    "operation": "UPSERT",
                    "payload": projection.payload,
                }
            )
        for start in range(0, len(candidates), _EVENT_INSERT_BATCH):
            session.execute(
                pg_insert(SyncEventRow)
                .values(candidates[start : start + _EVENT_INSERT_BATCH])
                .on_conflict_do_nothing(index_elements=[SyncEventRow.event_id])
            )
        return len(candidates)


class OpaqueCursor:
    """Signed cursor carrying only owner, device epoch and server sequence."""

    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def encode(self, *, user_id: UUID, device_id: UUID, epoch: UUID, sequence: int) -> str:
        body = json.dumps(
            {
                "v": 1,
                "p": "sync",
                "u": str(user_id),
                "d": str(device_id),
                "e": str(epoch),
                "s": sequence,
                "x": int(_now().timestamp()) + 3600,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        signature = hmac.new(self._secret, body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(body + signature).decode().rstrip("=")

    def decode(self, value: str, *, user_id: UUID, device_id: UUID, epoch: UUID) -> int:
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            body, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(
                signature, hmac.new(self._secret, body, hashlib.sha256).digest()
            ):
                raise ValueError
            parsed = json.loads(body)
            if (
                parsed.get("v") != 1
                or parsed.get("p") != "sync"
                or parsed.get("u") != str(user_id)
                or parsed.get("d") != str(device_id)
                or parsed.get("e") != str(epoch)
                or not isinstance(parsed.get("x"), int)
                or parsed["x"] < int(_now().timestamp())
            ):
                raise ValueError
            sequence = parsed["s"]
            if not isinstance(sequence, int) or sequence < 0:
                raise ValueError
            return sequence
        except ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError:
            raise SyncError("CURSOR_INVALID") from None

    def encode_bootstrap(
        self, *, snapshot_id: UUID, user_id: UUID, device_id: UUID, epoch: UUID, ordinal: int
    ) -> str:
        """Bind a bootstrap continuation to one frozen materialization."""
        body = json.dumps(
            {
                "v": 1,
                "p": "bootstrap",
                "n": str(snapshot_id),
                "u": str(user_id),
                "d": str(device_id),
                "e": str(epoch),
                "o": ordinal,
                "x": int(_now().timestamp()) + 3600,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return (
            base64.urlsafe_b64encode(body + hmac.new(self._secret, body, hashlib.sha256).digest())
            .decode()
            .rstrip("=")
        )

    def decode_bootstrap(
        self, value: str, *, snapshot_id: UUID, user_id: UUID, device_id: UUID, epoch: UUID
    ) -> int:
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            body, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(
                signature, hmac.new(self._secret, body, hashlib.sha256).digest()
            ):
                raise ValueError
            parsed = json.loads(body)
            if (
                parsed.get("v"),
                parsed.get("p"),
                parsed.get("n"),
                parsed.get("u"),
                parsed.get("d"),
                parsed.get("e"),
            ) != (1, "bootstrap", str(snapshot_id), str(user_id), str(device_id), str(epoch)):
                raise ValueError
            if (
                not isinstance(parsed.get("o"), int)
                or parsed["o"] < 0
                or not isinstance(parsed.get("x"), int)
                or parsed["x"] < int(_now().timestamp())
            ):
                raise ValueError
            ordinal = parsed["o"]
            assert isinstance(ordinal, int)
            return ordinal
        except ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError:
            raise SyncError("BOOTSTRAP_SNAPSHOT_INVALID") from None


class SyncService:
    """A short-session PostgreSQL implementation of every P09 server operation."""

    def __init__(self, engine: Engine, *, cursor_secret: bytes) -> None:
        self._sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        self._cursors = OpaqueCursor(cursor_secret)

    def bind(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        device_id, epoch = _binding(principal, body)
        with self._sessions.begin() as session:
            device = session.get(DeviceRow, device_id)
            if (
                device is None
                or device.user_id != principal.user_id
                or device.revoked_at is not None
            ):
                raise SyncError("BINDING_MISMATCH")
            device.device_name, device.platform, device.app_version = (
                _string(body, "device_name", 200),
                _enum(body, "platform", {"ANDROID", "WEB", "OTHER"}),
                _string(body, "app_version", 100),
            )
            device.last_seen_at = _now()
            cursor = session.get(DeviceSyncCursorRow, device_id)
            if cursor is None:
                cursor = DeviceSyncCursorRow(device_id=device_id, user_id=principal.user_id)
                session.add(cursor)
            if cursor.journal_epoch is not None and cursor.journal_epoch != epoch:
                raise SyncError("JOURNAL_RESET_REQUIRED")
            cursor.journal_epoch = epoch
            cursor.updated_at = _now()
        return _binding_response(principal, body)

    def push(self, principal: Principal, body: dict[str, Any], request_id: UUID) -> dict[str, Any]:
        device_id, epoch = _binding(principal, body)
        events = body.get("events")
        if not isinstance(events, list) or not 1 <= len(events) <= _MAX_BATCH:
            raise SyncError("REQUEST_VALIDATION_FAILED")
        _ensure_ascending(events)
        acks: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                raise SyncError("REQUEST_VALIDATION_FAILED")
            with self._sessions.begin() as session:
                acks.append(self._push_one(session, principal, device_id, epoch, event, request_id))
        with self._sessions() as session:
            cursor = session.get(DeviceSyncCursorRow, device_id)
            through = 0 if cursor is None else cursor.last_acked_device_sequence
        return {
            "protocol_version": 1,
            "device_id": str(device_id),
            "server_profile_id": str(body["server_profile_id"]),
            "journal_epoch": str(epoch),
            "acknowledged_through_device_sequence": through,
            "acks": acks,
            "server_time": _iso(_now()),
        }

    def _push_one(
        self,
        session: Session,
        principal: Principal,
        device_id: UUID,
        epoch: UUID,
        event: dict[str, Any],
        request_id: UUID,
    ) -> dict[str, Any]:
        normalized = _event(principal, device_id, event)
        # One device journal is a single ordered stream.  Lock its cursor before any
        # duplicate/sequence decision, so concurrent HTTP retries cannot both accept
        # the same next sequence and surface a uniqueness exception as a 500.
        cursor = session.scalar(
            select(DeviceSyncCursorRow)
            .where(DeviceSyncCursorRow.device_id == device_id)
            .with_for_update()
        )
        if cursor is None or cursor.journal_epoch != epoch:
            return _rejected(normalized, "JOURNAL_RESET_REQUIRED", request_id)
        existing = session.get(DeviceEventInboxRow, normalized["event_id"])
        if existing is not None:
            if existing.request_hash != normalized["request_hash"]:
                return _rejected(normalized, "EVENT_HASH_MISMATCH", request_id)
            return _duplicate(existing.terminal_ack, normalized)
        same_sequence = session.scalar(
            select(DeviceEventInboxRow).where(
                DeviceEventInboxRow.device_id == device_id,
                DeviceEventInboxRow.device_sequence == normalized["device_sequence"],
            )
        )
        if same_sequence is not None:
            return _rejected(normalized, "DEVICE_SEQUENCE_REUSE", request_id)
        scope = f"sync-event:{principal.user_id}:{device_id}:{epoch}"
        idempotency = session.get(IdempotencyRecordRow, (scope, normalized["idempotency_key"]))
        if idempotency is not None:
            if idempotency.request_hash != normalized["request_hash"]:
                return _rejected(normalized, "IDEMPOTENCY_KEY_REUSE", request_id)
            if isinstance(idempotency.response_reference, dict):
                return _duplicate(idempotency.response_reference, normalized)
        expected = cursor.last_acked_device_sequence + 1
        if normalized["device_sequence"] != expected:
            return _rejected(normalized, "DEVICE_SEQUENCE_GAP", request_id, retryable=True)
        inbox = DeviceEventInboxRow(
            event_id=normalized["event_id"],
            device_id=device_id,
            user_id=principal.user_id,
            device_sequence=normalized["device_sequence"],
            event_type=normalized["event_type"],
            schema_version=normalized["schema_version"],
            aggregate_type=normalized["aggregate_type"],
            aggregate_id=normalized["aggregate_id"],
            aggregate_local_id=normalized["aggregate_local_id"],
            idempotency_key=normalized["idempotency_key"],
            base_server_row_version=normalized["base_version"],
            payload=normalized["payload"],
            occurred_at=normalized["occurred_at"],
            request_hash=normalized["request_hash"],
        )
        session.add(inbox)
        ack = self._apply_event(session, principal, normalized, request_id)
        inbox.apply_status = ack["outcome"]
        inbox.error_code = ack.get("error", {}).get("code")
        inbox.terminal_ack = ack
        session.add(
            IdempotencyRecordRow(
                scope=scope,
                idempotency_key=normalized["idempotency_key"],
                request_hash=normalized["request_hash"],
                response_code=200,
                response_reference=ack,
                status="COMPLETED",
                created_at=_now(),
                expires_at=_now() + timedelta(days=30),
            )
        )
        cursor.last_acked_device_sequence = normalized["device_sequence"]
        cursor.updated_at = _now()
        cursor.last_successful_sync_at = _now()
        return ack

    def _apply_event(
        self, session: Session, principal: Principal, event: dict[str, Any], request_id: UUID
    ) -> dict[str, Any]:
        if event["schema_version"] != 1:
            return _rejected(event, "UNSUPPORTED_SCHEMA_VERSION", request_id)
        kind = event["event_type"]
        if kind in {
            "LISTENING_EVENT_RECORDED",
            "RECOMMENDATION_IMPRESSION_RECORDED",
            "RECOMMENDATION_FEEDBACK_RECORDED",
        }:
            return self._apply_interaction(session, principal, event, request_id)
        if kind in _GENERIC_EVENT_TYPES or kind == "AGGREGATE_DELETED":
            return self._apply_generic(session, principal, event, request_id)
        return _rejected(event, "UNSUPPORTED_EVENT_TYPE", request_id)

    def _apply_interaction(
        self, session: Session, principal: Principal, event: dict[str, Any], request_id: UUID
    ) -> dict[str, Any]:
        kind = event["event_type"]
        if event["event_id"] != event["aggregate_local_id"]:
            return _rejected(event, "EVENT_AGGREGATE_ID_MISMATCH", request_id)
        payload = event["payload"]
        recommendation = payload.get("recommendation")
        expected_aggregate = (
            "LISTENING_EVENT" if kind == "LISTENING_EVENT_RECORDED" else "USER_INTERACTION_EVENT"
        )
        if (
            event["aggregate_type"] != expected_aggregate
            or payload.get("interaction_type") != kind
            or (recommendation is not None and not isinstance(recommendation, dict))
        ):
            return _rejected(event, "REQUEST_VALIDATION_FAILED", request_id)
        if recommendation is not None:
            assert isinstance(recommendation, dict)
        try:
            attribution = _validate_attribution(session, principal, recommendation)
            if kind == "LISTENING_EVENT_RECORDED":
                return self._apply_listening(session, principal, event, attribution, request_id)
            if kind == "RECOMMENDATION_IMPRESSION_RECORDED" and (
                attribution is None
                or attribution[3] is None
                or not isinstance(recommendation, dict)
                or not isinstance(recommendation.get("display_position"), int)
            ):
                return _rejected(event, "REQUEST_VALIDATION_FAILED", request_id)
            if kind == "RECOMMENDATION_FEEDBACK_RECORDED":
                if (
                    attribution is None
                    or attribution[4] is None
                    or payload.get("feedback_type") not in {"SELECTED", "DISMISSED"}
                ):
                    return _rejected(event, "REQUEST_VALIDATION_FAILED", request_id)
                impression = session.get(UserInteractionEventRow, attribution[4])
                # Deliberately non-disclosing: missing, foreign and non-causal IDs all map alike.
                if (
                    impression is None
                    or impression.user_id != principal.user_id
                    or impression.device_id != principal.device_id
                    or impression.event_type != "RECOMMENDATION_IMPRESSION_RECORDED"
                    or impression.recommendation_request_id != attribution[0]
                    or impression.recording_id != attribution[1]
                    or impression.source_rank != attribution[2]
                    or (attribution[3] is not None and impression.presentation_id != attribution[3])
                ):
                    return _rejected(event, "ATTRIBUTION_NOT_FOUND", request_id)
        except _AttributionNotFound:
            return _rejected(event, "ATTRIBUTION_NOT_FOUND", request_id)
        except KeyError, TypeError, ValueError, SyncError:
            return _rejected(event, "REQUEST_VALIDATION_FAILED", request_id)
        interaction = UserInteractionEventRow(
            interaction_id=event["event_id"],
            user_id=principal.user_id,
            device_id=principal.device_id,
            event_type=kind,
            recommendation_request_id=None if attribution is None else attribution[0],
            recording_id=None if attribution is None else attribution[1],
            source_rank=None if attribution is None else attribution[2],
            presentation_id=None if attribution is None else attribution[3],
            impression_interaction_id=None if attribution is None else attribution[4],
            payload=payload,
            created_at=_now(),
        )
        if kind == "RECOMMENDATION_IMPRESSION_RECORDED" and interaction.presentation_id is None:
            return _rejected(event, "REQUEST_VALIDATION_FAILED", request_id)
        # The unique presentation index turns different-ID semantic retries into a safe
        # terminal result.
        if kind == "RECOMMENDATION_IMPRESSION_RECORDED" and interaction.presentation_id is not None:
            # Device cursor locks do not serialize different devices. Lock the semantic
            # presentation identity so check-and-insert is race-safe across an owner.
            semantic_key = ":".join(
                (
                    str(principal.user_id),
                    str(interaction.presentation_id),
                    str(interaction.recommendation_request_id),
                    str(interaction.source_rank),
                )
            )
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:semantic_key, 0))"),
                {"semantic_key": semantic_key},
            )
            present = session.scalar(
                select(UserInteractionEventRow.interaction_id).where(
                    UserInteractionEventRow.user_id == principal.user_id,
                    UserInteractionEventRow.presentation_id == interaction.presentation_id,
                    UserInteractionEventRow.recommendation_request_id
                    == interaction.recommendation_request_id,
                    UserInteractionEventRow.source_rank == interaction.source_rank,
                )
            )
            if present is not None:
                return _rejected(event, "IMPRESSION_ALREADY_RECORDED", request_id)
        session.add(interaction)
        sync_event = SyncEventRow(
            event_id=event["event_id"],
            user_id=principal.user_id,
            origin_device_id=principal.device_id,
            event_type=kind,
            schema_version=event["schema_version"],
            aggregate_type=event["aggregate_type"],
            aggregate_id=event["aggregate_id"],
            payload=payload,
            operation="UPSERT",
            server_row_version=1,
        )
        session.add(sync_event)
        session.flush()
        return _applied(event, 1)

    def _apply_listening(
        self,
        session: Session,
        principal: Principal,
        event: dict[str, Any],
        attribution: tuple[UUID, UUID, int, UUID | None, UUID | None] | None,
        request_id: UUID,
    ) -> dict[str, Any]:
        """Persist the canonical listening projection as well as its sync event."""
        payload = event["payload"]
        try:
            local_ref = _uuid(payload, "local_user_track_ref_id")
            ref = _owned_row(session, UserTrackRefRow, local_ref, principal)
            stated_server_ref = _uuid_optional(payload, "server_user_track_ref_id")
            if stated_server_ref is not None and stated_server_ref != ref.user_track_ref_id:
                raise ValueError("ref mismatch")
            recording = _uuid_optional(payload, "recording_id")
            if (
                recording is not None
                and ref.recording_id is not None
                and recording != ref.recording_id
            ):
                raise ValueError("recording mismatch")
            origin = _enum(
                payload, "event_origin", {"ORGANIC", "RECOMMENDED", "PLAYLIST", "SEARCH", "WAVE"}
            )
            context = _enum(
                payload, "context", {"GENERAL", "WORKOUT", "CYCLING", "WORK", "SLEEP", "PARTY"}
            )
            feedback = _enum(payload, "explicit_feedback", {"NONE", "LIKE", "DISLIKE"})
            if origin == "RECOMMENDED" and attribution is None:
                raise ValueError("recommendation required")
            played = _nonnegative_bounded(payload, "played_ms", 604_800_000)
            duration = _nullable_range(payload, "track_duration_ms", 1, 604_800_000)
            ratio = payload.get("completion_ratio")
            if ratio is not None and (not isinstance(ratio, int | float) or not 0 <= ratio <= 1):
                raise ValueError("ratio")
            if not isinstance(payload.get("excluded_from_taste"), bool):
                raise ValueError("excluded")
        except KeyError, TypeError, ValueError, SyncError:
            return _rejected(event, "REQUEST_VALIDATION_FAILED", request_id)
        session.add(
            ListeningEventRow(
                listening_event_id=event["event_id"],
                user_id=principal.user_id,
                device_id=principal.device_id,
                user_track_ref_id=ref.user_track_ref_id,
                recording_id=recording,
                started_at=event["occurred_at"],
                played_ms=played,
                track_duration_ms=duration,
                completion_ratio=ratio,
                event_origin=origin,
                context=context,
                recommendation_request_id=None if attribution is None else attribution[0],
                explicit_feedback=feedback,
                excluded_from_taste=payload["excluded_from_taste"],
                created_at=_now(),
            )
        )
        session.add(
            SyncEventRow(
                event_id=event["event_id"],
                user_id=principal.user_id,
                origin_device_id=principal.device_id,
                event_type=event["event_type"],
                schema_version=event["schema_version"],
                aggregate_type=event["aggregate_type"],
                aggregate_id=event["aggregate_id"],
                payload=payload,
                operation="UPSERT",
                server_row_version=1,
            )
        )
        session.flush()
        return _applied(event, 1)

    def _apply_generic(
        self, session: Session, principal: Principal, event: dict[str, Any], request_id: UUID
    ) -> dict[str, Any]:
        """Apply a P07 projection in the already-open inbox transaction."""
        if (
            event["event_type"] != "AGGREGATE_DELETED"
            and event["aggregate_type"] != _GENERIC_EVENT_TYPES[event["event_type"]]
        ):
            return _rejected(event, "UNSUPPORTED_AGGREGATE_TYPE", request_id)
        try:
            row, operation = self._generic_row(session, principal, event)
            session.flush()
        except _ProjectionConflict:
            return _conflict(event)
        except KeyError, TypeError, ValueError:
            return _rejected(event, "REQUEST_VALIDATION_FAILED", request_id)
        version = int(getattr(row, "row_version", 1))
        session.add(
            SyncEventRow(
                event_id=event["event_id"],
                user_id=principal.user_id,
                origin_device_id=principal.device_id,
                event_type=event["event_type"],
                schema_version=event["schema_version"],
                aggregate_type=event["aggregate_type"],
                aggregate_id=event["aggregate_id"],
                payload=event["payload"],
                operation=operation,
                server_row_version=version,
            )
        )
        if operation == "DELETE":
            session.flush()
            session.add(
                TombstoneRow(
                    user_id=principal.user_id,
                    aggregate_type=event["aggregate_type"],
                    aggregate_id=event["aggregate_id"],
                    deleted_by_event_id=event["event_id"],
                    deleted_at=_now(),
                    retain_until=_now() + timedelta(days=30),
                )
            )
        return _applied(event, version)

    def _generic_row(
        self, session: Session, principal: Principal, event: dict[str, Any]
    ) -> tuple[object, str]:
        kind, payload, aggregate_id, base, now = (
            event["event_type"],
            event["payload"],
            event["aggregate_id"],
            event["base_version"],
            _now(),
        )
        row: Any
        if kind == "AGGREGATE_DELETED":
            models: dict[str, type[Any]] = {
                "USER_TRACK_REF": UserTrackRefRow,
                "LIBRARY_ENTRY": LibraryEntryRow,
                "PLAYLIST": PlaylistRow,
                "PLAYLIST_ENTRY": PlaylistEntryRow,
            }
            model = models.get(event["aggregate_type"])
            if model is None:
                raise ValueError("aggregate type")
            row = session.get(model, aggregate_id)
            if row is None:
                raise _ProjectionConflict
            if isinstance(row, PlaylistEntryRow):
                parent = _owned_row(session, PlaylistRow, row.playlist_id, principal)
                if parent.deleted_at is not None:
                    raise _ProjectionConflict
            else:
                _owner(row, principal)
            _require_version(row, base)
            if not hasattr(row, "removed_at") and not hasattr(row, "deleted_at"):
                raise ValueError("append only")
            if hasattr(row, "removed_at"):
                row.removed_at = now
            else:
                row.deleted_at = now
            row.updated_at, row.row_version = now, row.row_version + 1
            return row, "DELETE"
        if kind == "USER_TRACK_REF_CREATED":
            if base is not None:
                raise _ProjectionConflict
            row = UserTrackRefRow(
                user_track_ref_id=aggregate_id,
                user_id=principal.user_id,
                resolution_status="UNRESOLVED",
                raw_title=_optional_string(payload, "title"),
                raw_artist=_optional_string(payload, "artist"),
                raw_album=_optional_string(payload, "album"),
                raw_duration_ms=_optional_int(payload, "duration_ms"),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            nested_entry_id = _uuid_optional(payload, "library_entry_local_id")
            if (
                nested_entry_id is not None
                and session.get(LibraryEntryRow, nested_entry_id) is None
            ):
                session.add(
                    LibraryEntryRow(
                        library_entry_id=nested_entry_id,
                        user_id=principal.user_id,
                        user_track_ref_id=row.user_track_ref_id,
                        source="IMPORT",
                        availability_status="PENDING",
                        added_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
            return row, "UPSERT"
        if kind == "USER_TRACK_REF_PATCHED":
            row = _owned_row(session, UserTrackRefRow, aggregate_id, principal)
            _require_version(row, base)
            for field, key in (
                ("raw_title", "title"),
                ("raw_artist", "artist"),
                ("raw_album", "album"),
            ):
                if key in payload:
                    setattr(row, field, _optional_string(payload, key))
            row.updated_at, row.row_version = now, row.row_version + 1
            return row, "UPSERT"
        if kind == "LIBRARY_ENTRY_UPSERTED":
            row = session.get(LibraryEntryRow, aggregate_id)
            ref_id = _uuid_optional_alias(payload, "user_track_ref_id", "local_user_track_ref_id")
            if row is None:
                if base is not None:
                    raise _ProjectionConflict
                if ref_id is None:
                    raise ValueError("missing ref")
                ref = _owned_row(session, UserTrackRefRow, ref_id, principal)
                row = LibraryEntryRow(
                    library_entry_id=aggregate_id,
                    user_id=principal.user_id,
                    user_track_ref_id=ref.user_track_ref_id,
                    source=_enum_default(
                        payload,
                        "source",
                        {"LOCAL", "IMPORT", "SEARCH", "SHARE", "RESTORE"},
                        "IMPORT",
                    ),
                    availability_status=_enum_default(
                        payload,
                        "availability_status",
                        {"LOCAL", "VAULT", "EXTERNAL", "PENDING", "NOT_FOUND", "AMBIGUOUS"},
                        "PENDING",
                    ),
                    added_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                _owner(row, principal)
                _require_version(row, base)
                removed = payload.get("removed_at_ms", _MISSING)
                if removed is not _MISSING and removed is not None and not isinstance(removed, int):
                    raise ValueError("removed")
                row.removed_at = now if isinstance(removed, int) else None
                row.updated_at = now
                row.row_version += 1
            return row, "UPSERT"
        if kind == "USER_TRACK_PREFERENCE_SET":
            ref_id = _uuid_optional_alias(payload, "user_track_ref_id", "local_user_track_ref_id")
            if ref_id is None:
                ref_id = aggregate_id
            _owned_row(session, UserTrackRefRow, ref_id, principal)
            row = session.get(UserTrackPreferenceRow, ref_id)
            if row is None:
                if base is not None:
                    raise _ProjectionConflict
                row = UserTrackPreferenceRow(
                    user_track_ref_id=ref_id,
                    preference=_enum(payload, "preference", {"NEUTRAL", "LIKED", "DISLIKED"}),
                    excluded_from_taste=_boolean(payload, "excluded_from_taste"),
                    updated_by_event_id=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                _require_version(row, base) if base is not None else None
                row.preference = _enum(payload, "preference", {"NEUTRAL", "LIKED", "DISLIKED"})
                row.excluded_from_taste = _boolean(payload, "excluded_from_taste")
                row.updated_by_event_id, row.updated_at = None, now
                row.row_version += 1
            return row, "UPSERT"
        if kind == "PLAYLIST_CREATED":
            if base is not None:
                raise _ProjectionConflict
            row = PlaylistRow(
                playlist_id=aggregate_id,
                owner_user_id=principal.user_id,
                name=_string(payload, "name", 500),
                description=_optional_string(payload, "description"),
                visibility="PRIVATE",
                playlist_type="MANUAL",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            return row, "UPSERT"
        if kind == "PLAYLIST_METADATA_PATCHED":
            row = _owned_row(session, PlaylistRow, aggregate_id, principal)
            _require_version(row, base)
            if "name" in payload:
                row.name = _string(payload, "name", 500)
            if "description" in payload:
                row.description = _optional_string(payload, "description")
            row.updated_at, row.row_version = now, row.row_version + 1
            return row, "UPSERT"
        if kind not in {"PLAYLIST_ENTRY_UPSERTED", "PLAYLIST_ENTRY_MOVED"}:
            raise ValueError("event type")
        entry = session.get(PlaylistEntryRow, aggregate_id)
        playlist_id = _uuid_optional_alias(payload, "playlist_id", "local_playlist_id")
        if playlist_id is None:
            if entry is None:
                raise ValueError("playlist required")
            playlist_id = entry.playlist_id
        playlist = _owned_row(session, PlaylistRow, playlist_id, principal)
        if entry is None:
            if base is not None:
                raise _ProjectionConflict
            ref_id = _uuid_optional_alias(payload, "user_track_ref_id", "local_user_track_ref_id")
            if ref_id is None:
                raise ValueError("track ref required")
            _owned_row(session, UserTrackRefRow, ref_id, principal)
            entry = PlaylistEntryRow(
                playlist_entry_id=aggregate_id,
                playlist_id=playlist.playlist_id,
                user_track_ref_id=ref_id,
                position_key=_playlist_position(
                    session, playlist.playlist_id, payload, aggregate_id
                ),
                added_by_user_id=principal.user_id,
                added_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(entry)
        else:
            if entry.playlist_id != playlist.playlist_id:
                raise _ProjectionConflict
            _require_version(entry, base)
            entry.position_key = _playlist_position(
                session, playlist.playlist_id, payload, aggregate_id
            )
            entry.updated_at = now
            entry.row_version += 1
        return entry, "UPSERT"

    def pull(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        device_id, epoch = _binding(principal, body)
        limit = body.get("limit", 100)
        if not isinstance(limit, int) or not 1 <= limit <= _MAX_PULL:
            raise SyncError("REQUEST_VALIDATION_FAILED")
        token = body.get("cursor")
        sequence = (
            0
            if token is None
            else self._cursors.decode(
                str(token), user_id=principal.user_id, device_id=device_id, epoch=epoch
            )
        )
        with self._sessions.begin() as session:
            cursor = session.get(DeviceSyncCursorRow, device_id)
            if cursor is None or cursor.journal_epoch != epoch:
                raise SyncError("JOURNAL_RESET_REQUIRED")
            rows = list(
                session.scalars(
                    select(SyncEventRow)
                    .where(
                        SyncEventRow.user_id == principal.user_id,
                        SyncEventRow.server_sequence > sequence,
                    )
                    .order_by(SyncEventRow.server_sequence)
                    .limit(limit + 1)
                )
            )
            page, more = rows[:limit], len(rows) > limit
            delete_ids = [row.event_id for row in page if row.operation == "DELETE"]
            tombstones = {
                row.deleted_by_event_id: row
                for row in session.scalars(
                    select(TombstoneRow).where(TombstoneRow.deleted_by_event_id.in_(delete_ids))
                )
            }
            next_sequence = page[-1].server_sequence if page else sequence
            # A response is not an ACK. Advance only when its cursor comes back on
            # the following pull, preventing process death after receive from losing events.
            cursor.last_pulled_server_sequence = max(cursor.last_pulled_server_sequence, sequence)
            cursor.updated_at = _now()
        next_cursor = self._cursors.encode(
            user_id=principal.user_id, device_id=device_id, epoch=epoch, sequence=next_sequence
        )
        return {
            "protocol_version": 1,
            "device_id": str(device_id),
            "server_profile_id": str(body["server_profile_id"]),
            "journal_epoch": str(epoch),
            "from_cursor": token,
            "next_cursor": next_cursor,
            "has_more": more,
            "events": [
                _server_event(row, tombstones.get(row.event_id))
                for row in page
                if _catalog_enabled(body) or row.aggregate_type not in _CATALOG_AGGREGATES
            ],
            "server_time": _iso(_now()),
        }

    def bootstrap(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        device_id, epoch = _binding(principal, body)
        snapshot_id = _uuid_optional(body, "snapshot_id")
        page_token = body.get("page_token")
        with self._sessions.begin() as session:
            # High-water and owner projections must share one stable MVCC snapshot.
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            if snapshot_id is None:
                high = session.scalar(
                    select(func.coalesce(func.max(SyncEventRow.server_sequence), 0)).where(
                        SyncEventRow.user_id == principal.user_id
                    )
                )
                snapshot = BootstrapSessionRow(
                    snapshot_id=uuid4(),
                    user_id=principal.user_id,
                    device_id=device_id,
                    journal_epoch=epoch,
                    capabilities=sorted(_effective_capabilities(body)),
                    high_water_server_sequence=int(high or 0),
                    created_at=_now(),
                    expires_at=_now() + timedelta(hours=1),
                )
                session.add(snapshot)
                snapshot_id = snapshot.snapshot_id
                projections = _bootstrap_projections(
                    session,
                    principal.user_id,
                    include_catalog_artist=_catalog_enabled(body),
                )
                for ordinal, item in enumerate(projections, start=1):
                    session.add(
                        BootstrapSnapshotItemRow(
                            snapshot_id=snapshot.snapshot_id,
                            ordinal=ordinal,
                            aggregate_type=item[0],
                            aggregate_id=item[1],
                            server_row_version=item[2],
                            payload=item[3],
                        )
                    )
            else:
                existing_snapshot = session.get(BootstrapSessionRow, snapshot_id)
                if (
                    existing_snapshot is None
                    or existing_snapshot.user_id != principal.user_id
                    or existing_snapshot.device_id != device_id
                    or existing_snapshot.journal_epoch != epoch
                    or existing_snapshot.expires_at <= _now()
                ):
                    raise SyncError("BOOTSTRAP_SNAPSHOT_INVALID")
                snapshot = existing_snapshot
            start = (
                0
                if page_token is None
                else self._cursors.decode_bootstrap(
                    str(page_token),
                    snapshot_id=snapshot.snapshot_id,
                    user_id=principal.user_id,
                    device_id=device_id,
                    epoch=epoch,
                )
            )
            items = list(
                session.scalars(
                    select(BootstrapSnapshotItemRow)
                    .where(
                        BootstrapSnapshotItemRow.snapshot_id == snapshot.snapshot_id,
                        BootstrapSnapshotItemRow.ordinal > start,
                    )
                    .order_by(BootstrapSnapshotItemRow.ordinal)
                    .limit(_MAX_PULL + 1)
                )
            )
        page, has_more = items[:_MAX_PULL], len(items) > _MAX_PULL
        next_ordinal = page[-1].ordinal if page else start
        # The first page owns capability negotiation.  Continuations are bound to
        # that frozen snapshot and must not silently filter rows when a retry omits
        # optional negotiation fields.
        catalog_visible = CATALOG_ARTIST_ID_V1 in snapshot.capabilities
        aggregates = [
            {
                "aggregate_type": item.aggregate_type,
                "aggregate_server_id": str(item.aggregate_id),
                "server_row_version": item.server_row_version,
                "payload": item.payload,
            }
            for item in page
            if item.aggregate_type not in {"TOMBSTONE", "RECORDING_REDIRECT"}
            and (catalog_visible or item.aggregate_type not in _CATALOG_AGGREGATES)
        ]
        return {
            "protocol_version": 1,
            "device_id": str(device_id),
            "server_profile_id": str(body["server_profile_id"]),
            "journal_epoch": str(epoch),
            "snapshot_id": str(snapshot_id),
            "snapshot_high_water_server_sequence": snapshot.high_water_server_sequence,
            "aggregates": aggregates,
            "tombstones": [
                {
                    "tombstone_id": str(item.aggregate_id),
                    **_json_object(item.payload),
                }
                for item in page
                if item.aggregate_type == "TOMBSTONE"
            ],
            "redirects": [
                _json_object(item.payload)
                for item in page
                if item.aggregate_type == "RECORDING_REDIRECT"
            ],
            "next_page_token": self._cursors.encode_bootstrap(
                snapshot_id=snapshot.snapshot_id,
                user_id=principal.user_id,
                device_id=device_id,
                epoch=epoch,
                ordinal=next_ordinal,
            )
            if has_more
            else None,
            "has_more": has_more,
            "snapshot_cursor": self._cursors.encode(
                user_id=principal.user_id,
                device_id=device_id,
                epoch=epoch,
                sequence=snapshot.high_water_server_sequence,
            ),
            "pending_event_directive": "PRESERVE_REBASE_RETRY",
            "server_time": _iso(_now()),
        }

    def status(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        device_id, epoch = _binding(principal, body)
        with self._sessions() as session:
            cursor = session.get(DeviceSyncCursorRow, device_id)
            if cursor is None or cursor.journal_epoch != epoch:
                raise SyncError("JOURNAL_RESET_REQUIRED")
            conflicts = int(
                session.scalar(
                    select(func.count())
                    .select_from(DeviceEventInboxRow)
                    .where(
                        DeviceEventInboxRow.device_id == device_id,
                        DeviceEventInboxRow.apply_status == "CONFLICT",
                    )
                )
                or 0
            )
            dead = int(
                session.scalar(
                    select(func.count())
                    .select_from(DeviceEventInboxRow)
                    .where(
                        DeviceEventInboxRow.device_id == device_id,
                        DeviceEventInboxRow.apply_status == "REJECTED",
                    )
                )
                or 0
            )
        return {
            "protocol_version": 1,
            "device_id": str(device_id),
            "server_profile_id": str(body["server_profile_id"]),
            "journal_epoch": str(epoch),
            "state": "CONFLICT" if conflicts else "SYNCED",
            "last_acked_device_sequence": cursor.last_acked_device_sequence,
            "last_pulled_server_sequence": cursor.last_pulled_server_sequence,
            "pending_event_count": 0,
            "dead_letter_count": dead,
            "conflict_count": conflicts,
            "bootstrap_required": False,
            "last_successful_sync_at": _iso(cursor.last_successful_sync_at)
            if cursor.last_successful_sync_at
            else None,
        }

    def compact_tombstones(self, *, now: datetime | None = None) -> int:
        """Remove only expired tombstones acknowledged by every non-revoked owner device."""
        instant = _now() if now is None else now
        removed = 0
        with self._sessions.begin() as session:
            candidates = list(
                session.scalars(select(TombstoneRow).where(TombstoneRow.retain_until <= instant))
            )
            for tombstone in candidates:
                sequence = session.scalar(
                    select(SyncEventRow.server_sequence).where(
                        SyncEventRow.event_id == tombstone.deleted_by_event_id
                    )
                )
                if sequence is None:
                    continue
                pending = session.scalar(
                    select(func.count())
                    .select_from(DeviceRow)
                    .outerjoin(
                        DeviceSyncCursorRow, DeviceSyncCursorRow.device_id == DeviceRow.device_id
                    )
                    .where(
                        DeviceRow.user_id == tombstone.user_id,
                        DeviceRow.revoked_at.is_(None),
                        (DeviceSyncCursorRow.last_pulled_server_sequence.is_(None))
                        | (DeviceSyncCursorRow.last_pulled_server_sequence < sequence),
                    )
                )
                if pending == 0:
                    session.execute(
                        delete(TombstoneRow).where(
                            TombstoneRow.tombstone_id == tombstone.tombstone_id
                        )
                    )
                    removed += 1
        return removed


def _binding(principal: Principal, body: dict[str, Any]) -> tuple[UUID, UUID]:
    if body.get("protocol_version") != 1:
        raise SyncError("UNSUPPORTED_PROTOCOL_VERSION")
    device_id = _uuid(body, "device_id")
    claimed_user_id = _uuid(body, "user_id") if "user_id" in body else principal.user_id
    if device_id != principal.device_id or claimed_user_id != principal.user_id:
        raise SyncError("BINDING_MISMATCH")
    return device_id, _uuid(body, "journal_epoch")


def _event(principal: Principal, device_id: UUID, value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "event_id",
        "idempotency_key",
        "user_id",
        "device_id",
        "device_sequence",
        "event_type",
        "schema_version",
        "aggregate_type",
        "aggregate_local_id",
        "aggregate_server_id",
        "base_server_row_version",
        "occurred_at",
        "payload",
        "request_hash",
    }
    if (
        not required.issubset(value)
        or _uuid(value, "user_id") != principal.user_id
        or _uuid(value, "device_id") != device_id
    ):
        raise SyncError("BINDING_MISMATCH")
    payload = value["payload"]
    if not isinstance(payload, dict) or not _safe(payload):
        raise SyncError("REQUEST_VALIDATION_FAILED")
    _canonical_payload_bytes(payload)
    omitted = {key: item for key, item in value.items() if key != "request_hash"}
    digest = hashlib.sha256(rfc8785.dumps(omitted)).hexdigest()
    supplied = value["request_hash"]
    if not isinstance(supplied, str) or not hmac.compare_digest(digest, supplied):
        raise SyncError("REQUEST_HASH_MISMATCH")
    server_id = _uuid_optional(value, "aggregate_server_id")
    return {
        "event_id": _uuid(value, "event_id"),
        "idempotency_key": _string(value, "idempotency_key", 300),
        "device_sequence": _positive(value, "device_sequence"),
        "event_type": _string(value, "event_type", 200),
        "schema_version": _positive(value, "schema_version"),
        "aggregate_type": _string(value, "aggregate_type", 100),
        "aggregate_local_id": _uuid(value, "aggregate_local_id"),
        "aggregate_id": server_id or _uuid(value, "aggregate_local_id"),
        "base_version": value["base_server_row_version"]
        if isinstance(value["base_server_row_version"], int)
        else None,
        "occurred_at": _time(value, "occurred_at"),
        "payload": payload,
        "request_hash": bytes.fromhex(digest),
    }


def _ensure_ascending(events: list[Any]) -> None:
    sequences = [item.get("device_sequence") if isinstance(item, dict) else None for item in events]
    if not all(isinstance(value, int) for value in sequences):
        raise SyncError("BATCH_SEQUENCE_NOT_ASCENDING", retryable=True)
    start = sequences[0]
    assert isinstance(start, int)
    if sequences != list(range(start, start + len(sequences))):
        raise SyncError("BATCH_SEQUENCE_NOT_ASCENDING", retryable=True)


def _safe(value: Any, depth: int = 0) -> bool:
    if depth > 32:
        return False
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and _SAFE_KEY.fullmatch(key)
            and not _FORBIDDEN.search(key)
            and _safe(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return len(value) <= 10_000 and all(_safe(item, depth + 1) for item in value)
    return value is None or isinstance(value, str | int | float | bool)


def _canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    """Return bounded wire bytes for inbound and server-generated sync payloads."""
    canonical = rfc8785.dumps(payload)
    if len(canonical) > _MAX_PAYLOAD_BYTES:
        raise SyncError("PAYLOAD_TOO_LARGE")
    return canonical


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SyncError("BOOTSTRAP_SNAPSHOT_INVALID")
    return dict(value)


def _uuid(value: dict[str, Any], key: str) -> UUID:
    try:
        return UUID(str(value[key]))
    except KeyError, TypeError, ValueError:
        raise SyncError("REQUEST_VALIDATION_FAILED") from None


def _optional_string(value: dict[str, Any], key: str) -> str | None:
    candidate = value.get(key)
    if candidate is not None and not isinstance(candidate, str):
        raise ValueError(key)
    return candidate


def _optional_int(value: dict[str, Any], key: str) -> int | None:
    candidate = value.get(key)
    if candidate is not None and (not isinstance(candidate, int) or candidate < 0):
        raise ValueError(key)
    return candidate


def _owner(row: object, principal: Principal) -> None:
    owner = getattr(row, "user_id", getattr(row, "owner_user_id", None))
    if owner != principal.user_id:
        raise _ProjectionConflict


def _owned_row(session: Session, model: type[Any], identifier: UUID, principal: Principal) -> Any:
    row = session.get(model, identifier)
    if row is None:
        raise _ProjectionConflict
    _owner(row, principal)
    return row


def _require_version(row: object, base: int | None) -> None:
    if base is None or getattr(row, "row_version", None) != base:
        raise _ProjectionConflict


def _conflict(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(event["event_id"]),
        "device_sequence": event["device_sequence"],
        "aggregate_type": event["aggregate_type"],
        "aggregate_local_id": str(event["aggregate_local_id"]),
        "aggregate_server_id": str(event["aggregate_id"]),
        "outcome": "CONFLICT",
        "conflict": {"conflict_kind": "STALE_VERSION", "resolution_state": "REVIEW_REQUIRED"},
    }


def _uuid_optional(value: dict[str, Any] | None, key: str) -> UUID | None:
    return None if value is None or value.get(key) is None else _uuid(value, key)


def _uuid_optional_alias(value: dict[str, Any], *keys: str) -> UUID | None:
    for key in keys:
        if value.get(key) is not None:
            return _uuid(value, key)
    return None


def _int_optional(value: dict[str, Any] | None, key: str) -> int | None:
    candidate = None if value is None else value.get(key)
    return candidate if isinstance(candidate, int) else None


def _nonnegative_bounded(value: dict[str, Any], key: str, maximum: int) -> int:
    candidate = value.get(key)
    if not isinstance(candidate, int) or not 0 <= candidate <= maximum:
        raise ValueError(key)
    return candidate


def _nullable_range(value: dict[str, Any], key: str, minimum: int, maximum: int) -> int | None:
    candidate = value.get(key)
    if candidate is not None and (
        not isinstance(candidate, int) or not minimum <= candidate <= maximum
    ):
        raise ValueError(key)
    return candidate


def _boolean(value: dict[str, Any], key: str) -> bool:
    candidate = value.get(key)
    if not isinstance(candidate, bool):
        raise ValueError(key)
    return candidate


def _enum_default(value: dict[str, Any], key: str, allowed: set[str], default: str) -> str:
    return default if key not in value else _enum(value, key, allowed)


def _playlist_position(
    session: Session, playlist_id: UUID, payload: dict[str, Any], self_id: UUID
) -> str:
    """Translate P07's before-local-id intent into a bounded, deterministic server key."""
    explicit = payload.get("position_key")
    if explicit is not None:
        return _string(payload, "position_key", 128)
    before = _uuid_optional_alias(
        payload, "before_playlist_entry_id", "before_local_playlist_entry_id"
    )
    rows = list(
        session.scalars(
            select(PlaylistEntryRow)
            .where(
                PlaylistEntryRow.playlist_id == playlist_id,
                PlaylistEntryRow.removed_at.is_(None),
                PlaylistEntryRow.playlist_entry_id != self_id,
            )
            .order_by(PlaylistEntryRow.position_key, PlaylistEntryRow.playlist_entry_id)
        )
    )
    if before is not None:
        for index, item in enumerate(rows):
            if item.playlist_entry_id == before:
                return f"{index:08d}:{self_id}"
        raise _ProjectionConflict
    return f"{len(rows):08d}:{self_id}"


def _validate_attribution(
    session: Session, principal: Principal, value: dict[str, Any] | None
) -> tuple[UUID, UUID, int, UUID | None, UUID | None] | None:
    """Validate request ownership and immutable recommendation item identity.

    Missing and foreign records intentionally have the same error upstream, preventing
    ownership probing through the sync endpoint.
    """
    if value is None:
        return None
    request_id = _uuid(value, "recommendation_request_id")
    recording_id = _uuid(value, "recording_id")
    rank = value.get("source_rank")
    if not isinstance(rank, int) or not 1 <= rank <= 1000:
        raise ValueError("rank")
    if not isinstance(value.get("source"), str) or not _SAFE_KEY.fullmatch(value["source"]):
        raise ValueError("source")
    if not isinstance(value.get("surface"), str) or not _SAFE_KEY.fullmatch(value["surface"]):
        raise ValueError("surface")
    request = session.get(RecommendationRequestRow, request_id)
    item = session.get(RecommendationItemRow, (request_id, rank))
    if (
        request is None
        or request.user_id != principal.user_id
        or item is None
        or item.recording_id != recording_id
    ):
        raise _AttributionNotFound
    presentation = _uuid_optional(value, "presentation_id")
    impression = _uuid_optional_alias(
        value, "impression_event_server_id", "impression_event_local_id"
    )
    return request_id, recording_id, rank, presentation, impression


def _string(value: dict[str, Any], key: str, maximum: int) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not 1 <= len(candidate) <= maximum:
        raise SyncError("REQUEST_VALIDATION_FAILED")
    return candidate


def _enum(value: dict[str, Any], key: str, allowed: set[str]) -> str:
    candidate = _string(value, key, 100)
    if candidate not in allowed:
        raise SyncError("REQUEST_VALIDATION_FAILED")
    return candidate


def _positive(value: dict[str, Any], key: str) -> int:
    candidate = value.get(key)
    if not isinstance(candidate, int) or candidate < 1:
        raise SyncError("REQUEST_VALIDATION_FAILED")
    return candidate


def _time(value: dict[str, Any], key: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value[key]).replace("Z", "+00:00"))
    except KeyError, TypeError, ValueError:
        raise SyncError("REQUEST_VALIDATION_FAILED") from None


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _binding_response(principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "user_id": str(principal.user_id), "device_id": str(principal.device_id)}


def _error(code: str, request_id: UUID, retryable: bool = False) -> dict[str, Any]:
    return {
        "code": code,
        "message": "The event could not be applied.",
        "retryable": retryable,
        "request_id": str(request_id),
    }


def _rejected(
    event: dict[str, Any], code: str, request_id: UUID, retryable: bool = False
) -> dict[str, Any]:
    return {
        "event_id": str(event["event_id"]),
        "device_sequence": event["device_sequence"],
        "aggregate_type": event["aggregate_type"],
        "aggregate_local_id": str(event["aggregate_local_id"]),
        "aggregate_server_id": None,
        "outcome": "REJECTED",
        "error": _error(code, request_id, retryable),
    }


def _applied(event: dict[str, Any], version: int) -> dict[str, Any]:
    return {
        "event_id": str(event["event_id"]),
        "device_sequence": event["device_sequence"],
        "aggregate_type": event["aggregate_type"],
        "aggregate_local_id": str(event["aggregate_local_id"]),
        "aggregate_server_id": str(event["aggregate_id"]),
        "outcome": "APPLIED",
        "server_row_version": version,
    }


def _duplicate(ack: Any, event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ack, dict):
        raise SyncError("INBOX_CORRUPT")
    duplicate = dict(ack)
    duplicate["outcome"] = "DUPLICATE"
    duplicate["original_outcome"] = ack["outcome"]
    duplicate["event_id"] = str(event["event_id"])
    return duplicate


def _server_event(row: SyncEventRow, tombstone: TombstoneRow | None = None) -> dict[str, Any]:
    event = {
        "server_sequence": row.server_sequence,
        "event_id": str(row.event_id),
        "origin_device_id": str(row.origin_device_id) if row.origin_device_id else None,
        "event_type": row.event_type,
        "schema_version": row.schema_version,
        "aggregate_type": row.aggregate_type,
        "aggregate_server_id": str(row.aggregate_id),
        "server_row_version": row.server_row_version,
        "operation": row.operation,
        "payload": row.payload,
        "created_at": _iso(row.created_at),
    }
    if tombstone is not None:
        event["tombstone"] = {
            "tombstone_id": str(tombstone.tombstone_id),
            "deleted_by_event_id": str(tombstone.deleted_by_event_id),
            "deleted_at": _iso(tombstone.deleted_at),
            "retain_until": _iso(tombstone.retain_until),
        }
    return event


def _aggregate(row: SyncEventRow) -> dict[str, Any]:
    return {
        "aggregate_type": row.aggregate_type,
        "aggregate_server_id": str(row.aggregate_id),
        "server_row_version": row.server_row_version or 1,
        "payload": row.payload,
    }


def _bootstrap_projections(
    session: Session, user_id: UUID, *, include_catalog_artist: bool = False
) -> list[tuple[str, UUID, int, Any]]:
    """Materialize live owner projections, never replaying patch event payloads."""
    values: list[tuple[str, UUID, int, Any]] = []
    for ref_row in session.scalars(
        select(UserTrackRefRow).where(
            UserTrackRefRow.user_id == user_id, UserTrackRefRow.deleted_at.is_(None)
        )
    ):
        values.append(
            (
                "USER_TRACK_REF",
                ref_row.user_track_ref_id,
                ref_row.row_version,
                {
                    "recording_id": str(ref_row.recording_id)
                    if ref_row.recording_id is not None
                    else None,
                    "title": ref_row.raw_title,
                    "artist": ref_row.raw_artist,
                    "album": ref_row.raw_album,
                    "duration_ms": ref_row.raw_duration_ms,
                    "resolution_status": ref_row.resolution_status,
                },
            )
        )
    for entry_row in session.scalars(
        select(LibraryEntryRow).where(
            LibraryEntryRow.user_id == user_id, LibraryEntryRow.removed_at.is_(None)
        )
    ):
        values.append(
            (
                "LIBRARY_ENTRY",
                entry_row.library_entry_id,
                entry_row.row_version,
                {
                    "server_user_track_ref_id": str(entry_row.user_track_ref_id),
                    "source": entry_row.source,
                    "availability_status": entry_row.availability_status,
                },
            )
        )
    for playlist_row in session.scalars(
        select(PlaylistRow).where(
            PlaylistRow.owner_user_id == user_id, PlaylistRow.deleted_at.is_(None)
        )
    ):
        values.append(
            (
                "PLAYLIST",
                playlist_row.playlist_id,
                playlist_row.row_version,
                {
                    "name": playlist_row.name,
                    "description": playlist_row.description,
                    "visibility": playlist_row.visibility,
                    "playlist_type": playlist_row.playlist_type,
                },
            )
        )
    entries = session.scalars(
        select(PlaylistEntryRow)
        .join(PlaylistRow)
        .where(
            PlaylistRow.owner_user_id == user_id,
            PlaylistRow.deleted_at.is_(None),
            PlaylistEntryRow.removed_at.is_(None),
        )
    )
    for playlist_entry_row in entries:
        values.append(
            (
                "PLAYLIST_ENTRY",
                playlist_entry_row.playlist_entry_id,
                playlist_entry_row.row_version,
                {
                    "server_playlist_id": str(playlist_entry_row.playlist_id),
                    "server_user_track_ref_id": str(playlist_entry_row.user_track_ref_id),
                    "position_key": playlist_entry_row.position_key,
                },
            )
        )
    for tombstone in session.scalars(
        select(TombstoneRow)
        .where(TombstoneRow.user_id == user_id, TombstoneRow.retain_until > _now())
        .order_by(TombstoneRow.deleted_at, TombstoneRow.tombstone_id)
    ):
        values.append(
            (
                "TOMBSTONE",
                tombstone.tombstone_id,
                1,
                {
                    "aggregate_type": tombstone.aggregate_type,
                    "aggregate_server_id": str(tombstone.aggregate_id),
                    "deleted_by_event_id": str(tombstone.deleted_by_event_id),
                    "deleted_at": _iso(tombstone.deleted_at),
                    "retain_until": _iso(tombstone.retain_until),
                },
            )
        )
    recording_ids = list(
        session.scalars(
            select(UserTrackRefRow.recording_id).where(
                UserTrackRefRow.user_id == user_id,
                UserTrackRefRow.deleted_at.is_(None),
                UserTrackRefRow.recording_id.is_not(None),
            )
        )
    )
    if recording_ids:
        for redirect in session.scalars(
            select(RecordingRedirectRow).where(
                RecordingRedirectRow.source_recording_id.in_(recording_ids)
            )
        ):
            values.append(
                (
                    "RECORDING_REDIRECT",
                    redirect.source_recording_id,
                    1,
                    {
                        "aggregate_type": "RECORDING",
                        "alias_server_id": str(redirect.source_recording_id),
                        "canonical_server_id": str(redirect.target_recording_id),
                    },
                )
            )
    if include_catalog_artist:
        values.extend(
            (item.aggregate_type, item.aggregate_id, item.row_version, item.payload)
            for item in _catalog_artist_projections(session, user_id)
        )
    values.sort(key=lambda value: (value[0], str(value[1])))
    return values


def _capabilities(body: dict[str, Any]) -> set[str]:
    """Accept only a small additive capability vocabulary; unknown values remain inert."""
    values = body.get("capabilities", [])
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        return set()
    return {item for item in values if item == CATALOG_ARTIST_ID_V1}


def _catalog_enabled(body: dict[str, Any]) -> bool:
    return body.get("catalog_projection_version") == 1 or CATALOG_ARTIST_ID_V1 in _capabilities(
        body
    )


def _effective_capabilities(body: dict[str, Any]) -> set[str]:
    """Persist the negotiated projection, including the numeric compatibility alias."""
    capabilities = _capabilities(body)
    if body.get("catalog_projection_version") == 1:
        capabilities.add(CATALOG_ARTIST_ID_V1)
    return capabilities


def _owner_recording_pages(recording_ids: set[UUID] | list[UUID]) -> list[dict[str, Any]]:
    """Page a complete owner proof without truncating or creating an oversized payload."""
    ordered = sorted(set(recording_ids), key=str)
    if not ordered:
        raise ValueError("owner recording proof is empty")
    scope_id = hashlib.sha256("\0".join(map(str, ordered)).encode()).hexdigest()
    page_count = (len(ordered) + _OWNER_RECORDING_PAGE_SIZE - 1) // _OWNER_RECORDING_PAGE_SIZE
    return [
        {
            "owner_scope_id": scope_id,
            "owner_recording_page": page,
            "owner_recording_page_count": page_count,
            "owner_recording_ids": [
                str(value)
                for value in ordered[
                    page * _OWNER_RECORDING_PAGE_SIZE : (page + 1) * _OWNER_RECORDING_PAGE_SIZE
                ]
            ],
        }
        for page in range(page_count)
    ]


def _catalog_artist_projections(session: Session, user_id: UUID) -> list[CatalogArtistProjection]:
    """Return the catalog closure reachable from one owner's resolved tracks.

    Names are display evidence only.  The canonical UUID from catalog.artist is the
    sole Artist identity and an empty child set remains an explicit unresolved credit.
    """
    recording_ids = sorted(
        set(
            session.scalars(
                select(UserTrackRefRow.recording_id).where(
                    UserTrackRefRow.user_id == user_id,
                    UserTrackRefRow.deleted_at.is_(None),
                    UserTrackRefRow.recording_id.is_not(None),
                )
            )
        ),
        key=str,
    )
    if not recording_ids:
        return []
    recordings = list(
        session.scalars(select(RecordingRow).where(RecordingRow.recording_id.in_(recording_ids)))
    )
    credit_ids = {row.artist_credit_id for row in recordings}
    release_rows = list(
        session.execute(
            select(ReleaseRow, ReleaseTrackRow.recording_id)
            .join(MediumRow, MediumRow.release_id == ReleaseRow.release_id)
            .join(ReleaseTrackRow, ReleaseTrackRow.medium_id == MediumRow.medium_id)
            .where(ReleaseTrackRow.recording_id.in_(recording_ids))
        )
    )
    releases_by_id = {release.release_id: release for release, _ in release_rows}
    release_owner_recordings: dict[UUID, set[UUID]] = {}
    for release, recording_id in release_rows:
        release_owner_recordings.setdefault(release.release_id, set()).add(recording_id)
    releases = list(releases_by_id.values())
    credit_ids.update(row.artist_credit_id for row in releases)
    credits = list(
        session.scalars(
            select(ArtistCreditRow).where(ArtistCreditRow.artist_credit_id.in_(credit_ids))
        )
    )
    names = list(
        session.scalars(
            select(ArtistCreditNameRow)
            .where(ArtistCreditNameRow.artist_credit_id.in_(credit_ids))
            .order_by(ArtistCreditNameRow.artist_credit_id, ArtistCreditNameRow.position)
        )
    )
    names_by_credit: dict[UUID, list[ArtistCreditNameRow]] = {}
    for name in names:
        names_by_credit.setdefault(name.artist_credit_id, []).append(name)
    artist_ids = {name.artist_id for name in names}
    artists = list(session.scalars(select(ArtistRow).where(ArtistRow.artist_id.in_(artist_ids))))
    values: list[CatalogArtistProjection] = []
    for artist in artists:
        values.append(
            CatalogArtistProjection(
                "ARTIST",
                artist.artist_id,
                artist.row_version,
                {
                    "artist_id": str(artist.artist_id),
                    "name": artist.name,
                    "sort_name": artist.sort_name,
                    "artist_type": artist.artist_type,
                    "disambiguation": artist.disambiguation,
                    "country_code": artist.country_code,
                    "identity_status": artist.identity_status,
                    "deleted_at": _iso(artist.deleted_at) if artist.deleted_at else None,
                },
            )
        )
    for credit in credits:
        values.append(
            CatalogArtistProjection(
                "ARTIST_CREDIT",
                credit.artist_credit_id,
                credit.row_version,
                {
                    "artist_credit_id": str(credit.artist_credit_id),
                    "display_name": credit.display_name,
                    "names": [
                        {
                            "artist_id": str(name.artist_id),
                            "position": name.position,
                            "credited_name": name.credited_name,
                            "join_phrase": name.join_phrase,
                            "role": name.role,
                        }
                        for name in names_by_credit.get(credit.artist_credit_id, [])
                    ],
                    "deleted_at": _iso(credit.deleted_at) if credit.deleted_at else None,
                },
            )
        )
    for recording in recordings:
        for owner_page in _owner_recording_pages({recording.recording_id}):
            values.append(
                CatalogArtistProjection(
                    "RECORDING_ARTIST_CREDIT",
                    recording.recording_id,
                    recording.row_version,
                    {
                        "recording_id": str(recording.recording_id),
                        "artist_credit_id": str(recording.artist_credit_id),
                        **owner_page,
                        "deleted_at": _iso(recording.deleted_at) if recording.deleted_at else None,
                    },
                )
            )
    for release in releases:
        for owner_page in _owner_recording_pages(release_owner_recordings[release.release_id]):
            values.append(
                CatalogArtistProjection(
                    "RELEASE_ARTIST_CREDIT",
                    release.release_id,
                    release.row_version,
                    {
                        "release_id": str(release.release_id),
                        "artist_credit_id": str(release.artist_credit_id),
                        **owner_page,
                        "deleted_at": _iso(release.deleted_at) if release.deleted_at else None,
                    },
                )
            )
    for projection in values:
        _canonical_payload_bytes(projection.payload)
    return sorted(
        values,
        key=lambda value: (
            value.aggregate_type,
            str(value.aggregate_id),
            int(value.payload.get("owner_recording_page", 0)),
        ),
    )


__all__ = (
    "CATALOG_ARTIST_ID_V1",
    "CatalogArtistProjection",
    "CatalogArtistSyncPublisher",
    "OpaqueCursor",
    "SyncError",
    "SyncService",
)
