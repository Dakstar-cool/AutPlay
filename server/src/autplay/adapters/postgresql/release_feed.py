# ruff: noqa: E501
"""Read-only, owner-fenced release-feed projection over A1C observation evidence.

Owner-local v1 ranks two items per artist per diversity round, then release date,
manual follow, availability, inferred affinity, and candidate ID. The 90-day
window and keyset cursor bound the result without treating metadata as playable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.domain.release_watch import ALGORITHM_VERSION

FEED_WINDOW = timedelta(days=90)


@dataclass(frozen=True, slots=True)
class ReleaseFeedItem:
    candidate_id: UUID
    canonical_artist_id: UUID
    artist_name: str
    title: str
    released_at: datetime
    reason: str
    state: str
    score_milli: int
    release_id: UUID | None
    recording_id: UUID | None
    user_track_ref_id: UUID | None
    provider_id: UUID
    evidence_run_id: UUID
    evidence_adapter_version: str
    diversity_round: int
    affinity_rank: int
    availability_rank: int


@dataclass(frozen=True, slots=True)
class FeedPosition:
    diversity_round: int
    released_at: datetime
    affinity_rank: int
    availability_rank: int
    score_milli: int
    candidate_id: UUID


_QUERY = text("""
WITH selected AS (
    SELECT artist_id FROM library.artist_watch_choice
    WHERE user_id=:owner AND manual_follow
    UNION
    SELECT f.artist_id FROM library.favorite_artist_projection f
    JOIN library.favorite_artist_refresh r ON r.user_id=f.user_id
        AND r.generation=f.generation AND r.algorithm_version=f.algorithm_version
        AND r.algorithm_version=:algorithm
        AND f.projected_at<=r.refreshed_at
        AND r.refreshed_at<=clock_timestamp()
        AND r.refreshed_at>clock_timestamp()-INTERVAL '24 hours'
    WHERE f.user_id=:owner
), watched AS (
    SELECT a.artist_id, a.name AS artist_name,
           CASE WHEN COALESCE(c.manual_follow, false) THEN 'MANUAL_FOLLOW'
                ELSE 'LISTENING_HISTORY' END AS reason,
           CASE WHEN COALESCE(c.manual_follow, false) THEN 1 ELSE 0 END AS affinity_rank,
           COALESCE(f.score_milli, 0) AS score_milli
    FROM selected s
    JOIN catalog.artist a ON a.artist_id=s.artist_id
    JOIN account.user_account owner ON owner.user_id=:owner
        AND owner.status='ACTIVE' AND owner.deleted_at IS NULL
    LEFT JOIN library.artist_watch_choice c ON c.user_id=:owner AND c.artist_id=a.artist_id
    LEFT JOIN library.favorite_artist_projection f ON f.user_id=:owner
        AND f.artist_id=a.artist_id
        AND EXISTS (SELECT 1 FROM library.favorite_artist_refresh r
                    WHERE r.user_id=f.user_id AND r.generation=f.generation
                      AND r.algorithm_version=f.algorithm_version
                      AND r.algorithm_version=:algorithm
                      AND f.projected_at<=r.refreshed_at
                      AND r.refreshed_at<=clock_timestamp()
                      AND r.refreshed_at>clock_timestamp()-INTERVAL '24 hours')
    WHERE a.identity_status='ACTIVE' AND a.deleted_at IS NULL
      AND NOT COALESCE(c.explicit_exclusion, false)
), observed AS (
    SELECT c.*, w.artist_name, w.reason, w.affinity_rank, w.score_milli,
           r.run_id AS evidence_run_id, r.adapter_version AS evidence_adapter_version,
           row_number() OVER (PARTITION BY c.candidate_id
             ORDER BY r.due_slot_at DESC, r.run_id DESC) AS observation_order
    FROM discovery.candidate c
    JOIN watched w ON w.artist_id=c.canonical_artist_id
    JOIN discovery.run_candidate rc ON rc.candidate_id=c.candidate_id
    JOIN discovery.run r ON r.run_id=rc.run_id AND r.user_id=:owner
        AND r.provider_id=c.provider_id AND r.provider_artist_id=c.provider_artist_id
        AND r.state IN ('PARTIAL','COMPLETED','RETRY_WAIT','FAILED_TERMINAL')
    JOIN discovery.artist_policy p ON p.policy_id=r.policy_id AND p.user_id=:owner
        AND p.canonical_artist_id=c.canonical_artist_id AND p.provider_id=c.provider_id
        AND p.provider_artist_id=c.provider_artist_id
    WHERE c.user_id=:owner AND c.released_at IS NOT NULL
      AND c.released_at BETWEEN :window_start AND :as_of
      AND c.created_at<=:as_of AND r.created_at<=:as_of
      AND r.adapter_id='autplay.jamendo.manual' AND r.adapter_version='1.0.0'
      AND EXISTS (SELECT 1 FROM discovery.run_page page WHERE page.run_id=r.run_id)
), evidence AS (
    SELECT o.*, provider.enabled AS provider_enabled,
           release_match.release_id AS canonical_release_id,
           EXISTS (
             SELECT 1 FROM identity.external_reference ext
             WHERE ext.external_reference_id=o.external_reference_id
               AND ext.provider_id=o.provider_id AND ext.market_scope=o.market_scope
               AND ext.external_entity_type='TRACK' AND ext.external_id=o.provider_track_id
               AND ext.recording_id=o.recording_id AND ext.deleted_at IS NULL
           ) AS recording_evidence,
           EXISTS (
             SELECT 1 FROM catalog.recording rec
             JOIN catalog.artist_credit_name credit ON credit.artist_credit_id=rec.artist_credit_id
                 AND credit.artist_id=o.canonical_artist_id AND credit.role='PRIMARY'
             WHERE rec.recording_id=o.recording_id AND rec.identity_status='ACTIVE'
               AND rec.deleted_at IS NULL
           ) AS canonical_recording,
           EXISTS (
             SELECT 1 FROM discovery.source_authorization auth
             WHERE auth.authorization_id=o.source_authorization_id
               AND auth.revision=o.source_authorization_revision
               AND auth.user_id=:owner AND auth.provider_id=o.provider_id
               AND auth.canonical_artist_id=o.canonical_artist_id
               AND auth.market_scope=o.market_scope
               AND auth.adapter_id='autplay.jamendo.manual'
               AND auth.adapter_version='1.0.0'
               AND auth.rights_capability='AUTHORIZED_DOWNLOAD'
               AND auth.revoked_at IS NULL AND auth.expires_at>:authority_now
               AND ((auth.purpose='MANUAL'
                     AND auth.policy_id IS NULL AND auth.policy_revision IS NULL
                     AND auth.policy_reference='ADR-034:a1b-manual:1') OR (
                   auth.purpose='AUTO_IMPORT' AND auth.policy_id=o.policy_id
                   AND auth.policy_revision=o.policy_revision
                   AND auth.policy_reference='ADR-042:a1c-auto-import:1'
                   AND EXISTS (SELECT 1 FROM discovery.artist_policy current_policy
                     WHERE current_policy.policy_id=auth.policy_id
                       AND current_policy.user_id=:owner
                       AND current_policy.current_revision=auth.policy_revision
                       AND current_policy.import_mode='AUTO_IMPORT'
                       AND current_policy.discovery_mode='SCHEDULED'
                       AND current_policy.automation_enabled)))
           ) AS current_rights,
           EXISTS (
             SELECT 1 FROM library.user_track_ref ref
             JOIN library.library_entry entry ON entry.library_entry_id=o.library_entry_id
               AND entry.user_id=:owner AND entry.user_track_ref_id=ref.user_track_ref_id
               AND entry.removed_at IS NULL AND entry.availability_status='VAULT'
             JOIN vault.audio_variant variant ON variant.audio_variant_id=o.audio_variant_id
               AND variant.recording_id=o.recording_id
               AND variant.validation_status='VALID' AND variant.deleted_at IS NULL
             JOIN vault.vault_object object ON object.vault_object_id=variant.vault_object_id
               AND object.commit_status='COMMITTED'
             JOIN vault.vault_replica replica ON replica.vault_object_id=object.vault_object_id
               AND replica.replica_status='AVAILABLE'
             JOIN vault.acquisition_record acquired ON acquired.audio_variant_id=variant.audio_variant_id
               AND acquired.provider_id=o.provider_id
               AND acquired.external_reference_id=o.external_reference_id
               AND acquired.authorized_by_user_id=:owner
               AND acquired.rights_capability='AUTHORIZED_DOWNLOAD'
             WHERE ref.user_track_ref_id=o.user_track_ref_id AND ref.user_id=:owner
               AND ref.recording_id=o.recording_id AND ref.resolution_status='RESOLVED'
               AND ref.deleted_at IS NULL
           ) AS owner_materialized
    FROM observed o
    JOIN identity.source_provider provider ON provider.provider_id=o.provider_id
       AND provider.deleted_at IS NULL AND provider.adapter_id='autplay.jamendo.manual'
       AND provider.adapter_version='1.0.0' AND 'RELEASE_WATCH'=ANY(provider.capabilities)
    LEFT JOIN LATERAL (
       SELECT rel.release_id FROM catalog.release_track track
       JOIN catalog.recording rec ON rec.recording_id=track.recording_id
           AND rec.identity_status='ACTIVE' AND rec.deleted_at IS NULL
       JOIN catalog.artist_credit_name credit ON credit.artist_credit_id=rec.artist_credit_id
           AND credit.artist_id=o.canonical_artist_id AND credit.role='PRIMARY'
       JOIN catalog.medium medium ON medium.medium_id=track.medium_id
           AND medium.deleted_at IS NULL
       JOIN catalog.release rel ON rel.release_id=medium.release_id
           AND rel.deleted_at IS NULL AND rel.status='OFFICIAL'
       JOIN catalog.artist_credit_name release_credit
           ON release_credit.artist_credit_id=rel.artist_credit_id
           AND release_credit.artist_id=o.canonical_artist_id
           AND release_credit.role='PRIMARY'
       WHERE track.recording_id=o.recording_id AND track.deleted_at IS NULL
       ORDER BY rel.release_date DESC NULLS LAST, rel.release_id
       LIMIT 1
    ) release_match ON true
    WHERE o.observation_order=1
), classified AS (
    SELECT e.*,
        CASE
          WHEN NOT e.provider_enabled OR e.disposition='UNAVAILABLE' THEN 'UNAVAILABLE'
          WHEN e.disposition='IGNORED' THEN 'UNAVAILABLE'
          WHEN e.disposition='IDENTITY_REVIEW_REQUIRED' THEN 'REVIEW_REQUIRED'
          WHEN e.acquisition_state='READY' AND e.current_rights
               AND e.recording_evidence AND e.canonical_recording AND e.owner_materialized
               THEN 'READY'
          WHEN e.acquisition_state='READY' THEN 'REVIEW_REQUIRED'
          WHEN e.acquisition_state IN ('FAILED_TERMINAL','RETRY_WAIT','CANCELLED')
               THEN 'FAILED'
          WHEN e.acquisition_state IN ('QUEUED','ACQUIRING','INGESTING','MATERIALIZING')
               AND e.current_rights THEN 'ACQUIRING'
          WHEN e.acquisition_state IN ('QUEUED','ACQUIRING','INGESTING','MATERIALIZING')
               THEN 'UNAVAILABLE'
          ELSE 'DISCOVERED'
        END AS state,
        'artist:' || e.canonical_artist_id::text || ':' ||
        CASE WHEN e.recording_evidence AND e.canonical_recording
             THEN COALESCE('release:' || e.canonical_release_id::text,
                           'recording:' || e.recording_id::text)
             ELSE 'provider:' || e.provider_id::text || ':' || e.market_scope || ':' || e.provider_track_id
        END AS dedup_key
    FROM evidence e
), ranked_identity AS (
    SELECT c.*, CASE c.state WHEN 'READY' THEN 5 WHEN 'ACQUIRING' THEN 4
                    WHEN 'DISCOVERED' THEN 3 WHEN 'REVIEW_REQUIRED' THEN 2
                    WHEN 'FAILED' THEN 1 ELSE 0 END AS availability_rank,
           row_number() OVER (PARTITION BY c.dedup_key
             ORDER BY CASE c.state WHEN 'READY' THEN 5 WHEN 'ACQUIRING' THEN 4
                       WHEN 'DISCOVERED' THEN 3 WHEN 'REVIEW_REQUIRED' THEN 2
                       WHEN 'FAILED' THEN 1 ELSE 0 END DESC,
                      c.released_at DESC, c.candidate_id) AS identity_order
    FROM classified c
), diverse AS (
    SELECT d.*, (row_number() OVER (PARTITION BY d.canonical_artist_id
      ORDER BY d.released_at DESC, d.affinity_rank DESC, d.availability_rank DESC,
               d.score_milli DESC, d.candidate_id) - 1) / 2 AS diversity_round
    FROM ranked_identity d WHERE d.identity_order=1
)
SELECT candidate_id, canonical_artist_id, artist_name, title, released_at, reason,
       state, score_milli,
       CASE WHEN recording_evidence AND canonical_recording
            THEN canonical_release_id ELSE NULL END AS safe_release_id,
       CASE WHEN state='READY' THEN recording_id ELSE NULL END AS ready_recording_id,
       CASE WHEN state='READY' THEN user_track_ref_id ELSE NULL END AS ready_user_track_ref_id,
       provider_id, evidence_run_id, evidence_adapter_version, diversity_round,
       affinity_rank, availability_rank
