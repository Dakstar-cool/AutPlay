-- AutPlay PostgreSQL Schema v1
-- Target: PostgreSQL 18.x, pgvector 0.8.6+
-- Scope: initial clean-install reference DDL
-- Migrations must be applied through Alembic; this file is the reviewed schema baseline.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA account;
CREATE SCHEMA catalog;
CREATE SCHEMA identity;
CREATE SCHEMA library;
CREATE SCHEMA playlist;
CREATE SCHEMA vault;
CREATE SCHEMA importing;
CREATE SCHEMA sync;
CREATE SCHEMA jobs;
CREATE SCHEMA ml;
CREATE SCHEMA audit;
CREATE SCHEMA app_private;

REVOKE ALL ON SCHEMA app_private FROM PUBLIC;

CREATE FUNCTION app_private.bump_row_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := clock_timestamp();
    NEW.row_version := OLD.row_version + 1;
    RETURN NEW;
END;
$$;

-- -----------------------------------------------------------------------------
-- account
-- -----------------------------------------------------------------------------

CREATE TABLE account.user_account (
    user_id uuid PRIMARY KEY DEFAULT uuidv7(),
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
    role text NOT NULL DEFAULT 'USER',
    status text NOT NULL DEFAULT 'ACTIVE',
    settings_version bigint NOT NULL DEFAULT 1 CHECK (settings_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT ck_user_account_role
        CHECK (role IN ('OWNER', 'ADMIN', 'USER')),
    CONSTRAINT ck_user_account_status
        CHECK (status IN ('ACTIVE', 'DISABLED'))
);

CREATE TABLE account.device (
    device_id uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    device_name text NOT NULL CHECK (length(device_name) BETWEEN 1 AND 200),
    platform text NOT NULL,
    app_version text NOT NULL CHECK (length(app_version) BETWEEN 1 AND 100),
    public_key bytea,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    revoked_at timestamptz,
    last_seen_at timestamptz,
    CONSTRAINT uq_device_user_pair UNIQUE (user_id, device_id),
    CONSTRAINT ck_device_platform
        CHECK (platform IN ('ANDROID', 'WEB', 'OTHER'))
);

CREATE INDEX ix_device_user_active
    ON account.device (user_id, last_seen_at DESC)
    WHERE revoked_at IS NULL;

CREATE TABLE account.user_session (
    session_id uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
    refresh_token_hash bytea NOT NULL UNIQUE,
    issued_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    last_rotated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_user_session_device_owner
        FOREIGN KEY (user_id, device_id)
        REFERENCES account.device(user_id, device_id) ON DELETE RESTRICT,
    CONSTRAINT ck_user_session_hash_len
        CHECK (octet_length(refresh_token_hash) = 32),
    CONSTRAINT ck_user_session_expiry
        CHECK (expires_at > issued_at)
);

CREATE INDEX ix_user_session_user_active
    ON account.user_session (user_id, expires_at)
    WHERE revoked_at IS NULL;

-- -----------------------------------------------------------------------------
-- catalog
-- -----------------------------------------------------------------------------

CREATE TABLE catalog.artist (
    artist_id uuid PRIMARY KEY DEFAULT uuidv7(),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 1000),
    sort_name text NOT NULL CHECK (length(sort_name) BETWEEN 1 AND 1000),
    normalized_name text NOT NULL CHECK (length(normalized_name) BETWEEN 1 AND 1000),
    artist_type text NOT NULL DEFAULT 'UNKNOWN',
    disambiguation text,
    country_code text,
    identity_status text NOT NULL DEFAULT 'ACTIVE',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT ck_artist_type
        CHECK (artist_type IN ('PERSON', 'GROUP', 'ORCHESTRA', 'OTHER', 'UNKNOWN')),
    CONSTRAINT ck_artist_country_code
        CHECK (country_code IS NULL OR country_code ~ '^[A-Z]{2}$'),
    CONSTRAINT ck_artist_identity_status
        CHECK (identity_status IN ('ACTIVE', 'PROVISIONAL', 'MERGED', 'DEPRECATED'))
);

CREATE INDEX ix_artist_normalized_name_trgm
    ON catalog.artist USING gin (normalized_name gin_trgm_ops);

CREATE TABLE catalog.artist_credit (
    artist_credit_id uuid PRIMARY KEY DEFAULT uuidv7(),
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 2000),
    normalized_name text NOT NULL CHECK (length(normalized_name) BETWEEN 1 AND 2000),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz
);

CREATE INDEX ix_artist_credit_normalized_name_trgm
    ON catalog.artist_credit USING gin (normalized_name gin_trgm_ops);

CREATE TABLE catalog.artist_credit_name (
    artist_credit_id uuid NOT NULL
        REFERENCES catalog.artist_credit(artist_credit_id) ON DELETE CASCADE,
    position integer NOT NULL CHECK (position >= 0),
    artist_id uuid NOT NULL REFERENCES catalog.artist(artist_id) ON DELETE RESTRICT,
    credited_name text NOT NULL CHECK (length(credited_name) BETWEEN 1 AND 1000),
    join_phrase text NOT NULL DEFAULT '',
    role text NOT NULL DEFAULT 'PRIMARY',
    PRIMARY KEY (artist_credit_id, position),
    CONSTRAINT ck_artist_credit_name_role
        CHECK (role IN ('PRIMARY', 'FEATURED', 'REMIXER', 'CONDUCTOR', 'OTHER'))
);

CREATE INDEX ix_artist_credit_name_artist
    ON catalog.artist_credit_name (artist_id, artist_credit_id);

CREATE TABLE catalog.work (
    work_id uuid PRIMARY KEY DEFAULT uuidv7(),
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 2000),
    work_type text NOT NULL DEFAULT 'OTHER',
    language_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT ck_work_type CHECK (work_type IN ('SONG', 'COMPOSITION', 'OTHER')),
    CONSTRAINT ck_work_language_code
        CHECK (language_code IS NULL OR length(language_code) BETWEEN 2 AND 35)
);

CREATE TABLE catalog.recording (
    recording_id uuid PRIMARY KEY DEFAULT uuidv7(),
    work_id uuid REFERENCES catalog.work(work_id) ON DELETE SET NULL,
    artist_credit_id uuid NOT NULL
        REFERENCES catalog.artist_credit(artist_credit_id) ON DELETE RESTRICT,
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 2000),
    normalized_title text NOT NULL CHECK (length(normalized_title) BETWEEN 1 AND 2000),
    duration_ms bigint CHECK (duration_ms > 0),
    recording_kind text NOT NULL DEFAULT 'UNKNOWN',
    version_text text,
    disambiguation text,
    explicit boolean,
    identity_status text NOT NULL DEFAULT 'PROVISIONAL',
    metadata_confidence numeric(5,4),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT ck_recording_kind
        CHECK (recording_kind IN ('STUDIO', 'LIVE', 'REMIX', 'EDIT', 'DEMO', 'OTHER', 'UNKNOWN')),
    CONSTRAINT ck_recording_identity_status
        CHECK (identity_status IN ('ACTIVE', 'PROVISIONAL', 'MERGED', 'DEPRECATED')),
    CONSTRAINT ck_recording_metadata_confidence
        CHECK (metadata_confidence IS NULL OR metadata_confidence BETWEEN 0 AND 1)
);

CREATE INDEX ix_recording_artist_credit
    ON catalog.recording (artist_credit_id, identity_status);

CREATE INDEX ix_recording_normalized_title_trgm
    ON catalog.recording USING gin (normalized_title gin_trgm_ops);

