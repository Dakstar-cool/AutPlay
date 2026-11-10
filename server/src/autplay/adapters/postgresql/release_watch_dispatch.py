"""Scheduled, bounded bridge from owner watches to safe A1C scan policies."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from autplay.application.discovery_automation import DiscoveryAutomationError
from autplay.application.discovery_scan_schedule import validate_interval
from autplay.domain.release_watch import ALGORITHM_VERSION

from .discovery_runtime import JAMENDO_PROVIDER_ID
from .models import ArtistPolicyRevisionRow, ArtistPolicyRow
from .watch_auto_download_authority import (
    is_watch_policy,
    require_effective_watch,
    watch_policy_id,
)

__all__ = (
    "is_watch_policy",
    "reconcile_watch_policies",
    "require_effective_watch",
    "watch_policy_id",
)


_ELIGIBLE = text("""
WITH watched AS (
 SELECT c.user_id,c.artist_id,c.updated_at AS eligible_since
 FROM library.artist_watch_choice c WHERE c.manual_follow AND NOT c.explicit_exclusion
 UNION ALL
 SELECT f.user_id,f.artist_id,f.projected_at AS eligible_since
 FROM library.favorite_artist_projection f
 JOIN library.favorite_artist_refresh r ON r.user_id=f.user_id
   AND r.generation=f.generation AND r.algorithm_version=f.algorithm_version
   AND r.algorithm_version=:algorithm
   AND f.projected_at<=r.refreshed_at
   AND r.refreshed_at<=clock_timestamp()
   AND r.refreshed_at>clock_timestamp()-INTERVAL '24 hours'
 WHERE NOT EXISTS(SELECT 1 FROM library.artist_watch_choice c
   WHERE c.user_id=f.user_id AND c.artist_id=f.artist_id
     AND (c.manual_follow OR c.explicit_exclusion))
)
SELECT w.user_id,w.artist_id,w.eligible_since,m.external_id
FROM watched w
JOIN account.user_account owner ON owner.user_id=w.user_id
 AND owner.status='ACTIVE' AND owner.deleted_at IS NULL
JOIN catalog.artist artist ON artist.artist_id=w.artist_id
 AND artist.identity_status='ACTIVE' AND artist.deleted_at IS NULL
JOIN identity.source_provider provider ON provider.provider_id=:provider
 AND provider.enabled AND provider.deleted_at IS NULL
 AND provider.adapter_id='autplay.jamendo.manual' AND provider.adapter_version='1.0.0'
 AND provider.capabilities @> ARRAY['SEARCH','DOWNLOAD','RELEASE_WATCH']::text[]
JOIN LATERAL (
 SELECT min(ext.external_id) AS external_id,count(*) AS binding_count
 FROM identity.external_reference ext
 WHERE ext.provider_id=:provider AND ext.artist_id=w.artist_id
   AND ext.external_entity_type='ARTIST' AND ext.market_scope='GLOBAL'
   AND ext.deleted_at IS NULL
) m ON m.binding_count=1 AND m.external_id ~ '^[0-9]{1,20}$'
WHERE NOT EXISTS(SELECT 1 FROM discovery.artist_policy p
 WHERE p.user_id=w.user_id AND p.canonical_artist_id=w.artist_id)
ORDER BY w.eligible_since,w.user_id,w.artist_id
LIMIT :limit
""")


def reconcile_watch_policies(
    session: Session, *, now: datetime, limit: int, interval_hours: int
) -> int:
    """Called only by periodic dispatch, never by a follow/listening command."""
    if not 1 <= limit <= 20:
        raise ValueError("watch reconciliation limit is invalid")
    candidates = session.execute(
        _ELIGIBLE,
        {"algorithm": ALGORITHM_VERSION, "provider": JAMENDO_PROVIDER_ID, "limit": limit},
    ).all()
    created = 0
    for owner_id, artist_id, eligible_since, provider_artist_id in candidates:
        # Follow and favorite refresh take FOR UPDATE on the owner row. This share
        # lock serializes the final authority check with either mutation.
        try:
            eligible_since = require_effective_watch(
                session,
                owner_id,
                artist_id,
                provider_artist_id,
                provider_id=JAMENDO_PROVIDER_ID,
            )
        except DiscoveryAutomationError as error:
            if error.code not in {"discovery_not_eligible", "identity_review_required"}:
                raise
            continue
        existing = session.scalar(
            select(ArtistPolicyRow.policy_id).where(
                ArtistPolicyRow.user_id == owner_id,
                ArtistPolicyRow.canonical_artist_id == artist_id,
            )
        )
        if existing is not None:
            continue
        count = int(
            session.scalar(
                text("""
            SELECT count(*) FROM discovery.artist_policy
            WHERE automation_enabled AND discovery_mode='SCHEDULED'
        """)
            )
            or 0
        )
        try:
            validate_interval(interval_hours, scheduled_policy_count=count + 1)
        except ValueError:
            break
        policy_id = watch_policy_id(owner_id, artist_id)
        eligible_at = eligible_since + timedelta(hours=interval_hours)
        policy = ArtistPolicyRow(
            policy_id=policy_id,
            user_id=owner_id,
            canonical_artist_id=artist_id,
            provider_id=JAMENDO_PROVIDER_ID,
            provider_artist_id=provider_artist_id,
            discovery_mode="SCHEDULED",
            import_mode="REVIEW_REQUIRED",
            automation_enabled=True,
            current_revision=1,
            next_eligible_at=eligible_at,
            created_at=now,
            updated_at=now,
        )
        session.add(policy)
        session.flush([policy])
        session.add(
            ArtistPolicyRevisionRow(
                policy_id=policy_id,
                owner_user_id=owner_id,
                revision=1,
                discovery_mode="SCHEDULED",
                import_mode="REVIEW_REQUIRED",
                automation_enabled=True,
                change_kind="SAFE_DEFAULT",
                last_checked_at=None,
                next_eligible_at=eligible_at,
                created_at=now,
            )
        )
        session.flush()
        created += 1
    return created
