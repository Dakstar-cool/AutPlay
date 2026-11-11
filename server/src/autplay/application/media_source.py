"""M1 read application: settled relationship, last DB clock, pure send admission."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from autplay.domain.auth import AccessTokenClaims, PortableAccessAdmission, Principal
from autplay.domain.media_source import (
    MAX_INTEGER,
    NAMESPACE,
    MediaSourceError,
    MediaSourceSnapshotRequestV1,
    MediaSourceSnapshotV1,
    canonical_uuid,
)
from autplay.ports.media_source import (
    CurrentMediaSourcePolicy,
    DatabaseClockSample,
    ElapsedClock,
    ElapsedMark,
    MediaSourcePolicy,
    MediaSourceReader,
    RelationshipCapture,
)

CONTROL_NS = 15_000_000_000
CLOCK_UNCERTAINTY_NS = 1_000_000_000
DISCONTINUITY_NS = 100_000_000


def elapsed(before: ElapsedMark, after: ElapsedMark) -> int:
    """Watch is never an authority UTC anchor; reject changed elapsed domains."""
    monotonic = after.monotonic_ns - before.monotonic_ns
    watch = after.suspend_watch_ns - before.suspend_watch_ns
    if (
        monotonic < 0
        or monotonic > CONTROL_NS
        or watch < 0
        or abs(watch - monotonic) > DISCONTINUITY_NS
    ):
        raise MediaSourceError()
    return monotonic


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MediaSourceError()
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PreparedMediaSourceReply:
    """No transaction/connection/lazy value survives preparation.

    Call admit immediately before ASGI response start. Threadpool handoff and
    response preparation delay are deliberately included by its fresh mark.
    """

    principal: Principal
    claims: AccessTokenClaims
    capture: RelationshipCapture
    policy: MediaSourcePolicy
    current_policy: CurrentMediaSourcePolicy = field(repr=False)
    clock: ElapsedClock = field(repr=False)
    started: ElapsedMark
    sample: DatabaseClockSample
    body: bytes = field(repr=False)
    pending_error: str | None = None

    def admit(self) -> bytes:
        final = self.clock.mark()
        elapsed(self.started, final)
        uncertainty = elapsed(self.sample.before, self.sample.after)
        if uncertainty > CLOCK_UNCERTAINTY_NS:
            raise MediaSourceError()
        upper_ns = elapsed(self.sample.before, final)
        lower_ns = elapsed(self.sample.after, final)
        try:
            anchor = _utc(self.sample.db_utc)
            # Ceil upper and floor lower when converting nanoseconds to UTC's
            # microsecond resolution, preserving a conservative closed bracket.
            now = anchor + timedelta(microseconds=(upper_ns + 999) // 1000)
            earliest = anchor + timedelta(microseconds=lower_ns // 1000)
            self._require_actor(earliest, now)
        except OverflowError, TypeError, ValueError:
            raise MediaSourceError() from None
        if (
            self.current_policy() is not self.policy
            or self.policy.media_source_snapshot_v1_enabled is not True
            or self.policy.identity is None
            or self.capture.identity != self.policy.identity
            or type(self.policy.m5_capability_revision) is not int
            or not 1 <= self.policy.m5_capability_revision <= MAX_INTEGER
            or self.capture.m5_capability_revision != self.policy.m5_capability_revision
        ):
            raise MediaSourceError()
        if self.pending_error is not None:
            raise MediaSourceError(self.pending_error)
        return self.body

    def _require_actor(self, earliest: datetime, now: datetime) -> None:
        actor, claims, principal = self.capture.actor, self.claims, self.principal
        if actor is None:
            raise MediaSourceError("authentication_required")
        expected = (principal.user_id, principal.device_id, principal.session_id)
        if (
            (claims.user_id, claims.device_id, claims.session_id) != expected
            or (actor.user_id, actor.device_id, actor.session_id) != expected
            or actor.device_user_id != principal.user_id
            or actor.session_user_id != principal.user_id
            or actor.session_device_id != principal.device_id
            or actor.account_status != "ACTIVE"
            or actor.account_deleted_at is not None
            or actor.role not in {"OWNER", "ADMIN", "USER"}
            or actor.role != principal.role.value
            or actor.device_revoked_at is not None
            or actor.device_key_blocked is not False
        ):
            raise MediaSourceError("authentication_required")
        issued, not_before, expires = map(
            _utc, (claims.issued_at, claims.not_before, claims.expires_at)
        )
        session_issue, session_expiry = map(
            _utc, (actor.session_issued_at, actor.session_expires_at)
        )
        ttl = self.policy.access_token_ttl_seconds
        if type(ttl) is not int or not 1 <= ttl <= 900:
            raise MediaSourceError()
        lifetime = expires - issued
        lifetime_allowed = timedelta(0) < lifetime <= timedelta(seconds=ttl)
        if not lifetime_allowed and timedelta(seconds=ttl) < lifetime <= timedelta(seconds=900):
            admission = claims.portable_admission
            lifetime_allowed = type(admission) is PortableAccessAdmission and admission.accepts(
                principal, claims, now
            )
        if (
            issued > earliest
            or not_before > earliest
            or expires <= now
            or not_before != issued
            or not lifetime_allowed
            or issued < session_issue.replace(microsecond=0)
            or session_issue > earliest
            or session_expiry <= session_issue
            or _utc(actor.device_created_at) > earliest
        ):
            raise MediaSourceError("authentication_required")
        if actor.session_revoked_at is not None or session_expiry <= now:
            raise MediaSourceError("session_revoked")


class MediaSourceService:
    def __init__(
        self, reader: MediaSourceReader, *, policy: CurrentMediaSourcePolicy, clock: ElapsedClock
    ) -> None:
        self._reader, self._policy, self._clock = reader, policy, clock

    def current_policy(self) -> MediaSourcePolicy:
        """Forward the owner's immutable reference without allocation or reload."""
        return self._policy()

    def prepare(
        self,
        principal: Principal,
        claims: AccessTokenClaims,
        request: MediaSourceSnapshotRequestV1,
        *,
        started: ElapsedMark,
    ) -> PreparedMediaSourceReply:
        policy = self._policy()
        if (
            type(policy) is not MediaSourcePolicy
            or policy.media_source_snapshot_v1_enabled is not True
            or policy.identity is None
            or type(policy.m5_capability_revision) is not int
            or not 1 <= policy.m5_capability_revision <= MAX_INTEGER
        ):
            raise MediaSourceError()
        elapsed(started, self._clock.mark())
        capture = self._reader.read_relationship(
            principal, canonical_uuid(request.user_track_ref_id), started=started
        )
        body, pending = b"", None
        if capture.source is None:
            pending = "not_found"
        elif (
            capture.identity is None
            or capture.identity != policy.identity
            or capture.m5_capability_revision != policy.m5_capability_revision
        ):
            pending = "media_source_unavailable"
        else:
            source, identity = capture.source, capture.identity
            try:
                if source.user_track_ref_id != canonical_uuid(request.user_track_ref_id):
                    raise ValueError("source identity")
                revision = capture.m5_capability_revision
                if type(revision) is not int:
                    raise ValueError("M5 revision unavailable")
                body = MediaSourceSnapshotV1(
                    namespace=NAMESPACE,
                    wire_version=1,
                    server_instance_id=str(identity.server_instance_id),
                    identity_epoch=identity.identity_epoch,
                    identity_thumbprint_sha256=identity.thumbprint_sha256.hex(),
                    capability_revision=revision,
                    user_id=str(principal.user_id),
                    device_id=str(principal.device_id),
                    user_track_ref_id=str(source.user_track_ref_id),
                    user_track_ref_row_version=source.user_track_ref_row_version,
                    recording_id=str(source.recording_id),
                    audio_variant_id=str(source.audio_variant_id),
                    audio_variant_row_version=source.audio_variant_row_version,
                    sha256=source.sha256.hex(),
                    total_bytes=source.total_bytes,
                    media_type=source.media_type,
                    codec=source.codec,
                    container=source.container,
                ).encode()
            except AttributeError, TypeError, ValueError, UnicodeError:
                pending = "media_source_unavailable"
        elapsed(started, self._clock.mark())
        # read_relationship has returned only after rollback/release. This is the
        # LAST blocking read; the returned reply permits only pure checks.
        sample = self._reader.final_database_clock(started=started)
        return PreparedMediaSourceReply(
            principal,
            claims,
            capture,
            policy,
            self._policy,
            self._clock,
            started,
            sample,
            body,
            pending,
        )