CREATE TABLE catalog.release_group (
    release_group_id uuid PRIMARY KEY DEFAULT uuidv7(),
    artist_credit_id uuid NOT NULL
        REFERENCES catalog.artist_credit(artist_credit_id) ON DELETE RESTRICT,
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 2000),
    normalized_title text NOT NULL CHECK (length(normalized_title) BETWEEN 1 AND 2000),
    primary_type text NOT NULL,
    secondary_types text[] NOT NULL DEFAULT ARRAY[]::text[],
    first_release_date date,
    date_precision text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT ck_release_group_primary_type
        CHECK (primary_type IN ('ALBUM', 'SINGLE', 'EP', 'BROADCAST', 'OTHER')),
    CONSTRAINT ck_release_group_secondary_types
        CHECK (secondary_types <@ ARRAY['COMPILATION', 'SOUNDTRACK', 'LIVE', 'REMIX', 'DJ_MIX', 'MIXTAPE', 'OTHER']::text[]),
    CONSTRAINT ck_release_group_date_precision
        CHECK (
            (first_release_date IS NULL AND date_precision IS NULL)
            OR
            (first_release_date IS NOT NULL AND date_precision IN ('YEAR', 'MONTH', 'DAY'))
        )
);

CREATE INDEX ix_release_group_title_trgm
    ON catalog.release_group USING gin (normalized_title gin_trgm_ops);

CREATE TABLE catalog.release (
    release_id uuid PRIMARY KEY DEFAULT uuidv7(),
    release_group_id uuid NOT NULL
        REFERENCES catalog.release_group(release_group_id) ON DELETE RESTRICT,
    artist_credit_id uuid NOT NULL
        REFERENCES catalog.artist_credit(artist_credit_id) ON DELETE RESTRICT,
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 2000),
    country_code text,
    release_date date,
    date_precision text,
    status text NOT NULL DEFAULT 'UNKNOWN',
    barcode text,
    label_name text,
    catalog_number text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT ck_release_country_code
        CHECK (country_code IS NULL OR country_code ~ '^[A-Z]{2}$'),
    CONSTRAINT ck_release_date_precision
        CHECK (
            (release_date IS NULL AND date_precision IS NULL)
            OR
            (release_date IS NOT NULL AND date_precision IN ('YEAR', 'MONTH', 'DAY'))
        ),
    CONSTRAINT ck_release_status
        CHECK (status IN ('OFFICIAL', 'PROMOTION', 'BOOTLEG', 'PSEUDO', 'UNKNOWN'))
);

CREATE INDEX ix_release_release_group
    ON catalog.release (release_group_id, release_date);

CREATE INDEX ix_release_barcode
    ON catalog.release (barcode)
    WHERE barcode IS NOT NULL;

CREATE TABLE catalog.medium (
    medium_id uuid PRIMARY KEY DEFAULT uuidv7(),
    release_id uuid NOT NULL REFERENCES catalog.release(release_id) ON DELETE RESTRICT,
    position integer NOT NULL CHECK (position >= 1),
    format text,
    title text,
    track_count integer CHECK (track_count IS NULL OR track_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT uq_medium_release_position UNIQUE (release_id, position)
);

CREATE TABLE catalog.release_track (
    release_track_id uuid PRIMARY KEY DEFAULT uuidv7(),
    medium_id uuid NOT NULL REFERENCES catalog.medium(medium_id) ON DELETE RESTRICT,
    recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    artist_credit_id uuid NOT NULL
        REFERENCES catalog.artist_credit(artist_credit_id) ON DELETE RESTRICT,
    sequence_no integer NOT NULL CHECK (sequence_no >= 1),
    number_text text,
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 2000),
    duration_ms bigint CHECK (duration_ms IS NULL OR duration_ms > 0),
    hidden boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT uq_release_track_medium_sequence UNIQUE (medium_id, sequence_no)
);

CREATE INDEX ix_release_track_recording
    ON catalog.release_track (recording_id, medium_id);

-- -----------------------------------------------------------------------------
-- audit base
-- -----------------------------------------------------------------------------

CREATE TABLE audit.catalog_change_set (
    change_set_id uuid PRIMARY KEY DEFAULT uuidv7(),
    operation_type text NOT NULL,
    actor_type text NOT NULL,
    actor_user_id uuid REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 4000),
    confidence numeric(5,4),
    created_at timestamptz NOT NULL DEFAULT now(),
    reversible_until timestamptz,
    status text NOT NULL DEFAULT 'PLANNED',
    CONSTRAINT ck_catalog_change_set_operation
        CHECK (operation_type IN ('MERGE', 'SPLIT', 'REASSIGN', 'UNDO')),
    CONSTRAINT ck_catalog_change_set_actor
        CHECK (actor_type IN ('SYSTEM', 'USER', 'ADMIN')),
    CONSTRAINT ck_catalog_change_set_actor_user
        CHECK (
            (actor_type = 'SYSTEM')
            OR
            (actor_type IN ('USER', 'ADMIN') AND actor_user_id IS NOT NULL)
        ),
    CONSTRAINT ck_catalog_change_set_confidence
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    CONSTRAINT ck_catalog_change_set_status
        CHECK (status IN ('PLANNED', 'APPLIED', 'REVERTED', 'FAILED'))
);

CREATE TABLE audit.catalog_change_item (
    change_item_id uuid PRIMARY KEY DEFAULT uuidv7(),
    change_set_id uuid NOT NULL
        REFERENCES audit.catalog_change_set(change_set_id) ON DELETE CASCADE,
    entity_type text NOT NULL CHECK (length(entity_type) BETWEEN 1 AND 100),
    entity_id uuid NOT NULL,
    action text NOT NULL CHECK (length(action) BETWEEN 1 AND 100),
    from_snapshot jsonb,
    to_snapshot jsonb,
    sequence_no integer NOT NULL CHECK (sequence_no >= 1),
    CONSTRAINT uq_catalog_change_item_sequence UNIQUE (change_set_id, sequence_no)
);

CREATE TABLE audit.audit_event (
    audit_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_type text NOT NULL,
    actor_user_id uuid REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    actor_device_id uuid REFERENCES account.device(device_id) ON DELETE RESTRICT,
    action text NOT NULL CHECK (length(action) BETWEEN 1 AND 200),
    target_type text NOT NULL CHECK (length(target_type) BETWEEN 1 AND 100),
    target_id uuid,
    request_id uuid,
    reason_code text,
    metadata_sanitized jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT ck_audit_event_actor_type
        CHECK (actor_type IN ('SYSTEM', 'USER', 'ADMIN', 'WORKER'))
);

CREATE INDEX ix_audit_event_occurred_at
    ON audit.audit_event (occurred_at DESC);

CREATE INDEX ix_audit_event_target
    ON audit.audit_event (target_type, target_id, occurred_at DESC);

-- -----------------------------------------------------------------------------
-- identity and provenance
-- -----------------------------------------------------------------------------

CREATE TABLE identity.source_provider (
    provider_id uuid PRIMARY KEY DEFAULT uuidv7(),
    provider_key text NOT NULL UNIQUE CHECK (provider_key ~ '^[a-z0-9][a-z0-9._-]{1,99}$'),
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
    adapter_id text NOT NULL CHECK (length(adapter_id) BETWEEN 1 AND 200),
    adapter_version text NOT NULL CHECK (length(adapter_version) BETWEEN 1 AND 100),
    capabilities text[] NOT NULL DEFAULT ARRAY[]::text[],
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT ck_source_provider_capabilities
        CHECK (capabilities <@ ARRAY['SEARCH', 'METADATA', 'IMPORT', 'DOWNLOAD', 'STREAM', 'RELEASE_WATCH']::text[])
);

