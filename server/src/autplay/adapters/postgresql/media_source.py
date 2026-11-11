"""M1 one-statement read model and same-database final UTC clock.

No Session/identity map, resolver, locks, audit, sequence, repair or flush. The
dedicated engine is derived from the authoritative engine URL, never a replica.
"""

from __future__ import annotations

from datetime import datetime
from time import monotonic_ns, time_ns
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from autplay.application.media_source import elapsed
from autplay.domain.auth import Principal
from autplay.domain.media_source import MediaSourceError
from autplay.ports.media_source import (
    ActorFacts,
    ConfiguredM5Identity,
    DatabaseClockSample,
    ElapsedClock,
    ElapsedMark,
    RelationshipCapture,
    SourceFacts,
)

# The sentinel retains account/config facts when the requested source is a miss.
# LATERAL and correlated EXISTS stay inside THIS single statement snapshot.
RELATIONSHIP_SQL = """
SELECT a.user_id, a.role, a.status AS account_status, a.deleted_at AS account_deleted_at,
       d.device_id, d.user_id AS device_user_id, d.created_at AS device_created_at,
       d.revoked_at AS device_revoked_at,
       s.session_id, s.user_id AS session_user_id, s.device_id AS session_device_id,
       s.issued_at AS session_issued_at, s.expires_at AS session_expires_at,
       s.revoked_at AS session_revoked_at,
       EXISTS (SELECT 1 FROM account.device_key_block b
               WHERE b.user_id = d.user_id
                 AND b.device_key_thumbprint_sha256 = d.public_key_thumbprint_sha256
                 AND b.unblocked_at IS NULL) AS device_key_blocked,
       i.server_instance_id, i.identity_epoch, i.identity_public_key_spki,
       i.identity_thumbprint_sha256, i.api_origin, i.stream_origin, i.capability_revision,
       src.*
FROM (SELECT 1) AS anchor
LEFT JOIN account.user_account a ON a.user_id = :user_id
LEFT JOIN account.device d ON d.device_id = :device_id AND d.user_id = a.user_id
LEFT JOIN account.user_session s ON s.session_id = :session_id
       AND s.user_id = a.user_id AND s.device_id = d.device_id
LEFT JOIN account.server_instance i ON TRUE
LEFT JOIN LATERAL (
    SELECT u.user_track_ref_id, u.row_version AS user_track_ref_row_version,
           u.recording_id, v.audio_variant_id, v.row_version AS audio_variant_row_version,
           o.sha256, o.byte_size AS total_bytes, o.detected_mime_type AS media_type,
           v.codec, v.container
    FROM library.user_track_ref u
    JOIN catalog.recording r ON r.recording_id = u.recording_id AND r.deleted_at IS NULL
    JOIN vault.recording_canonical_variant c ON c.recording_id = u.recording_id
    JOIN vault.audio_variant v ON v.audio_variant_id = c.audio_variant_id
         AND v.recording_id = u.recording_id AND v.recording_id = c.recording_id
         AND v.validation_status = 'VALID' AND v.deleted_at IS NULL
    JOIN vault.vault_object o ON o.vault_object_id = v.vault_object_id
         AND o.commit_status = 'COMMITTED'
    WHERE u.user_track_ref_id = :user_track_ref_id AND u.user_id = a.user_id
      AND a.status = 'ACTIVE' AND a.deleted_at IS NULL
      AND a.role IN ('OWNER', 'ADMIN', 'USER') AND d.revoked_at IS NULL
      AND s.session_id = :session_id AND s.revoked_at IS NULL
      AND s.expires_at > clock_timestamp()
      AND u.resolution_status = 'RESOLVED' AND u.deleted_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM identity.recording_redirect rr
                      WHERE rr.source_recording_id = r.recording_id)
      AND EXISTS (SELECT 1 FROM library.library_entry e
                  WHERE e.user_track_ref_id = u.user_track_ref_id
                    AND e.user_id = a.user_id AND e.removed_at IS NULL)
      AND EXISTS (SELECT 1 FROM vault.vault_replica p
                  WHERE p.vault_object_id = o.vault_object_id
                    AND p.storage_backend = 'LOCAL_FILESYSTEM'
                    AND p.replica_status = 'AVAILABLE' AND p.verified_at IS NOT NULL)
) src ON TRUE
"""