FROM diverse
WHERE CAST(:after_round AS bigint) IS NULL OR
      (diversity_round, -extract(epoch FROM released_at), -affinity_rank,
       -availability_rank, -score_milli, candidate_id) >
      (CAST(:after_round AS bigint),
       -extract(epoch FROM CAST(:after_released_at AS timestamptz)),
       -CAST(:after_affinity AS integer), -CAST(:after_availability AS integer),
       -CAST(:after_score AS integer), CAST(:after_candidate AS uuid))
ORDER BY diversity_round ASC, released_at DESC, affinity_rank DESC,
         availability_rank DESC, score_milli DESC, candidate_id ASC
LIMIT :limit
""")


class ReleaseFeedRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_page(
        self,
        owner_id: UUID,
        *,
        as_of: datetime,
        authority_now: datetime | None = None,
        limit: int,
        after: FeedPosition | None = None,
    ) -> list[ReleaseFeedItem]:
        if (
            as_of.tzinfo is None
            or (authority_now is not None and authority_now.tzinfo is None)
            or not 1 <= limit <= 51
        ):
            raise ValueError("invalid release-feed page")
        rows = self._session.execute(
            _QUERY,
            {
                "owner": owner_id,
                "algorithm": ALGORITHM_VERSION,
                "window_start": as_of - FEED_WINDOW,
                "as_of": as_of,
                "authority_now": authority_now or as_of,
                "limit": limit,
                "after_round": None if after is None else after.diversity_round,
                "after_released_at": None if after is None else after.released_at,
                "after_affinity": None if after is None else after.affinity_rank,
                "after_availability": None if after is None else after.availability_rank,
                "after_score": None if after is None else after.score_milli,
                "after_candidate": None if after is None else str(after.candidate_id),
            },
        ).all()
        return [ReleaseFeedItem(*row) for row in rows]