CREATE TABLE identity.recording_identifier (
    recording_identifier_id uuid PRIMARY KEY DEFAULT uuidv7(),
    recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    scheme text NOT NULL,
    value text NOT NULL CHECK (length(value) BETWEEN 1 AND 500),
    provider_id uuid REFERENCES identity.source_provider(provider_id) ON DELETE RESTRICT,
    confidence numeric(5,4) NOT NULL DEFAULT 0,
    verified boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_recording_identifier_scheme
        CHECK (scheme IN ('ISRC', 'MBID', 'OTHER')),
    CONSTRAINT ck_recording_identifier_confidence
        CHECK (confidence BETWEEN 0 AND 1),
    CONSTRAINT uq_recording_identifier_recording_scheme_value
        UNIQUE (recording_id, scheme, value)
);

CREATE INDEX ix_recording_identifier_lookup
    ON identity.recording_identifier (scheme, value);

CREATE TABLE identity.external_reference (
    external_reference_id uuid PRIMARY KEY DEFAULT uuidv7(),
    provider_id uuid NOT NULL REFERENCES identity.source_provider(provider_id) ON DELETE RESTRICT,
    external_entity_type text NOT NULL CHECK (length(external_entity_type) BETWEEN 1 AND 100),
    external_id text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 1000),
    market_scope text NOT NULL DEFAULT 'GLOBAL' CHECK (length(market_scope) BETWEEN 1 AND 100),
    artist_id uuid REFERENCES catalog.artist(artist_id) ON DELETE RESTRICT,
    recording_id uuid REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    release_group_id uuid REFERENCES catalog.release_group(release_group_id) ON DELETE RESTRICT,
    release_id uuid REFERENCES catalog.release(release_id) ON DELETE RESTRICT,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT ck_external_reference_single_target
        CHECK (num_nonnulls(artist_id, recording_id, release_group_id, release_id) <= 1),
    CONSTRAINT ck_external_reference_seen_order
        CHECK (last_seen_at >= first_seen_at),
    CONSTRAINT uq_external_reference_namespace
        UNIQUE (provider_id, external_entity_type, external_id, market_scope)
);

CREATE INDEX ix_external_reference_recording
    ON identity.external_reference (recording_id)
    WHERE recording_id IS NOT NULL;

CREATE TABLE identity.source_observation (
    source_observation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    external_reference_id uuid NOT NULL
        REFERENCES identity.external_reference(external_reference_id) ON DELETE RESTRICT,
    observed_at timestamptz NOT NULL,
    adapter_version text NOT NULL CHECK (length(adapter_version) BETWEEN 1 AND 100),
    raw_metadata_hash bytea NOT NULL,
    raw_metadata jsonb NOT NULL,
    confidence numeric(5,4) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_source_observation_hash_len
        CHECK (octet_length(raw_metadata_hash) = 32),
    CONSTRAINT ck_source_observation_confidence
        CHECK (confidence BETWEEN 0 AND 1)
);

CREATE INDEX ix_source_observation_reference_time
    ON identity.source_observation (external_reference_id, observed_at DESC);

CREATE TABLE identity.recording_redirect (
    source_recording_id uuid PRIMARY KEY
        REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    target_recording_id uuid NOT NULL
        REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    change_set_id uuid NOT NULL
        REFERENCES audit.catalog_change_set(change_set_id) ON DELETE RESTRICT,
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 4000),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_recording_redirect_not_self
        CHECK (source_recording_id <> target_recording_id)
);

CREATE FUNCTION app_private.prevent_recording_redirect_cycle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        WITH RECURSIVE chain(recording_id) AS (
            SELECT NEW.target_recording_id
            UNION ALL
            SELECT rr.target_recording_id
            FROM identity.recording_redirect rr
            JOIN chain c ON rr.source_recording_id = c.recording_id
        )
        SELECT 1 FROM chain WHERE recording_id = NEW.source_recording_id
    ) THEN
        RAISE EXCEPTION 'recording redirect cycle detected';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_recording_redirect_no_cycle
BEFORE INSERT OR UPDATE ON identity.recording_redirect
FOR EACH ROW EXECUTE FUNCTION app_private.prevent_recording_redirect_cycle();

-- -----------------------------------------------------------------------------
-- sync base
-- -----------------------------------------------------------------------------

CREATE TABLE sync.device_event_inbox (
    event_id uuid PRIMARY KEY,
    device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    device_sequence bigint NOT NULL CHECK (device_sequence >= 1),
    event_type text NOT NULL CHECK (length(event_type) BETWEEN 1 AND 200),
    schema_version integer NOT NULL CHECK (schema_version >= 1),
    aggregate_type text NOT NULL CHECK (length(aggregate_type) BETWEEN 1 AND 100),
    aggregate_id uuid NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    apply_status text NOT NULL DEFAULT 'RECEIVED',
    error_code text,
    request_hash bytea NOT NULL,
    CONSTRAINT fk_device_event_inbox_owner
        FOREIGN KEY (user_id, device_id)
        REFERENCES account.device(user_id, device_id) ON DELETE RESTRICT,
    CONSTRAINT uq_device_event_sequence UNIQUE (device_id, device_sequence),
    CONSTRAINT ck_device_event_apply_status
        CHECK (apply_status IN ('RECEIVED', 'APPLIED', 'DUPLICATE', 'CONFLICT', 'REJECTED')),
    CONSTRAINT ck_device_event_request_hash_len
        CHECK (octet_length(request_hash) = 32)
);

CREATE INDEX ix_device_event_inbox_pending
    ON sync.device_event_inbox (received_at, event_id)
    WHERE apply_status = 'RECEIVED';

CREATE TABLE sync.sync_event (
    server_sequence bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    event_id uuid NOT NULL UNIQUE DEFAULT uuidv7(),
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    origin_device_id uuid REFERENCES account.device(device_id) ON DELETE RESTRICT,
    event_type text NOT NULL CHECK (length(event_type) BETWEEN 1 AND 200),
    schema_version integer NOT NULL CHECK (schema_version >= 1),
    aggregate_type text NOT NULL CHECK (length(aggregate_type) BETWEEN 1 AND 100),
    aggregate_id uuid NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_sync_event_user_event UNIQUE (user_id, event_id),
    CONSTRAINT fk_sync_event_origin_owner
        FOREIGN KEY (user_id, origin_device_id)
        REFERENCES account.device(user_id, device_id) ON DELETE RESTRICT
);

CREATE INDEX ix_sync_event_user_sequence
    ON sync.sync_event (user_id, server_sequence);

CREATE TABLE sync.device_sync_cursor (
    device_id uuid PRIMARY KEY REFERENCES account.device(device_id) ON DELETE RESTRICT,
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    last_pulled_server_sequence bigint NOT NULL DEFAULT 0 CHECK (last_pulled_server_sequence >= 0),
    last_acked_device_sequence bigint NOT NULL DEFAULT 0 CHECK (last_acked_device_sequence >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_device_sync_cursor_owner
        FOREIGN KEY (user_id, device_id)
        REFERENCES account.device(user_id, device_id) ON DELETE RESTRICT
);

CREATE INDEX ix_device_sync_cursor_user
    ON sync.device_sync_cursor (user_id, updated_at);

CREATE TABLE sync.tombstone (
    tombstone_id uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    aggregate_type text NOT NULL CHECK (length(aggregate_type) BETWEEN 1 AND 100),
    aggregate_id uuid NOT NULL,
    deleted_by_event_id uuid NOT NULL REFERENCES sync.sync_event(event_id) ON DELETE RESTRICT,
    deleted_at timestamptz NOT NULL,
    retain_until timestamptz NOT NULL,
    CONSTRAINT fk_tombstone_event_owner
        FOREIGN KEY (user_id, deleted_by_event_id)
        REFERENCES sync.sync_event(user_id, event_id) ON DELETE RESTRICT,
    CONSTRAINT uq_tombstone_aggregate UNIQUE (user_id, aggregate_type, aggregate_id),
    CONSTRAINT ck_tombstone_retention CHECK (retain_until > deleted_at)
);

CREATE INDEX ix_tombstone_retention
    ON sync.tombstone (retain_until);

CREATE TABLE sync.idempotency_record (
    scope text NOT NULL CHECK (length(scope) BETWEEN 1 AND 300),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 300),
    request_hash bytea NOT NULL,
    response_code integer,
    response_reference jsonb,
    status text NOT NULL DEFAULT 'IN_PROGRESS',
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (scope, idempotency_key),
    CONSTRAINT ck_idempotency_request_hash_len
        CHECK (octet_length(request_hash) = 32),
    CONSTRAINT ck_idempotency_status
        CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')),
    CONSTRAINT ck_idempotency_expiry
        CHECK (expires_at > created_at)
);

