"""Owner-fenced persistence for manual watches and inferred favorite artists."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.domain.auth import OwnedObjectNotFoundError
from autplay.domain.release_watch import (
    ALGORITHM_VERSION,
    EVIDENCE_WINDOW,
    MAX_EVIDENCE_EVENTS,
    REFRESH_INTERVAL,
    ListeningEvidence,
    evidence_digest,
    project_favorites,
)

from .discovery_runtime import JAMENDO_PROVIDER_ID
from .watch_auto_download_authority import watch_policy_id
from .watch_auto_download_consent import WatchAutoDownloadRepository


@dataclass(frozen=True, slots=True)
class ArtistAutoDownload:
    watch_policy_id: UUID | None
    provider_artist_id: str | None
    policy_revision: int | None
    policy_import_mode: str | None
    effective_opt_in: bool
    execution_enabled: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ArtistWatch:
    artist_id: UUID
    reason: str
    score_milli: int
    favorite_generation: int | None
    auto_download: ArtistAutoDownload = ArtistAutoDownload(
        None, None, None, None, False, False, "WATCH_POLICY_PENDING"
    )


@dataclass(frozen=True, slots=True)
class FavoriteRefresh:
    generation: int
    evidence_event_count: int
    favorite_count: int
    refreshed_at: datetime


class FavoriteEvidenceOverflow(ValueError):
    """A complete projection cannot be published within the evidence budget."""


@dataclass(frozen=True, slots=True)
class WatchMonitoringStatus:
    artist_id: UUID
    state: str
    provider_artist_id: str | None


class ReleaseWatchRepository:
    """A caller supplies one transaction; every read/write is scoped to owner ID."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _lock_active_owner(self, owner_id: UUID) -> None:
        found = self._session.scalar(
            text("""
                SELECT user_id FROM account.user_account
                WHERE user_id=:owner AND status='ACTIVE' AND deleted_at IS NULL
                FOR UPDATE
            """),
            {"owner": owner_id},
        )
        if found is None:
            raise OwnedObjectNotFoundError()

    def _require_artist(self, artist_id: UUID) -> None:
        found = self._session.scalar(
            text("""
                SELECT artist_id FROM catalog.artist
                WHERE artist_id=:artist AND identity_status='ACTIVE' AND deleted_at IS NULL
            """),
            {"artist": artist_id},
        )
        if found is None:
            raise OwnedObjectNotFoundError()

    def set_manual_follow(
        self, owner_id: UUID, artist_id: UUID, *, follow: bool, now: datetime
    ) -> int:
        """Unfollow records an exclusion so the next favorite refresh cannot re-add it."""
        self._lock_active_owner(owner_id)
        self._require_artist(artist_id)
        return int(
            self._session.scalar(
                text("""
                    INSERT INTO library.artist_watch_choice
                        (user_id,artist_id,manual_follow,explicit_exclusion,created_at,updated_at)
                    VALUES (:owner,:artist,:follow,:excluded,:now,:now)
                    ON CONFLICT (user_id,artist_id) DO UPDATE SET
                        manual_follow=EXCLUDED.manual_follow,
                        explicit_exclusion=EXCLUDED.explicit_exclusion,
                        revision=artist_watch_choice.revision+1,
                        updated_at=EXCLUDED.updated_at
                    WHERE (artist_watch_choice.manual_follow,
                           artist_watch_choice.explicit_exclusion)
                       IS DISTINCT FROM (EXCLUDED.manual_follow,EXCLUDED.explicit_exclusion)
                    RETURNING revision
                """),
                {
                    "owner": owner_id,
                    "artist": artist_id,
                    "follow": follow,
                    "excluded": not follow,
                    "now": now,
                },
            )
            or self._session.scalar(
                text("""
                    SELECT revision FROM library.artist_watch_choice
                    WHERE user_id=:owner AND artist_id=:artist
                """),
                {"owner": owner_id, "artist": artist_id},
            )
        )

    def list_effective(
        self, owner_id: UUID, *, limit: int = 100, after_artist_id: UUID | None = None
    ) -> list[ArtistWatch]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be 1..200")
        rows = self._session.execute(
            text("""
                WITH selected AS (
                    SELECT artist_id FROM library.artist_watch_choice
                    WHERE user_id=:owner AND manual_follow
                    UNION
                    SELECT p.artist_id FROM library.favorite_artist_projection p
                    JOIN library.favorite_artist_refresh r ON r.user_id=p.user_id
                        AND r.generation=p.generation
                        AND r.algorithm_version=p.algorithm_version
                    WHERE p.user_id=:owner AND r.algorithm_version=:algorithm
                        AND p.projected_at<=r.refreshed_at
                        AND r.refreshed_at<=clock_timestamp()
                        AND r.refreshed_at>clock_timestamp()-INTERVAL '24 hours'
                )
                SELECT s.artist_id, COALESCE(c.manual_follow,false) AS manual_follow,
                       COALESCE(p.score_milli,0) AS score_milli,
                       p.generation, wp.policy_id,wp.provider_artist_id,
                       wp.current_revision,wp.import_mode,wp.discovery_mode,
                       wp.automation_enabled,first.change_kind
                FROM selected s
                JOIN account.user_account owner ON owner.user_id=:owner
                    AND owner.status='ACTIVE' AND owner.deleted_at IS NULL
                JOIN catalog.artist a ON a.artist_id=s.artist_id
                    AND a.identity_status='ACTIVE' AND a.deleted_at IS NULL
                LEFT JOIN library.artist_watch_choice c
                    ON c.user_id=:owner AND c.artist_id=s.artist_id
                LEFT JOIN library.favorite_artist_projection p
                    ON p.user_id=:owner AND p.artist_id=s.artist_id
                    AND EXISTS(SELECT 1 FROM library.favorite_artist_refresh r
                      WHERE r.user_id=p.user_id AND r.generation=p.generation
                        AND r.algorithm_version=p.algorithm_version
                        AND r.algorithm_version=:algorithm
                        AND p.projected_at<=r.refreshed_at
                        AND r.refreshed_at<=clock_timestamp()
                        AND r.refreshed_at>clock_timestamp()-INTERVAL '24 hours')
                LEFT JOIN discovery.artist_policy wp
                    ON wp.user_id=:owner AND wp.canonical_artist_id=s.artist_id
                LEFT JOIN discovery.artist_policy_revision first
                    ON first.policy_id=wp.policy_id AND first.revision=1
                WHERE NOT COALESCE(c.explicit_exclusion,false)
                  AND (CAST(:after_id AS uuid) IS NULL OR s.artist_id > CAST(:after_id AS uuid))
                ORDER BY s.artist_id LIMIT :limit
            """),
            {
                "owner": owner_id,
                "after_id": after_artist_id,
                "limit": limit,
                "algorithm": ALGORITHM_VERSION,
            },
        ).all()
        owner_auto = WatchAutoDownloadRepository(self._session).get(owner_id)

        def auto_download(
            artist_id: UUID,
            policy_id: UUID | None,
            provider_artist_id: str | None,
            revision: int | None,
            import_mode: str | None,
            discovery_mode: str | None,
            automation_enabled: bool | None,
            origin: str | None,
        ) -> ArtistAutoDownload:
            watch_origin = (
                policy_id == watch_policy_id(owner_id, artist_id) and origin == "SAFE_DEFAULT"
            )
            policy_ready = bool(
                watch_origin
                and import_mode == "AUTO_IMPORT"
                and discovery_mode == "SCHEDULED"
                and automation_enabled
            )
            effective = policy_ready and owner_auto.enabled
            if not watch_origin:
                reason = "WATCH_POLICY_PENDING"
            elif not policy_ready:
                reason = "ARTIST_REVIEW_REQUIRED"
            elif not owner_auto.execution_enabled:
                reason = "STAGED_GATE_OFF"
            elif not owner_auto.enabled:
                reason = "OWNER_CONSENT_OFF"
            else:
                reason = "READY"
            return ArtistAutoDownload(
                policy_id if watch_origin else None,
                provider_artist_id if watch_origin else None,
                revision if watch_origin else None,
                import_mode if watch_origin else None,
                effective,
                effective and owner_auto.execution_enabled,
                reason,
            )

        return [
            ArtistWatch(
                artist_id=artist_id,
                reason="MANUAL_FOLLOW" if manual else "LISTENING_HISTORY",
                score_milli=score,
                favorite_generation=generation,
                auto_download=auto_download(
                    artist_id,
                    policy_id,
                    provider_artist_id,
                    revision,
                    import_mode,
                    discovery_mode,
                    automation_enabled,
                    origin,
                ),
            )
            for (
                artist_id,
                manual,
                score,
                generation,
                policy_id,
                provider_artist_id,
                revision,
                import_mode,
                discovery_mode,
                automation_enabled,
                origin,
            ) in rows
        ]

    def list_exclusions(
        self, owner_id: UUID, *, limit: int = 100, after_artist_id: UUID | None = None
    ) -> list[UUID]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be 1..200")
        return list(
            self._session.scalars(
                text("""
                    SELECT c.artist_id FROM library.artist_watch_choice c
                    JOIN account.user_account owner ON owner.user_id=c.user_id
                        AND owner.status='ACTIVE' AND owner.deleted_at IS NULL
                    WHERE c.user_id=:owner AND c.explicit_exclusion
                      AND (CAST(:after_id AS uuid) IS NULL
                           OR c.artist_id > CAST(:after_id AS uuid))
                    ORDER BY c.artist_id LIMIT :limit
                """),
                {"owner": owner_id, "after_id": after_artist_id, "limit": limit},
            )
        )

    def monitoring_status(self, owner_id: UUID, artist_id: UUID) -> WatchMonitoringStatus:
        """Explain unavailable identity/provider states without guessing from a name."""
        row = self._session.execute(
            text("""
                SELECT COALESCE(c.manual_follow,false),
                       COALESCE(c.explicit_exclusion,false),
                       EXISTS(SELECT 1 FROM library.favorite_artist_projection f
                         JOIN library.favorite_artist_refresh r ON r.user_id=f.user_id
                           AND r.generation=f.generation
                           AND r.algorithm_version=f.algorithm_version
                         WHERE f.user_id=:owner AND f.artist_id=:artist
                           AND r.algorithm_version=:algorithm
                           AND f.projected_at<=r.refreshed_at
                           AND r.refreshed_at<=clock_timestamp()
                           AND r.refreshed_at>clock_timestamp()-INTERVAL '24 hours'),
                       a.identity_status,a.deleted_at
                FROM catalog.artist a
                JOIN account.user_account owner ON owner.user_id=:owner
                  AND owner.status='ACTIVE' AND owner.deleted_at IS NULL
                LEFT JOIN library.artist_watch_choice c
                  ON c.user_id=:owner AND c.artist_id=a.artist_id
                WHERE a.artist_id=:artist
            """),
            {"owner": owner_id, "artist": artist_id, "algorithm": ALGORITHM_VERSION},
        ).one_or_none()
        if row is None:
            raise OwnedObjectNotFoundError()
        manual, excluded, inferred, identity_status, deleted_at = row
        if excluded:
            return WatchMonitoringStatus(artist_id, "EXCLUDED", None)
        if not manual and not inferred:
            return WatchMonitoringStatus(artist_id, "NOT_WATCHED", None)
        if identity_status != "ACTIVE" or deleted_at is not None:
            return WatchMonitoringStatus(artist_id, "ARTIST_UNAVAILABLE", None)
        provider = self._session.scalar(
            text("""
                SELECT EXISTS(SELECT 1 FROM identity.source_provider
                 WHERE provider_id=:provider AND enabled AND deleted_at IS NULL
                   AND adapter_id='autplay.jamendo.manual' AND adapter_version='1.0.0'
                   AND capabilities @> ARRAY['SEARCH','DOWNLOAD','RELEASE_WATCH']::text[])
            """),
            {"provider": JAMENDO_PROVIDER_ID},
        )
        if not provider:
            return WatchMonitoringStatus(artist_id, "PROVIDER_UNAVAILABLE", None)
        mappings = self._session.scalars(
            text("""
                SELECT external_id FROM identity.external_reference
                WHERE provider_id=:provider AND artist_id=:artist
                  AND external_entity_type='ARTIST' AND market_scope='GLOBAL'
                  AND deleted_at IS NULL
            """),
            {"provider": JAMENDO_PROVIDER_ID, "artist": artist_id},
        ).all()
        if not mappings:
            return WatchMonitoringStatus(artist_id, "PROVIDER_MAPPING_UNAVAILABLE", None)
        if len(mappings) != 1 or re.fullmatch(r"[0-9]{1,20}", mappings[0]) is None:
            return WatchMonitoringStatus(artist_id, "IDENTITY_REVIEW_REQUIRED", None)
        policy_mode = self._session.scalar(
            text("""
                SELECT discovery_mode FROM discovery.artist_policy
                WHERE user_id=:owner AND canonical_artist_id=:artist
            """),
            {"owner": owner_id, "artist": artist_id},
        )
        state = (
            "ELIGIBLE_NEXT_PASS"
            if policy_mode is None
            else "SCHEDULED"
            if policy_mode == "SCHEDULED"
            else "AUTOMATIC_CHECK_DISABLED"
        )
        return WatchMonitoringStatus(artist_id, state, mappings[0])

    def refresh_favorites(self, owner_id: UUID, *, now: datetime) -> FavoriteRefresh:
        """Scheduled refresh; no provider scan, acquisition, or job enqueue occurs."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone aware")
        self._lock_active_owner(owner_id)
        previous = self._session.execute(
            text("""
                SELECT generation,algorithm_version,evidence_event_count,refreshed_at
                FROM library.favorite_artist_refresh WHERE user_id=:owner
            """),
            {"owner": owner_id},
        ).one_or_none()
        if (
            previous is not None
            and previous.algorithm_version == ALGORITHM_VERSION
            and now - REFRESH_INTERVAL < previous.refreshed_at <= now
        ):
            count = self._session.scalar(
                text("""
                    SELECT count(*) FROM library.favorite_artist_projection
                    WHERE user_id=:owner AND generation=:generation
                """),
                {"owner": owner_id, "generation": previous.generation},
            )
            return FavoriteRefresh(
                previous.generation,
                previous.evidence_event_count,
                int(count or 0),
                previous.refreshed_at,
            )
        rows = self._session.execute(
            text("""
                SELECT DISTINCT e.listening_event_id,n.artist_id,e.recording_id,e.started_at
                FROM library.listening_event e
                JOIN library.user_track_ref ref ON ref.user_track_ref_id=e.user_track_ref_id
                    AND ref.user_id=e.user_id
                JOIN catalog.recording recording ON recording.recording_id=e.recording_id
                    AND recording.identity_status='ACTIVE' AND recording.deleted_at IS NULL
                JOIN catalog.artist_credit_name n
                    ON n.artist_credit_id=recording.artist_credit_id AND n.role='PRIMARY'
                JOIN catalog.artist artist ON artist.artist_id=n.artist_id
                    AND artist.identity_status='ACTIVE' AND artist.deleted_at IS NULL
                WHERE e.user_id=:owner AND e.started_at>=:start AND e.started_at<=:now
                  AND e.recording_id IS NOT NULL AND e.played_ms>=30000
                  AND (e.completion_ratio IS NULL OR e.completion_ratio>=0.5)
                  AND NOT e.excluded_from_taste AND e.explicit_feedback<>'DISLIKE'
                  -- Release Watch also runs on the V1-only sync schema.
                  -- If V2 ingress is present, keep its private events excluded.
                  AND NOT EXISTS (
                    SELECT 1 FROM sync.device_event_inbox inbox
                    WHERE inbox.event_id=e.listening_event_id
                      AND to_jsonb(inbox)->>'ingress_validation_version'='2'
                  )
                ORDER BY e.started_at,e.listening_event_id,n.artist_id,e.recording_id
                LIMIT :fetch_limit
            """),
            {
                "owner": owner_id,
                "start": now - EVIDENCE_WINDOW,
                "now": now,
                "fetch_limit": MAX_EVIDENCE_EVENTS + 1,
            },
        ).all()
        if len(rows) > MAX_EVIDENCE_EVENTS:
            raise FavoriteEvidenceOverflow("favorite evidence exceeds bounded refresh")
        evidence = [ListeningEvidence(*row) for row in rows]
        favorites = project_favorites(evidence, as_of=now)
        generation = 1 if previous is None else previous.generation + 1
        event_count = len({item.event_id for item in evidence})
        first_projected: dict[UUID, datetime] = {
            artist_id: projected_at
            for artist_id, projected_at in self._session.execute(
                text("""
                    SELECT p.artist_id,p.projected_at FROM library.favorite_artist_projection p
                    JOIN library.favorite_artist_refresh r ON r.user_id=p.user_id
                      AND r.generation=p.generation AND r.algorithm_version=p.algorithm_version
                    WHERE p.user_id=:owner AND r.algorithm_version=:algorithm
                      AND p.projected_at<=r.refreshed_at AND r.refreshed_at<=:now
                """),
                {"owner": owner_id, "algorithm": ALGORITHM_VERSION, "now": now},
            ).all()
        }
        self._session.execute(
            text("""
                INSERT INTO library.favorite_artist_refresh
                    (user_id,generation,algorithm_version,evidence_window_start,
                     evidence_window_end,evidence_event_count,evidence_sha256,refreshed_at)
                VALUES (:owner,:generation,:algorithm,:start,:now,:count,:digest,:now)
                ON CONFLICT (user_id) DO UPDATE SET
                    generation=EXCLUDED.generation,algorithm_version=EXCLUDED.algorithm_version,
                    evidence_window_start=EXCLUDED.evidence_window_start,
                    evidence_window_end=EXCLUDED.evidence_window_end,
                    evidence_event_count=EXCLUDED.evidence_event_count,
                    evidence_sha256=EXCLUDED.evidence_sha256,refreshed_at=EXCLUDED.refreshed_at
            """),
            {
                "owner": owner_id,
                "generation": generation,
                "algorithm": ALGORITHM_VERSION,
                "start": now - EVIDENCE_WINDOW,
                "now": now,
                "count": event_count,
                "digest": evidence_digest(evidence),
            },
        )
        self._session.execute(
            text("DELETE FROM library.favorite_artist_projection WHERE user_id=:owner"),
            {"owner": owner_id},
        )
        for favorite in favorites:
            self._session.execute(
                text("""
                    INSERT INTO library.favorite_artist_projection
                        (user_id,artist_id,generation,algorithm_version,score_milli,
                         qualified_play_count,distinct_recording_count,last_played_at,
                         evidence_sha256,projected_at)
                    VALUES (:owner,:artist,:generation,:algorithm,:score,:plays,:recordings,
                            :last_played,:digest,:now)
                """),
                {
                    "owner": owner_id,
                    "artist": favorite.artist_id,
                    "generation": generation,
                    "algorithm": ALGORITHM_VERSION,
                    "score": favorite.score_milli,
                    "plays": favorite.qualified_play_count,
                    "recordings": favorite.distinct_recording_count,
                    "last_played": favorite.last_played_at,
                    "digest": favorite.evidence_sha256,
                    "now": first_projected.get(favorite.artist_id, now),
                },
            )
        return FavoriteRefresh(generation, event_count, len(favorites), now)