class SystemElapsedClock:
    def mark(self) -> ElapsedMark:
        # UTC is NOT taken from time_ns. Its elapsed delta only detects local
        # wall-clock steps and suspension omitted by a platform monotonic clock.
        return ElapsedMark(monotonic_ns(), time_ns())


def create_media_source_engine(authoritative_engine: Engine) -> Engine:
    """Standalone future PG-fixture engine on the authoritative database URL.

    Production uses MediaSourceDatabaseRuntime instead: a sync pool alone cannot
    bound DNS or retain every authentication worker through shutdown. This helper
    does not supply a serving-runtime fallback or execution qualification.
    """
    return create_engine(
        authoritative_engine.url,
        echo=False,
        hide_parameters=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=0.25,
        pool_pre_ping=False,
        skip_autocommit_rollback=True,
        connect_args={
            "connect_timeout": 1,
            "tcp_user_timeout": 1000,
            "keepalives": 1,
            "keepalives_idle": 1,
            "keepalives_interval": 1,
            "keepalives_count": 2,
            "application_name": "autplay-media-source",
            "options": "-c default_transaction_read_only=on -c statement_timeout=6000 "
            "-c lock_timeout=250 -c idle_in_transaction_session_timeout=6500",
        },
    )


class PostgresMediaSourceReader:
    def __init__(self, engine: Engine, *, clock: ElapsedClock) -> None:
        """Production supplies MediaSourceDatabaseRuntime.engine.sync_engine.

        Every call then runs inside that runtime's run_sync greenlet. Isolated
        future PG fixtures may use create_media_source_engine explicitly.
        """
        self._engine, self._clock = engine, clock

    def read_relationship(
        self, principal: Principal, user_track_ref_id: UUID, *, started: ElapsedMark
    ) -> RelationshipCapture:
        try:
            elapsed(started, self._clock.mark())
            with self._engine.connect() as connection, connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout = '6000ms'"))
                result = connection.execute(
                    text(RELATIONSHIP_SQL),
                    {
                        "user_id": principal.user_id,
                        "device_id": principal.device_id,
                        "session_id": principal.session_id,
                        "user_track_ref_id": user_track_ref_id,
                    },
                )
                try:
                    rows = result.mappings().fetchmany(2)
                finally:
                    result.close()
                if len(rows) != 1:
                    raise MediaSourceError()
                row = rows[0]
                actor = None
                if all(row[key] is not None for key in ("user_id", "device_id", "session_id")):
                    actor = ActorFacts(**{key: row[key] for key in ActorFacts.__dataclass_fields__})
                identity = None
                if row["server_instance_id"] is not None:
                    identity = ConfiguredM5Identity(
                        row["server_instance_id"],
                        row["identity_epoch"],
                        bytes(row["identity_public_key_spki"]),
                        bytes(row["identity_thumbprint_sha256"]),
                        row["api_origin"],
                        row["stream_origin"],
                    )
                source = None
                if row["user_track_ref_id"] is not None:
                    values = {key: row[key] for key in SourceFacts.__dataclass_fields__}
                    values["sha256"] = bytes(values["sha256"])
                    source = SourceFacts(**values)
                capture = RelationshipCapture(actor, identity, row["capability_revision"], source)
                # All transaction settlement and connection release happen here,
                # BEFORE the separate final clock is dispatched by application.
            elapsed(started, self._clock.mark())
            return capture
        except SQLAlchemyError, TypeError, ValueError, KeyError:
            raise MediaSourceError() from None

    def final_database_clock(self, *, started: ElapsedMark) -> DatabaseClockSample:
        try:
            before = self._clock.mark()
            elapsed(started, before)
            with self._engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                # Default READ ONLY is a connection property of the dedicated
                # pool; this clock-only statement reads no authority or config.
                connection.execute(text("SET statement_timeout = '250ms'"))
                result = connection.execute(text("SELECT clock_timestamp()"))
                try:
                    value = result.scalar_one()
                finally:
                    result.close()
                # AUTOCOMMIT SET survives pool return. Restore the auth/read
                # default on this same connection; its settlement/release stays
                # INSIDE m0..m1. This reads no further authority/config/source.
                connection.execute(text("SET statement_timeout = '6000ms'"))
            after = self._clock.mark()  # release/reset latency is inside m0..m1
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise MediaSourceError()
            elapsed(started, after)
            return DatabaseClockSample(value, before, after)
        except SQLAlchemyError, TypeError, ValueError:
            raise MediaSourceError() from None