CREATE INDEX ix_idempotency_record_expiry
    ON sync.idempotency_record (expires_at);

-- -----------------------------------------------------------------------------
-- durable jobs
-- -----------------------------------------------------------------------------

CREATE TABLE jobs.job (
    job_id uuid PRIMARY KEY DEFAULT uuidv7(),
    job_type text NOT NULL CHECK (length(job_type) BETWEEN 1 AND 200),
    schema_version integer NOT NULL CHECK (schema_version >= 1),
    user_id uuid REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    priority smallint NOT NULL DEFAULT 3 CHECK (priority BETWEEN 0 AND 4),
    state text NOT NULL DEFAULT 'QUEUED',
    idempotency_scope text,
    idempotency_key text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    checkpoint jsonb,
    progress_current bigint CHECK (progress_current IS NULL OR progress_current >= 0),
    progress_total bigint CHECK (progress_total IS NULL OR progress_total >= 0),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    scheduled_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_deadline timestamptz,
    heartbeat_at timestamptz,
    cancel_requested_at timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    error_code text,
    error_detail jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    CONSTRAINT ck_job_state
        CHECK (state IN ('QUEUED', 'RUNNING', 'RETRY_WAIT', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED')),
    CONSTRAINT ck_job_idempotency_pair
        CHECK (
            (idempotency_scope IS NULL AND idempotency_key IS NULL)
            OR
            (idempotency_scope IS NOT NULL AND idempotency_key IS NOT NULL)
        ),
    CONSTRAINT ck_job_progress
        CHECK (
            progress_total IS NULL
            OR progress_current IS NULL
            OR progress_current <= progress_total
        ),
    CONSTRAINT ck_job_lease_fields
        CHECK (
            state = 'RUNNING'
            OR (lease_owner IS NULL AND lease_deadline IS NULL AND heartbeat_at IS NULL)
        )
);

CREATE UNIQUE INDEX uq_job_idempotency
    ON jobs.job (idempotency_scope, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX ix_job_claim
    ON jobs.job (priority, scheduled_at, created_at)
    WHERE state IN ('QUEUED', 'RETRY_WAIT');

CREATE INDEX ix_job_expired_lease
    ON jobs.job (lease_deadline)
    WHERE state = 'RUNNING';

CREATE TABLE jobs.job_attempt (
    job_attempt_id uuid PRIMARY KEY DEFAULT uuidv7(),
    job_id uuid NOT NULL REFERENCES jobs.job(job_id) ON DELETE CASCADE,
    attempt_no integer NOT NULL CHECK (attempt_no >= 1),
    worker_id text NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 300),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    outcome text,
    error_code text,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_job_attempt_number UNIQUE (job_id, attempt_no),
    CONSTRAINT ck_job_attempt_outcome
        CHECK (
            outcome IS NULL
            OR outcome IN ('SUCCESS', 'RETRYABLE_ERROR', 'TERMINAL_ERROR', 'LEASE_EXPIRED', 'CANCELLED')
        ),
    CONSTRAINT ck_job_attempt_finish
        CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE jobs.job_dependency (
    job_id uuid NOT NULL REFERENCES jobs.job(job_id) ON DELETE CASCADE,
    depends_on_job_id uuid NOT NULL REFERENCES jobs.job(job_id) ON DELETE RESTRICT,
    dependency_policy text NOT NULL DEFAULT 'REQUIRE_SUCCESS',
    PRIMARY KEY (job_id, depends_on_job_id),
    CONSTRAINT ck_job_dependency_not_self CHECK (job_id <> depends_on_job_id),
    CONSTRAINT ck_job_dependency_policy
        CHECK (dependency_policy IN ('REQUIRE_SUCCESS', 'REQUIRE_TERMINAL'))
);

CREATE FUNCTION app_private.prevent_job_dependency_cycle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        WITH RECURSIVE deps(job_id) AS (
            SELECT NEW.depends_on_job_id
            UNION ALL
            SELECT jd.depends_on_job_id
            FROM jobs.job_dependency jd
            JOIN deps d ON jd.job_id = d.job_id
        )
        SELECT 1 FROM deps WHERE job_id = NEW.job_id
    ) THEN
        RAISE EXCEPTION 'job dependency cycle detected';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_job_dependency_no_cycle
BEFORE INSERT OR UPDATE ON jobs.job_dependency
FOR EACH ROW EXECUTE FUNCTION app_private.prevent_job_dependency_cycle();

-- -----------------------------------------------------------------------------
-- user library
-- -----------------------------------------------------------------------------

CREATE TABLE library.user_track_ref (
    user_track_ref_id uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    recording_id uuid REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    resolution_status text NOT NULL DEFAULT 'UNRESOLVED',
    raw_title text,
    raw_artist text,
    raw_album text,
    raw_duration_ms bigint CHECK (raw_duration_ms IS NULL OR raw_duration_ms > 0),
    resolved_at timestamptz,
    resolution_confidence numeric(5,4),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT ck_user_track_ref_resolution_status
        CHECK (resolution_status IN ('UNRESOLVED', 'CANDIDATES', 'RESOLVED', 'AMBIGUOUS', 'NOT_FOUND')),
    CONSTRAINT ck_user_track_ref_resolution_target
        CHECK (
            (resolution_status = 'RESOLVED' AND recording_id IS NOT NULL AND resolved_at IS NOT NULL)
            OR
            (resolution_status <> 'RESOLVED' AND recording_id IS NULL)
        ),
    CONSTRAINT ck_user_track_ref_resolution_confidence
        CHECK (resolution_confidence IS NULL OR resolution_confidence BETWEEN 0 AND 1)
);

CREATE UNIQUE INDEX uq_user_track_ref_active_recording
    ON library.user_track_ref (user_id, recording_id)
    WHERE recording_id IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX ix_user_track_ref_user_status
    ON library.user_track_ref (user_id, resolution_status, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE library.user_track_ref_external_reference (
    user_track_ref_id uuid NOT NULL
        REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE CASCADE,
    external_reference_id uuid NOT NULL
        REFERENCES identity.external_reference(external_reference_id) ON DELETE RESTRICT,
    relation_role text NOT NULL DEFAULT 'ALIAS',
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_track_ref_id, external_reference_id),
    CONSTRAINT ck_user_track_ref_external_role
        CHECK (relation_role IN ('PRIMARY_SOURCE', 'ALIAS', 'IMPORT_EVIDENCE'))
);

CREATE INDEX ix_user_track_ref_external_reverse
    ON library.user_track_ref_external_reference (external_reference_id, user_track_ref_id);

CREATE TABLE library.library_entry (
    library_entry_id uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    user_track_ref_id uuid NOT NULL
        REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE RESTRICT,
    added_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL,
    availability_status text NOT NULL DEFAULT 'PENDING',
    removed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    CONSTRAINT ck_library_entry_source
        CHECK (source IN ('LOCAL', 'IMPORT', 'SEARCH', 'SHARE', 'RESTORE')),
    CONSTRAINT ck_library_entry_availability
        CHECK (availability_status IN ('LOCAL', 'VAULT', 'EXTERNAL', 'PENDING', 'NOT_FOUND', 'AMBIGUOUS'))
);

CREATE UNIQUE INDEX uq_library_entry_active
    ON library.library_entry (user_id, user_track_ref_id)
    WHERE removed_at IS NULL;

CREATE INDEX ix_library_entry_page
    ON library.library_entry (user_id, added_at DESC, library_entry_id)
    WHERE removed_at IS NULL;

CREATE TABLE library.user_track_preference (
    user_track_ref_id uuid PRIMARY KEY
        REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE CASCADE,
    preference text NOT NULL DEFAULT 'NEUTRAL',
    rating smallint,
    excluded_from_taste boolean NOT NULL DEFAULT false,
    updated_by_event_id uuid REFERENCES sync.sync_event(event_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    CONSTRAINT ck_user_track_preference
        CHECK (preference IN ('NEUTRAL', 'LIKED', 'DISLIKED')),
    CONSTRAINT ck_user_track_rating
        CHECK (rating IS NULL OR rating BETWEEN 1 AND 5)
);

CREATE FUNCTION app_private.enforce_library_entry_owner()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    ref_user_id uuid;
BEGIN
    SELECT utr.user_id INTO ref_user_id
    FROM library.user_track_ref utr
    WHERE utr.user_track_ref_id = NEW.user_track_ref_id;

    IF ref_user_id IS DISTINCT FROM NEW.user_id THEN
        RAISE EXCEPTION 'library entry and user track reference owners differ';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_library_entry_owner
BEFORE INSERT OR UPDATE ON library.library_entry
FOR EACH ROW EXECUTE FUNCTION app_private.enforce_library_entry_owner();

-- -----------------------------------------------------------------------------
-- playlists
-- -----------------------------------------------------------------------------

CREATE TABLE playlist.playlist (
    playlist_id uuid PRIMARY KEY DEFAULT uuidv7(),
    owner_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 500),
    description text,
    visibility text NOT NULL DEFAULT 'PRIVATE',
    playlist_type text NOT NULL DEFAULT 'MANUAL',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT ck_playlist_visibility
        CHECK (visibility IN ('PRIVATE', 'SHARED', 'PUBLIC')),
    CONSTRAINT ck_playlist_type
        CHECK (playlist_type IN ('MANUAL', 'SMART', 'SYSTEM'))
);

CREATE INDEX ix_playlist_owner_active
    ON playlist.playlist (owner_user_id, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE playlist.playlist_entry (
    playlist_entry_id uuid PRIMARY KEY DEFAULT uuidv7(),
    playlist_id uuid NOT NULL REFERENCES playlist.playlist(playlist_id) ON DELETE RESTRICT,
    user_track_ref_id uuid NOT NULL
        REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE RESTRICT,
    position_key text NOT NULL CHECK (length(position_key) BETWEEN 1 AND 128),
    added_by_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    added_at timestamptz NOT NULL DEFAULT now(),
    source_position integer CHECK (source_position IS NULL OR source_position >= 0),
    removed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1)
);

CREATE UNIQUE INDEX uq_playlist_entry_active_position
    ON playlist.playlist_entry (playlist_id, position_key)
    WHERE removed_at IS NULL;

CREATE INDEX ix_playlist_entry_order
    ON playlist.playlist_entry (playlist_id, position_key, playlist_entry_id)
    WHERE removed_at IS NULL;

CREATE TABLE playlist.smart_playlist_rule (
    playlist_id uuid PRIMARY KEY REFERENCES playlist.playlist(playlist_id) ON DELETE CASCADE,
    rule_schema_version integer NOT NULL CHECK (rule_schema_version >= 1),
    rule_json jsonb NOT NULL,
    compiled_hash bytea NOT NULL,
    last_validated_at timestamptz NOT NULL,
    CONSTRAINT ck_smart_playlist_rule_hash_len
        CHECK (octet_length(compiled_hash) = 32)
);

CREATE FUNCTION app_private.enforce_playlist_entry_owner()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    playlist_owner uuid;
    ref_owner uuid;
BEGIN
    SELECT p.owner_user_id INTO playlist_owner
    FROM playlist.playlist p
    WHERE p.playlist_id = NEW.playlist_id;

    SELECT utr.user_id INTO ref_owner
    FROM library.user_track_ref utr
    WHERE utr.user_track_ref_id = NEW.user_track_ref_id;

    IF playlist_owner IS DISTINCT FROM ref_owner
       OR playlist_owner IS DISTINCT FROM NEW.added_by_user_id THEN
        RAISE EXCEPTION 'playlist entry crosses the v1 owner boundary';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_playlist_entry_owner
BEFORE INSERT OR UPDATE ON playlist.playlist_entry
FOR EACH ROW EXECUTE FUNCTION app_private.enforce_playlist_entry_owner();

-- -----------------------------------------------------------------------------
-- vault
-- -----------------------------------------------------------------------------

CREATE TABLE vault.vault_object (
    vault_object_id uuid PRIMARY KEY DEFAULT uuidv7(),
    sha256 bytea NOT NULL UNIQUE,
    byte_size bigint NOT NULL CHECK (byte_size > 0),
    detected_mime_type text NOT NULL CHECK (length(detected_mime_type) BETWEEN 1 AND 200),
    commit_status text NOT NULL DEFAULT 'STAGING',
    committed_at timestamptz,
    last_verified_at timestamptz,
    verification_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    CONSTRAINT ck_vault_object_sha256_len CHECK (octet_length(sha256) = 32),
    CONSTRAINT ck_vault_object_commit_status
        CHECK (commit_status IN ('STAGING', 'COMMITTED', 'QUARANTINED', 'DELETED')),
    CONSTRAINT ck_vault_object_committed_at
        CHECK (commit_status <> 'COMMITTED' OR committed_at IS NOT NULL)
);

CREATE INDEX ix_vault_object_status
    ON vault.vault_object (commit_status, created_at);

CREATE TABLE vault.vault_replica (
    vault_replica_id uuid PRIMARY KEY DEFAULT uuidv7(),
    vault_object_id uuid NOT NULL REFERENCES vault.vault_object(vault_object_id) ON DELETE RESTRICT,
    storage_backend text NOT NULL CHECK (length(storage_backend) BETWEEN 1 AND 100),
    storage_key text NOT NULL CHECK (length(storage_key) BETWEEN 1 AND 2000),
    replica_status text NOT NULL DEFAULT 'COPYING',
    verified_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    CONSTRAINT uq_vault_replica_backend_key UNIQUE (storage_backend, storage_key),
    CONSTRAINT ck_vault_replica_status
        CHECK (replica_status IN ('AVAILABLE', 'MISSING', 'CORRUPT', 'COPYING', 'QUARANTINED'))
);

CREATE INDEX ix_vault_replica_object_status
    ON vault.vault_replica (vault_object_id, replica_status);

CREATE TABLE vault.audio_variant (
    audio_variant_id uuid PRIMARY KEY DEFAULT uuidv7(),
    recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    vault_object_id uuid NOT NULL UNIQUE
        REFERENCES vault.vault_object(vault_object_id) ON DELETE RESTRICT,
    codec text NOT NULL CHECK (length(codec) BETWEEN 1 AND 100),
    container text NOT NULL CHECK (length(container) BETWEEN 1 AND 100),
    bitrate_bps integer CHECK (bitrate_bps IS NULL OR bitrate_bps > 0),
    bit_depth integer CHECK (bit_depth IS NULL OR bit_depth > 0),
    sample_rate_hz integer NOT NULL CHECK (sample_rate_hz > 0),
    channels integer NOT NULL CHECK (channels BETWEEN 1 AND 64),
    duration_ms bigint NOT NULL CHECK (duration_ms > 0),
    validation_status text NOT NULL DEFAULT 'VALID',
    quality_score numeric(7,4),
    quality_policy_version text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    deleted_at timestamptz,
    CONSTRAINT ck_audio_variant_validation_status
        CHECK (validation_status IN ('VALID', 'SUSPECT', 'INVALID', 'QUARANTINED')),
    CONSTRAINT ck_audio_variant_quality_score
        CHECK (quality_score IS NULL OR quality_score >= 0)
);

CREATE INDEX ix_audio_variant_recording_valid
    ON vault.audio_variant (recording_id, quality_score DESC NULLS LAST)
    WHERE validation_status = 'VALID' AND deleted_at IS NULL;

CREATE TABLE vault.audio_fingerprint (
    audio_fingerprint_id uuid PRIMARY KEY DEFAULT uuidv7(),
    audio_variant_id uuid NOT NULL
        REFERENCES vault.audio_variant(audio_variant_id) ON DELETE CASCADE,
    algorithm text NOT NULL CHECK (length(algorithm) BETWEEN 1 AND 100),
    algorithm_version text NOT NULL CHECK (length(algorithm_version) BETWEEN 1 AND 100),
    tool_build_sha256 bytea,
    decoder_name text,
    decoder_version text,
    duration_ms bigint NOT NULL CHECK (duration_ms > 0),
    fingerprint_hash bytea,
    fingerprint_payload bytea,
    quality_flags jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_audio_fingerprint_variant_version
        UNIQUE (audio_variant_id, algorithm, algorithm_version),
    CONSTRAINT ck_audio_fingerprint_payload
        CHECK (fingerprint_hash IS NOT NULL OR fingerprint_payload IS NOT NULL),
    CONSTRAINT ck_audio_fingerprint_tool_hash_len
        CHECK (tool_build_sha256 IS NULL OR octet_length(tool_build_sha256) = 32)
);

CREATE INDEX ix_audio_fingerprint_candidate
    ON vault.audio_fingerprint (algorithm, algorithm_version, fingerprint_hash)
    WHERE fingerprint_hash IS NOT NULL;

CREATE TABLE vault.recording_canonical_variant (
    recording_id uuid PRIMARY KEY REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    audio_variant_id uuid NOT NULL UNIQUE
        REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
    policy_version text NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 100),
    reason jsonb NOT NULL DEFAULT '{}'::jsonb,
    selected_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION app_private.enforce_canonical_variant_recording()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    variant_recording_id uuid;
BEGIN
    SELECT av.recording_id INTO variant_recording_id
    FROM vault.audio_variant av
    WHERE av.audio_variant_id = NEW.audio_variant_id;

    IF variant_recording_id IS DISTINCT FROM NEW.recording_id THEN
        RAISE EXCEPTION 'canonical audio variant belongs to another recording';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_canonical_variant_recording
BEFORE INSERT OR UPDATE ON vault.recording_canonical_variant
FOR EACH ROW EXECUTE FUNCTION app_private.enforce_canonical_variant_recording();

CREATE TABLE vault.acquisition_record (
    acquisition_record_id uuid PRIMARY KEY DEFAULT uuidv7(),
    audio_variant_id uuid NOT NULL REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
    provider_id uuid NOT NULL REFERENCES identity.source_provider(provider_id) ON DELETE RESTRICT,
    external_reference_id uuid REFERENCES identity.external_reference(external_reference_id) ON DELETE RESTRICT,
    authorized_by_user_id uuid REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    rights_capability text NOT NULL,
    source_uri_encrypted bytea,
    acquired_at timestamptz NOT NULL DEFAULT now(),
    adapter_version text,
    CONSTRAINT ck_acquisition_rights_capability
        CHECK (rights_capability IN ('AUTHORIZED_DOWNLOAD', 'USER_UPLOAD', 'LOCAL_IMPORT', 'RESTORE'))
);

CREATE INDEX ix_acquisition_record_variant
    ON vault.acquisition_record (audio_variant_id, acquired_at DESC);

-- -----------------------------------------------------------------------------
-- library migration
-- -----------------------------------------------------------------------------

CREATE TABLE importing.import_job (
    import_job_id uuid PRIMARY KEY DEFAULT uuidv7(),
    job_id uuid NOT NULL UNIQUE REFERENCES jobs.job(job_id) ON DELETE RESTRICT,
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    adapter_id text NOT NULL CHECK (length(adapter_id) BETWEEN 1 AND 200),
    adapter_version text NOT NULL CHECK (length(adapter_version) BETWEEN 1 AND 100),
    input_sha256 bytea NOT NULL,
    input_schema_version text,
    mode text NOT NULL,
    checkpoint jsonb,
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    CONSTRAINT ck_import_job_hash_len CHECK (octet_length(input_sha256) = 32),
    CONSTRAINT ck_import_job_mode CHECK (mode IN ('LIBRARY_ONLY', 'MATERIALIZE'))
);

CREATE INDEX ix_import_job_user
    ON importing.import_job (user_id, created_at DESC);

CREATE TABLE importing.import_entry (
    import_entry_id uuid PRIMARY KEY DEFAULT uuidv7(),
    import_job_id uuid NOT NULL REFERENCES importing.import_job(import_job_id) ON DELETE CASCADE,
    source_row_key text NOT NULL CHECK (length(source_row_key) BETWEEN 1 AND 1000),
    raw_title text NOT NULL,
    raw_artist text NOT NULL,
    raw_album text,
    raw_duration_ms bigint CHECK (raw_duration_ms IS NULL OR raw_duration_ms > 0),
    raw_external_id text,
    raw_payload jsonb,
    match_status text NOT NULL DEFAULT 'PENDING',
    selected_recording_id uuid REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    user_track_ref_id uuid REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    CONSTRAINT uq_import_entry_source_row UNIQUE (import_job_id, source_row_key),
    CONSTRAINT ck_import_entry_match_status
        CHECK (match_status IN ('PENDING', 'AUTO_MATCH', 'REVIEW_REQUIRED', 'NO_MATCH', 'REJECTED')),
    CONSTRAINT ck_import_entry_selected_recording
        CHECK (
            match_status NOT IN ('AUTO_MATCH')
            OR selected_recording_id IS NOT NULL
        )
);

CREATE INDEX ix_import_entry_job_status
    ON importing.import_entry (import_job_id, match_status, source_row_key);

CREATE TABLE importing.match_candidate (
    match_candidate_id uuid PRIMARY KEY DEFAULT uuidv7(),
    import_entry_id uuid NOT NULL REFERENCES importing.import_entry(import_entry_id) ON DELETE CASCADE,
    recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    rank integer NOT NULL CHECK (rank >= 1),
    raw_score numeric(7,6) NOT NULL CHECK (raw_score BETWEEN 0 AND 1),
    confidence numeric(7,6) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_tier text NOT NULL,
    feature_scores jsonb NOT NULL,
    hard_conflicts jsonb NOT NULL DEFAULT '[]'::jsonb,
    candidate_generation_version text NOT NULL,
    matcher_version text NOT NULL,
    calibrator_version text,
    threshold_set_version text,
    decision text NOT NULL DEFAULT 'NONE',
    decided_by_user_id uuid REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_match_candidate_version
        UNIQUE (import_entry_id, recording_id, matcher_version),
    CONSTRAINT uq_match_candidate_rank
        UNIQUE (import_entry_id, matcher_version, rank),
    CONSTRAINT ck_match_candidate_evidence_tier
        CHECK (evidence_tier IN ('T0', 'T1', 'T2', 'T3', 'T4')),
    CONSTRAINT ck_match_candidate_decision
        CHECK (decision IN ('NONE', 'ACCEPTED', 'REJECTED')),
    CONSTRAINT ck_match_candidate_actor_time
        CHECK (
            (decision = 'NONE' AND decided_at IS NULL)
            OR
            (decision <> 'NONE' AND decided_at IS NOT NULL)
        )
);

CREATE INDEX ix_match_candidate_entry_rank
    ON importing.match_candidate (import_entry_id, matcher_version, rank);

-- -----------------------------------------------------------------------------
-- ML and recommendations
-- -----------------------------------------------------------------------------

CREATE TABLE ml.embedding_model (
    embedding_model_id uuid PRIMARY KEY DEFAULT uuidv7(),
    model_key text NOT NULL CHECK (length(model_key) BETWEEN 1 AND 300),
    version text NOT NULL CHECK (length(version) BETWEEN 1 AND 200),
    task text NOT NULL CHECK (length(task) BETWEEN 1 AND 100),
    weights_sha256 bytea NOT NULL,
    license_id text NOT NULL CHECK (length(license_id) BETWEEN 1 AND 200),
    runtime text NOT NULL CHECK (length(runtime) BETWEEN 1 AND 200),
    inference_precision text NOT NULL CHECK (length(inference_precision) BETWEEN 1 AND 50),
    input_sample_rate_hz integer NOT NULL CHECK (input_sample_rate_hz > 0),
    segment_duration_ms integer NOT NULL CHECK (segment_duration_ms > 0),
    preprocessing_version text NOT NULL CHECK (length(preprocessing_version) BETWEEN 1 AND 200),
    pooling_strategy text NOT NULL CHECK (length(pooling_strategy) BETWEEN 1 AND 200),
    dimension integer NOT NULL CHECK (dimension BETWEEN 1 AND 16000),
    status text NOT NULL DEFAULT 'BENCHMARK',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    CONSTRAINT ck_embedding_model_weights_hash_len
        CHECK (octet_length(weights_sha256) = 32),
    CONSTRAINT ck_embedding_model_status
        CHECK (status IN ('BENCHMARK', 'ACTIVE', 'RETIRED', 'BLOCKED')),
    CONSTRAINT uq_embedding_model_version
        UNIQUE (model_key, version, preprocessing_version, pooling_strategy)
);

CREATE UNIQUE INDEX uq_embedding_model_single_active_task
    ON ml.embedding_model (task)
    WHERE status = 'ACTIVE';

CREATE TABLE ml.recording_embedding (
    recording_embedding_id uuid PRIMARY KEY DEFAULT uuidv7(),
    recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    embedding_model_id uuid NOT NULL
        REFERENCES ml.embedding_model(embedding_model_id) ON DELETE RESTRICT,
    audio_variant_id uuid NOT NULL REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
    embedding vector NOT NULL,
    normalized boolean NOT NULL DEFAULT true,
    quality_flags jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_recording_embedding_source
        UNIQUE (recording_id, embedding_model_id, audio_variant_id)
);

CREATE INDEX ix_recording_embedding_model_recording
    ON ml.recording_embedding (embedding_model_id, recording_id);

CREATE FUNCTION app_private.enforce_embedding_dimension_and_recording()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected_dimension integer;
    variant_recording_id uuid;
BEGIN
    SELECT em.dimension INTO expected_dimension
    FROM ml.embedding_model em
    WHERE em.embedding_model_id = NEW.embedding_model_id;

    SELECT av.recording_id INTO variant_recording_id
    FROM vault.audio_variant av
    WHERE av.audio_variant_id = NEW.audio_variant_id;

    IF vector_dims(NEW.embedding) <> expected_dimension THEN
        RAISE EXCEPTION 'embedding dimension does not match model registry';
    END IF;

    IF variant_recording_id IS DISTINCT FROM NEW.recording_id THEN
        RAISE EXCEPTION 'embedding source variant belongs to another recording';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_recording_embedding_integrity
BEFORE INSERT OR UPDATE ON ml.recording_embedding
FOR EACH ROW EXECUTE FUNCTION app_private.enforce_embedding_dimension_and_recording();

CREATE TABLE ml.recommendation_request (
    recommendation_request_id uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    context text NOT NULL DEFAULT 'GENERAL',
    model_bundle_version text NOT NULL,
    candidate_policy_version text NOT NULL,
    filter_policy_version text NOT NULL,
    reranker_version text NOT NULL,
    seed bigint NOT NULL,
    request_features jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_recommendation_request_context
        CHECK (context IN ('GENERAL', 'WORKOUT', 'CYCLING', 'WORK', 'SLEEP', 'PARTY'))
);

CREATE INDEX ix_recommendation_request_user_time
    ON ml.recommendation_request (user_id, created_at DESC);

CREATE TABLE ml.recommendation_item (
    recommendation_request_id uuid NOT NULL
        REFERENCES ml.recommendation_request(recommendation_request_id) ON DELETE CASCADE,
    rank integer NOT NULL CHECK (rank >= 1),
    recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    score numeric NOT NULL,
    candidate_sources text[] NOT NULL DEFAULT ARRAY[]::text[],
    explanation_code text NOT NULL,
    availability_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (recommendation_request_id, rank),
    CONSTRAINT uq_recommendation_item_recording
        UNIQUE (recommendation_request_id, recording_id)
);

CREATE INDEX ix_recommendation_item_recording
    ON ml.recommendation_item (recording_id, recommendation_request_id);

CREATE TABLE ml.taste_cluster (
    taste_cluster_id uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    context text NOT NULL DEFAULT 'GENERAL',
    model_bundle_version text NOT NULL,
    centroid vector NOT NULL,
    weight numeric NOT NULL CHECK (weight >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    retired_at timestamptz,
    CONSTRAINT ck_taste_cluster_context
        CHECK (context IN ('GENERAL', 'WORKOUT', 'CYCLING', 'WORK', 'SLEEP', 'PARTY'))
);

CREATE INDEX ix_taste_cluster_user_active
    ON ml.taste_cluster (user_id, context, model_bundle_version)
    WHERE retired_at IS NULL;

CREATE TABLE ml.taste_cluster_member (
    taste_cluster_id uuid NOT NULL REFERENCES ml.taste_cluster(taste_cluster_id) ON DELETE CASCADE,
    user_track_ref_id uuid NOT NULL
        REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE RESTRICT,
    membership_score numeric NOT NULL CHECK (membership_score BETWEEN 0 AND 1),
    explicit_weight numeric NOT NULL DEFAULT 1 CHECK (explicit_weight >= 0),
    PRIMARY KEY (taste_cluster_id, user_track_ref_id)
);

CREATE FUNCTION app_private.enforce_taste_cluster_member_owner()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    cluster_user_id uuid;
    ref_user_id uuid;
BEGIN
    SELECT tc.user_id INTO cluster_user_id
    FROM ml.taste_cluster tc
    WHERE tc.taste_cluster_id = NEW.taste_cluster_id;

    SELECT utr.user_id INTO ref_user_id
    FROM library.user_track_ref utr
    WHERE utr.user_track_ref_id = NEW.user_track_ref_id;

    IF cluster_user_id IS DISTINCT FROM ref_user_id THEN
        RAISE EXCEPTION 'taste cluster member crosses user boundary';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_taste_cluster_member_owner
BEFORE INSERT OR UPDATE ON ml.taste_cluster_member
FOR EACH ROW EXECUTE FUNCTION app_private.enforce_taste_cluster_member_owner();

CREATE TABLE ml.offline_recommendation_pack (
    offline_pack_id uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    device_id uuid REFERENCES account.device(device_id) ON DELETE RESTRICT,
    catalog_snapshot bigint NOT NULL CHECK (catalog_snapshot >= 0),
    model_bundle_version text NOT NULL,
    payload_version integer NOT NULL CHECK (payload_version >= 1),
    payload_encoding text NOT NULL,
    payload bytea NOT NULL,
    payload_sha256 bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CONSTRAINT fk_offline_pack_device_owner
        FOREIGN KEY (user_id, device_id)
        REFERENCES account.device(user_id, device_id) ON DELETE RESTRICT,
    CONSTRAINT ck_offline_pack_encoding
        CHECK (payload_encoding IN ('JSON_ZSTD', 'PROTOBUF_ZSTD')),
    CONSTRAINT ck_offline_pack_hash_len
        CHECK (octet_length(payload_sha256) = 32),
    CONSTRAINT ck_offline_pack_expiry
        CHECK (expires_at > created_at)
);

CREATE INDEX ix_offline_pack_user_device
    ON ml.offline_recommendation_pack (user_id, device_id, expires_at DESC);

-- HNSW is intentionally absent from the initial migration. The embedding
-- dimension and active model are selected by the RTX 3060 benchmark. A later
-- model-specific migration may create a partial expression index, for example:
-- CREATE INDEX ... USING hnsw ((embedding::vector(N)) vector_cosine_ops)
-- WHERE embedding_model_id = '<active-model-id>';

-- -----------------------------------------------------------------------------
-- listening history
-- -----------------------------------------------------------------------------

CREATE TABLE library.listening_event (
    listening_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
    user_track_ref_id uuid NOT NULL
        REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE RESTRICT,
    recording_id uuid REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    started_at timestamptz NOT NULL,
    played_ms bigint NOT NULL DEFAULT 0 CHECK (played_ms >= 0),
    track_duration_ms bigint CHECK (track_duration_ms IS NULL OR track_duration_ms > 0),
    completion_ratio numeric(7,6) CHECK (completion_ratio IS NULL OR completion_ratio BETWEEN 0 AND 1),
    event_origin text NOT NULL DEFAULT 'ORGANIC',
    context text NOT NULL DEFAULT 'GENERAL',
    recommendation_request_id uuid
        REFERENCES ml.recommendation_request(recommendation_request_id) ON DELETE RESTRICT,
    explicit_feedback text NOT NULL DEFAULT 'NONE',
    excluded_from_taste boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_listening_event_device_owner
        FOREIGN KEY (user_id, device_id)
        REFERENCES account.device(user_id, device_id) ON DELETE RESTRICT,
    CONSTRAINT ck_listening_event_origin
        CHECK (event_origin IN ('ORGANIC', 'RECOMMENDED', 'PLAYLIST', 'SEARCH', 'WAVE')),
    CONSTRAINT ck_listening_event_context
        CHECK (context IN ('GENERAL', 'WORKOUT', 'CYCLING', 'WORK', 'SLEEP', 'PARTY')),
    CONSTRAINT ck_listening_event_feedback
        CHECK (explicit_feedback IN ('NONE', 'LIKE', 'DISLIKE')),
    CONSTRAINT ck_listening_event_recommendation_origin
        CHECK (
            event_origin <> 'RECOMMENDED'
            OR recommendation_request_id IS NOT NULL
        )
);

CREATE INDEX ix_listening_event_user_time
    ON library.listening_event (user_id, started_at DESC, listening_event_id);

CREATE INDEX ix_listening_event_recording_time
    ON library.listening_event (recording_id, started_at DESC)
    WHERE recording_id IS NOT NULL;

CREATE FUNCTION app_private.enforce_listening_event_owner()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    ref_user_id uuid;
    recommendation_user_id uuid;
BEGIN
    SELECT utr.user_id INTO ref_user_id
    FROM library.user_track_ref utr
    WHERE utr.user_track_ref_id = NEW.user_track_ref_id;

    IF ref_user_id IS DISTINCT FROM NEW.user_id THEN
        RAISE EXCEPTION 'listening event and user track reference owners differ';
    END IF;

    IF NEW.recommendation_request_id IS NOT NULL THEN
        SELECT rr.user_id INTO recommendation_user_id
        FROM ml.recommendation_request rr
        WHERE rr.recommendation_request_id = NEW.recommendation_request_id;

        IF recommendation_user_id IS DISTINCT FROM NEW.user_id THEN
            RAISE EXCEPTION 'listening event references another user recommendation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_listening_event_owner
BEFORE INSERT OR UPDATE ON library.listening_event
FOR EACH ROW EXECUTE FUNCTION app_private.enforce_listening_event_owner();

-- -----------------------------------------------------------------------------
-- cross-table serving invariants
-- -----------------------------------------------------------------------------

CREATE FUNCTION app_private.audio_variant_is_servable(p_audio_variant_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM vault.audio_variant av
        JOIN vault.vault_object vo ON vo.vault_object_id = av.vault_object_id
        WHERE av.audio_variant_id = p_audio_variant_id
          AND av.validation_status = 'VALID'
          AND av.deleted_at IS NULL
          AND vo.commit_status = 'COMMITTED'
    );
$$;

-- -----------------------------------------------------------------------------
-- row-version triggers
-- -----------------------------------------------------------------------------

CREATE TRIGGER tr_user_account_row_version
BEFORE UPDATE ON account.user_account
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_device_row_version
BEFORE UPDATE ON account.device
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_artist_row_version
BEFORE UPDATE ON catalog.artist
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_artist_credit_row_version
BEFORE UPDATE ON catalog.artist_credit
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_work_row_version
BEFORE UPDATE ON catalog.work
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_recording_row_version
BEFORE UPDATE ON catalog.recording
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_release_group_row_version
BEFORE UPDATE ON catalog.release_group
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_release_row_version
BEFORE UPDATE ON catalog.release
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_medium_row_version
BEFORE UPDATE ON catalog.medium
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_release_track_row_version
BEFORE UPDATE ON catalog.release_track
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_source_provider_row_version
BEFORE UPDATE ON identity.source_provider
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_external_reference_row_version
BEFORE UPDATE ON identity.external_reference
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_user_track_ref_row_version
BEFORE UPDATE ON library.user_track_ref
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_library_entry_row_version
BEFORE UPDATE ON library.library_entry
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_user_track_preference_row_version
BEFORE UPDATE ON library.user_track_preference
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_playlist_row_version
BEFORE UPDATE ON playlist.playlist
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_playlist_entry_row_version
BEFORE UPDATE ON playlist.playlist_entry
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_vault_object_row_version
BEFORE UPDATE ON vault.vault_object
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_vault_replica_row_version
BEFORE UPDATE ON vault.vault_replica
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_audio_variant_row_version
BEFORE UPDATE ON vault.audio_variant
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_import_job_row_version
BEFORE UPDATE ON importing.import_job
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_import_entry_row_version
BEFORE UPDATE ON importing.import_entry
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_job_row_version
BEFORE UPDATE ON jobs.job
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

CREATE TRIGGER tr_embedding_model_row_version
BEFORE UPDATE ON ml.embedding_model
FOR EACH ROW EXECUTE FUNCTION app_private.bump_row_version();

-- -----------------------------------------------------------------------------
-- privileges
-- -----------------------------------------------------------------------------

REVOKE ALL ON ALL TABLES IN SCHEMA account, catalog, identity, library, playlist,
    vault, importing, sync, jobs, ml, audit FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA sync FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app_private FROM PUBLIC;

COMMIT;
