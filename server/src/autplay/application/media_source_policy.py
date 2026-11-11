"""Committed public M5 configuration for M1's final, nonblocking admission.

No identity creation, SQL, key derivation or new policy allocation occurs in
current(). Only the existing M5 producer stages public values; session events
publish after its successful OUTER commit. SAVEPOINT release is not publication.
SQLAlchemy's pinned Session lifecycle places after_commit after driver commit
and before transaction-end cleanup:
https://github.com/sqlalchemy/sqlalchemy/blob/rel_2_0_52/lib/sqlalchemy/orm/session.py
https://docs.sqlalchemy.org/en/20/orm/events.html
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, SessionTransaction

from autplay.domain.device_compatibility import DeviceCompatibilityError, NegotiationIdentity
from autplay.domain.media_source import MAX_INTEGER
from autplay.ports.media_source import ConfiguredM5Identity, MediaSourcePolicy


@dataclass(frozen=True, slots=True)
class _Candidate:
    order: int
    identity: ConfiguredM5Identity | None
    revision: int | None


class MediaSourcePolicyCapture:
    """One stable immutable-reference getter, owned by ProfilePairingService.

    Writer serialization never reaches the getter. Repeated commits of the same
    public configuration/revision retain the SAME policy object, so they cannot
    spuriously invalidate a read. M5 producer order prevents a delayed callback
    from replacing a newer committed capture. It is process-local ordering, not
    a capability counter, persisted high-water, D1/B1 guard or authority grant.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        access_token_ttl_seconds: int,
        public_key_spki: bytes,
        thumbprint_sha256: bytes,
        api_origin: str,
        stream_origin: str,
    ) -> None:
        self._expected = (public_key_spki, thumbprint_sha256, api_origin, stream_origin)
        self._policy = MediaSourcePolicy(enabled, None, access_token_ttl_seconds)
        self._writer = Lock()
        self._order = 0
        self._published = 0

    def current(self) -> MediaSourcePolicy:
        return self._policy

    def configure(self, *, enabled: bool, access_token_ttl_seconds: int) -> None:
        """Explicit owner configuration publication, never called by snapshot GET.

        The API settings object is frozen; a future settings replacement must
        update this owner together with the actual AuthService TTL. Key/origin
        replacement requires recomposition and a fresh committed M5 capture.
        """
        if (
            type(enabled) is not bool
            or type(access_token_ttl_seconds) is not int
            or not 1 <= access_token_ttl_seconds <= 900
        ):
            raise ValueError("invalid media source policy")
        with self._writer:
            previous = self._policy
            if (
                previous.media_source_snapshot_v1_enabled != enabled
                or previous.access_token_ttl_seconds != access_token_ttl_seconds
            ):
                self._policy = MediaSourcePolicy(
                    enabled,
                    previous.identity,
                    access_token_ttl_seconds,
                    previous.m5_capability_revision,
                )

    def stage(self, session: Session, identity: ConfiguredM5Identity, revision: int) -> None:
        """Stage already loaded producer evidence, without another database read."""
        valid = True
        try:
            NegotiationIdentity(
                identity.server_instance_id,
                identity.identity_epoch,
                identity.public_key_spki,
                identity.thumbprint_sha256,
                identity.api_origin,
                identity.stream_origin,
            ).fields()
            if (
                (
                    identity.public_key_spki,
                    identity.thumbprint_sha256,
                    identity.api_origin,
                    identity.stream_origin,
                )
                != self._expected
                or type(revision) is not int
                or not 1 <= revision <= MAX_INTEGER
            ):
                valid = False
        except DeviceCompatibilityError, TypeError, ValueError, AttributeError:
            valid = False
        with self._writer:
            self._order += 1
            candidate = _Candidate(
                self._order, identity if valid else None, revision if valid else None
            )
        owners = session.info.setdefault("autplay_media_source_publications", {})
        publication = owners.get(self)
        if publication is None:
            publication = _CommitPublication(self, session)
            owners[self] = publication
        publication.stage(session, candidate)

    def _publish(self, candidate: _Candidate) -> None:
        with self._writer:
            if candidate.order <= self._published:
                return
            self._published = candidate.order
            previous = self._policy
            if (
                previous.identity != candidate.identity
                or previous.m5_capability_revision != candidate.revision
            ):
                self._policy = MediaSourcePolicy(
                    previous.media_source_snapshot_v1_enabled,
                    candidate.identity,
                    previous.access_token_ttl_seconds,
                    candidate.revision,
                )


class _CommitPublication:
    def __init__(self, owner: MediaSourcePolicyCapture, session: Session) -> None:
        self._owner = owner
        self._pending: dict[SessionTransaction, _Candidate] = {}
        self._committed: set[SessionTransaction] = set()
        event.listen(session, "after_commit", self._after_commit)
        event.listen(session, "after_transaction_end", self._after_end)

    def stage(self, session: Session, candidate: _Candidate) -> None:
        # Engine-bound production Sessions own their actual driver transaction.
        # A Session joined to an externally managed Connection can report a
        # logical commit before that external transaction commits; do not publish.
        if not isinstance(session.get_bind(), Engine):
            return
        transaction = session.get_nested_transaction() or session.get_transaction()
        if transaction is not None:
            self._pending[transaction] = candidate

    def _after_commit(self, session: Session) -> None:
        transaction = session.get_nested_transaction() or session.get_transaction()
        if transaction is not None:
            if transaction.parent is None:
                candidate = self._pending.pop(transaction, None)
                if candidate is not None:
                    # Driver COMMIT has completed. Publish before connection
                    # release/transaction-end cleanup can delay invalidation.
                    self._owner._publish(candidate)
            else:
                self._committed.add(transaction)

    def _after_end(self, session: Session, transaction: SessionTransaction) -> None:
        candidate = self._pending.pop(transaction, None)
        committed = transaction in self._committed
        self._committed.discard(transaction)
        if not committed or candidate is None:
            return
        if transaction.nested and transaction.parent is not None:
            # A released SAVEPOINT only stages into its parent. Parent rollback
            # discards it; only the eventual real outer commit can publish it.
            self._pending[transaction.parent] = candidate
