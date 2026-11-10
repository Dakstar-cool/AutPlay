"""Current watch, operator gate, and owner consent checks for A1C lineage."""

from __future__ import annotations

from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from autplay.application.discovery_automation import DiscoveryAutomationError
from autplay.domain.release_watch import ALGORITHM_VERSION

from .models import (
    ArtistPolicyRevisionRow,
    ArtistPolicyRow,
    WatchAutoDownloadConsentRow,
    WatchAutoDownloadGateRow,
)


def watch_policy_id(owner_id: UUID, artist_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"autplay:m11-watch-policy-v1:{owner_id}:{artist_id}")


def is_watch_policy(session: Session, policy: ArtistPolicyRow) -> bool:
    """The safe-default first revision is immutable watch-origin evidence."""
    if policy.policy_id != watch_policy_id(policy.user_id, policy.canonical_artist_id):
        return False
    revision = session.get(ArtistPolicyRevisionRow, (policy.policy_id, 1))
    return revision is not None and revision.change_kind == "SAFE_DEFAULT"


def gate_state(session: Session, *, lock: bool = False) -> tuple[bool, int]:
    query = select(WatchAutoDownloadGateRow).where(WatchAutoDownloadGateRow.singleton_id == 1)
    if lock:
        query = query.with_for_update(read=True)
    row = session.scalar(query.execution_options(populate_existing=True))
    return (bool(row.enabled), row.revision) if row is not None else (False, 0)


def consent_state(
    session: Session,
    owner_id: UUID,
    *,
    gate_revision: int,
    lock: bool = False,
) -> tuple[bool, int]:
    query = select(WatchAutoDownloadConsentRow).where(
        WatchAutoDownloadConsentRow.user_id == owner_id
    )
    if lock:
        query = query.with_for_update(read=True)
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        return False, 0
    active = bool(row.enabled and row.gate_revision == gate_revision)
    return active, row.revision


def require_effective_watch(
    session: Session,
    owner_id: UUID,
    artist_id: UUID,
    provider_artist_id: str,
    *,
    provider_id: UUID,
) -> datetime:
    """Serialize unfollow/favorite revocation with a final owner share lock."""
    owner = session.scalar(
        text("""
            SELECT user_id FROM account.user_account
            WHERE user_id=:owner AND status='ACTIVE' AND deleted_at IS NULL FOR SHARE
        """),
        {"owner": owner_id},
    )
    if owner is None:
        raise DiscoveryAutomationError("discovery_not_eligible")
    eligible_since = session.scalar(
        text("""
            SELECT COALESCE(
              (SELECT c.updated_at FROM library.artist_watch_choice c
               WHERE c.user_id=:owner AND c.artist_id=:artist AND c.manual_follow),
              (SELECT f.projected_at FROM library.favorite_artist_projection f
               JOIN library.favorite_artist_refresh r ON r.user_id=f.user_id
                 AND r.generation=f.generation AND r.algorithm_version=f.algorithm_version
               WHERE f.user_id=:owner AND f.artist_id=:artist
                 AND r.algorithm_version=:algorithm
                 AND f.projected_at<=r.refreshed_at
                 AND r.refreshed_at<=clock_timestamp()
                 AND r.refreshed_at>clock_timestamp()-INTERVAL '24 hours'))
            FROM catalog.artist a WHERE a.artist_id=:artist
              AND a.identity_status='ACTIVE' AND a.deleted_at IS NULL
              AND NOT EXISTS(SELECT 1 FROM library.artist_watch_choice c
                WHERE c.user_id=:owner AND c.artist_id=:artist AND c.explicit_exclusion)
        """),
        {"owner": owner_id, "artist": artist_id, "algorithm": ALGORITHM_VERSION},
    )
    if not isinstance(eligible_since, datetime):
        raise DiscoveryAutomationError("discovery_not_eligible")
    mappings = session.scalars(
        text("""
            SELECT external_id FROM identity.external_reference
            WHERE provider_id=:provider AND artist_id=:artist
              AND external_entity_type='ARTIST' AND market_scope='GLOBAL'
              AND deleted_at IS NULL
        """),
        {"provider": provider_id, "artist": artist_id},
    ).all()
    if len(mappings) != 1 or mappings[0] != provider_artist_id:
        raise DiscoveryAutomationError("identity_review_required")
    return eligible_since


def require_watch_auto_authority(session: Session, policy: ArtistPolicyRow) -> None:
    """Gate every watch-origin AUTO_IMPORT checkpoint in one transaction."""
    if not is_watch_policy(session, policy):
        return
    gate_enabled, gate_revision = gate_state(session, lock=True)
    consent_enabled, _ = consent_state(
        session,
        policy.user_id,
        gate_revision=gate_revision,
        lock=True,
    )
    if not gate_enabled or not consent_enabled:
        raise DiscoveryAutomationError("watch_auto_download_not_authorized")
    require_effective_watch(
        session,
        policy.user_id,
        policy.canonical_artist_id,
        policy.provider_artist_id,
        provider_id=policy.provider_id,
    )


__all__ = (
    "consent_state",
    "gate_state",
    "is_watch_policy",
    "require_effective_watch",
    "require_watch_auto_authority",
    "watch_policy_id",
)
