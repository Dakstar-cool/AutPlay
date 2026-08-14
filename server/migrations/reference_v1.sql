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

CREATE TABLE identity.matcher_release (
    matcher_version text PRIMARY KEY CHECK (length(matcher_version) BETWEEN 1 AND 200),
    candidate_generation_version text NOT NULL
        CHECK (length(candidate_generation_version) BETWEEN 1 AND 200),
    normalization_version text NOT NULL
        CHECK (length(normalization_version) BETWEEN 1 AND 200),
    feature_extractor_versions jsonb NOT NULL,
    feature_schema_version text NOT NULL
        CHECK (length(feature_schema_version) BETWEEN 1 AND 100),
    manifest_sha256 bytea NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_matcher_release_feature_manifest
        CHECK (
            jsonb_typeof(feature_extractor_versions) = 'object'
            AND octet_length(convert_to(feature_extractor_versions::text, 'UTF8')) <= 131072
        ),
    CONSTRAINT ck_matcher_release_manifest_hash_len
        CHECK (octet_length(manifest_sha256) = 32)
);

CREATE TABLE identity.calibrator_release (
    calibrator_version text PRIMARY KEY CHECK (length(calibrator_version) BETWEEN 1 AND 200),
    matcher_version text NOT NULL
        REFERENCES identity.matcher_release(matcher_version) ON DELETE RESTRICT,
    evidence_mode text NOT NULL,
    artifact_sha256 bytea NOT NULL UNIQUE,
    input_schema_version text NOT NULL
        CHECK (length(input_schema_version) BETWEEN 1 AND 100),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_calibrator_release_matcher_mode
        UNIQUE (calibrator_version, matcher_version, evidence_mode),
    CONSTRAINT ck_calibrator_release_evidence_mode
        CHECK (evidence_mode IN ('METADATA_ONLY', 'AUDIO_AVAILABLE')),
    CONSTRAINT ck_calibrator_release_artifact_hash_len
        CHECK (octet_length(artifact_sha256) = 32)
);

CREATE TABLE identity.threshold_set (
    threshold_set_version text PRIMARY KEY
        CHECK (length(threshold_set_version) BETWEEN 1 AND 200),
    matcher_version text NOT NULL
        REFERENCES identity.matcher_release(matcher_version) ON DELETE RESTRICT,
    calibrator_version text,
    evidence_mode text NOT NULL,
    minimum_evidence_tier text NOT NULL,
    auto_threshold numeric(7,6) NOT NULL CHECK (auto_threshold BETWEEN 0 AND 1),
    review_threshold numeric(7,6) NOT NULL CHECK (review_threshold BETWEEN 0 AND 1),
    margin_threshold numeric(7,6) NOT NULL CHECK (margin_threshold BETWEEN 0 AND 1),
    benchmark_report_sha256 bytea,
    gate_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    gate_metadata_schema_version text NOT NULL
        CHECK (length(gate_metadata_schema_version) BETWEEN 1 AND 100),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_threshold_set_calibrator_scope
        FOREIGN KEY (calibrator_version, matcher_version, evidence_mode)
        REFERENCES identity.calibrator_release(
            calibrator_version, matcher_version, evidence_mode
        ) ON DELETE RESTRICT,
    CONSTRAINT uq_threshold_set_scope
        UNIQUE (threshold_set_version, evidence_mode, minimum_evidence_tier),
    CONSTRAINT ck_threshold_set_evidence_mode
        CHECK (evidence_mode IN ('METADATA_ONLY', 'AUDIO_AVAILABLE', 'DETERMINISTIC_BYTES')),
    CONSTRAINT ck_threshold_set_evidence_tier
        CHECK (minimum_evidence_tier IN ('T0', 'T1', 'T2', 'T3', 'T4')),
    CONSTRAINT ck_threshold_set_order
        CHECK (auto_threshold >= review_threshold),
    CONSTRAINT ck_threshold_set_benchmark_hash_len
        CHECK (
            benchmark_report_sha256 IS NULL
            OR octet_length(benchmark_report_sha256) = 32
        ),
    CONSTRAINT ck_threshold_set_gate_metadata
        CHECK (
            jsonb_typeof(gate_metadata) = 'object'
            AND octet_length(convert_to(gate_metadata::text, 'UTF8')) <= 131072
        )
);

CREATE INDEX ix_threshold_set_scope
    ON identity.threshold_set (
        evidence_mode, minimum_evidence_tier, created_at DESC, threshold_set_version
    );

CREATE TABLE identity.match_policy_activation (
    activation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    evidence_mode text NOT NULL,
    evidence_tier text NOT NULL,
    sequence_no bigint NOT NULL CHECK (sequence_no >= 1),
    action text NOT NULL,
    threshold_set_version text NOT NULL,
    supersedes_activation_id uuid,
    actor_type text NOT NULL DEFAULT 'ADMIN',
    actor_user_id uuid NOT NULL
        REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 4000),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_match_policy_activation_threshold_scope
        FOREIGN KEY (threshold_set_version, evidence_mode, evidence_tier)
        REFERENCES identity.threshold_set(
            threshold_set_version, evidence_mode, minimum_evidence_tier
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_match_policy_activation_predecessor
        FOREIGN KEY (supersedes_activation_id)
        REFERENCES identity.match_policy_activation(activation_id) ON DELETE RESTRICT,
    CONSTRAINT uq_match_policy_activation_scope_sequence
        UNIQUE (evidence_mode, evidence_tier, sequence_no),
    CONSTRAINT uq_match_policy_activation_successor
        UNIQUE (supersedes_activation_id),
    CONSTRAINT ck_match_policy_activation_mode
        CHECK (evidence_mode IN ('METADATA_ONLY', 'AUDIO_AVAILABLE', 'DETERMINISTIC_BYTES')),
    CONSTRAINT ck_match_policy_activation_tier
        CHECK (evidence_tier IN ('T0', 'T1', 'T2', 'T3', 'T4')),
    CONSTRAINT ck_match_policy_activation_action
        CHECK (action IN ('ACTIVATE', 'DEACTIVATE', 'ROLLBACK')),
    CONSTRAINT ck_match_policy_activation_actor
        CHECK (actor_type = 'ADMIN'),
    CONSTRAINT ck_match_policy_activation_chain
        CHECK (
            (sequence_no = 1 AND supersedes_activation_id IS NULL)
            OR (sequence_no > 1 AND supersedes_activation_id IS NOT NULL)
        )
);

CREATE INDEX ix_match_policy_activation_threshold_time
    ON identity.match_policy_activation (
        threshold_set_version, created_at DESC, activation_id
    );

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
    current_match_decision_id uuid,
    resolved_at timestamptz,
    resolution_confidence numeric(7,6),
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
        CHECK (resolution_confidence IS NULL OR resolution_confidence BETWEEN 0 AND 1),
    CONSTRAINT ck_user_track_ref_decision_projection
        CHECK (
            (current_match_decision_id IS NULL
                AND resolution_status = 'UNRESOLVED'
                AND recording_id IS NULL
                AND resolution_confidence IS NULL)
            OR current_match_decision_id IS NOT NULL
        )
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
    current_match_decision_id uuid,
    selected_recording_id uuid REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    user_track_ref_id uuid REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    CONSTRAINT uq_import_entry_source_row UNIQUE (import_job_id, source_row_key),
    CONSTRAINT ck_import_entry_match_status
        CHECK (
            match_status IN (
                'PENDING', 'AUTO_MATCH', 'MANUAL_MATCH', 'MANUAL_UNRESOLVED',
                'REVIEW_REQUIRED', 'NO_MATCH', 'INTEGRITY_CONFLICT',
                'DEFERRED_EVIDENCE', 'REJECTED'
            )
        ),
    CONSTRAINT ck_import_entry_selected_recording
        CHECK (
            (match_status IN ('AUTO_MATCH', 'MANUAL_MATCH')
                AND selected_recording_id IS NOT NULL)
            OR (match_status NOT IN ('AUTO_MATCH', 'MANUAL_MATCH')
                AND selected_recording_id IS NULL)
        ),
    CONSTRAINT ck_import_entry_decision_projection
        CHECK (
            (current_match_decision_id IS NULL
                AND match_status IN ('PENDING', 'REJECTED')
                AND selected_recording_id IS NULL)
            OR current_match_decision_id IS NOT NULL
        )
);

CREATE INDEX ix_import_entry_job_status
    ON importing.import_entry (import_job_id, match_status, source_row_key);

CREATE TABLE identity.match_decision (
    decision_id uuid PRIMARY KEY DEFAULT uuidv7(),
    query_type text NOT NULL,
    owner_user_id uuid REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    device_id uuid REFERENCES account.device(device_id) ON DELETE RESTRICT,
    import_entry_id uuid REFERENCES importing.import_entry(import_entry_id) ON DELETE RESTRICT,
    user_track_ref_id uuid REFERENCES library.user_track_ref(user_track_ref_id) ON DELETE RESTRICT,
    local_audio_id uuid,
    external_reference_id uuid
        REFERENCES identity.external_reference(external_reference_id) ON DELETE RESTRICT,
    vault_object_id uuid REFERENCES vault.vault_object(vault_object_id) ON DELETE RESTRICT,
    audio_variant_id uuid REFERENCES vault.audio_variant(audio_variant_id) ON DELETE RESTRICT,
    query_snapshot jsonb NOT NULL,
    query_snapshot_schema_version text NOT NULL
        CHECK (length(query_snapshot_schema_version) BETWEEN 1 AND 100),
    snapshot_canonicalization_version text NOT NULL
        CHECK (length(snapshot_canonicalization_version) BETWEEN 1 AND 100),
    query_snapshot_sha256 bytea NOT NULL,
    decision_kind text NOT NULL,
    execution_mode text NOT NULL,
    review_action text,
    reviewed_candidate_evidence_id uuid,
    candidate_recording_id uuid REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    decision_state text NOT NULL,
    candidate_count integer NOT NULL CHECK (candidate_count BETWEEN 0 AND 100),
    candidate_evidence_sha256 bytea NOT NULL,
    candidate_evidence_size_bytes integer NOT NULL DEFAULT 0
        CHECK (candidate_evidence_size_bytes BETWEEN 0 AND 4194304),
    evidence_mode text NOT NULL,
    candidate_generation_version text NOT NULL
        CHECK (length(candidate_generation_version) BETWEEN 1 AND 200),
    normalization_version text NOT NULL
        CHECK (length(normalization_version) BETWEEN 1 AND 200),
    feature_extractor_versions jsonb NOT NULL,
    matcher_version text NOT NULL
        REFERENCES identity.matcher_release(matcher_version) ON DELETE RESTRICT,
    calibrator_version text
        REFERENCES identity.calibrator_release(calibrator_version) ON DELETE RESTRICT,
    threshold_set_version text
        REFERENCES identity.threshold_set(threshold_set_version) ON DELETE RESTRICT,
    raw_score numeric(7,6),
    confidence numeric(7,6),
    top2_confidence numeric(7,6),
    margin numeric(7,6),
    evidence_tier text,
    feature_scores jsonb NOT NULL DEFAULT '[]'::jsonb,
    hard_conflicts jsonb NOT NULL DEFAULT '[]'::jsonb,
    candidate_origins jsonb NOT NULL DEFAULT '[]'::jsonb,
    explanation_schema_version text NOT NULL
        CHECK (length(explanation_schema_version) BETWEEN 1 AND 100),
    actor_type text NOT NULL,
    actor_user_id uuid REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
    idempotency_scope text NOT NULL CHECK (length(idempotency_scope) BETWEEN 1 AND 100),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    request_sha256 bytea NOT NULL,
    supersedes_decision_id uuid
        REFERENCES identity.match_decision(decision_id) ON DELETE RESTRICT,
    supersession_reason text,
    decided_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_match_decision_idempotency UNIQUE (idempotency_scope, idempotency_key),
    CONSTRAINT uq_match_decision_successor UNIQUE (supersedes_decision_id),
    CONSTRAINT ck_match_decision_query_type
        CHECK (
            (query_type = 'IMPORT_ENTRY' AND import_entry_id IS NOT NULL
                AND owner_user_id IS NOT NULL AND device_id IS NULL
                AND num_nonnulls(user_track_ref_id, local_audio_id, external_reference_id,
                    vault_object_id, audio_variant_id) = 0)
            OR (query_type = 'USER_TRACK_REF' AND user_track_ref_id IS NOT NULL
                AND owner_user_id IS NOT NULL AND device_id IS NULL
                AND num_nonnulls(import_entry_id, local_audio_id, external_reference_id,
                    vault_object_id, audio_variant_id) = 0)
            OR (query_type = 'LOCAL_AUDIO' AND local_audio_id IS NOT NULL
                AND owner_user_id IS NOT NULL AND device_id IS NOT NULL
                AND num_nonnulls(import_entry_id, user_track_ref_id, external_reference_id,
                    vault_object_id, audio_variant_id) = 0)
            OR (query_type = 'EXTERNAL_REFERENCE' AND external_reference_id IS NOT NULL
                AND device_id IS NULL
                AND num_nonnulls(import_entry_id, user_track_ref_id, local_audio_id,
                    vault_object_id, audio_variant_id) = 0)
            OR (query_type = 'VAULT_OBJECT' AND vault_object_id IS NOT NULL
                AND device_id IS NULL
                AND num_nonnulls(import_entry_id, user_track_ref_id, local_audio_id,
                    external_reference_id, audio_variant_id) = 0)
            OR (query_type = 'AUDIO_VARIANT' AND audio_variant_id IS NOT NULL
                AND device_id IS NULL
                AND num_nonnulls(import_entry_id, user_track_ref_id, local_audio_id,
                    external_reference_id, vault_object_id) = 0)
        ),
    CONSTRAINT ck_match_decision_snapshot
        CHECK (
            jsonb_typeof(query_snapshot) = 'object'
            AND octet_length(convert_to(query_snapshot::text, 'UTF8')) <= 131072
            AND octet_length(query_snapshot_sha256) = 32
        ),
    CONSTRAINT ck_match_decision_kind_mode
        CHECK (
            decision_kind IN ('EVALUATION', 'REVIEW_ACTION')
            AND execution_mode IN ('SHADOW', 'APPLIED')
            AND (
                (decision_kind = 'EVALUATION' AND review_action IS NULL
                    AND reviewed_candidate_evidence_id IS NULL)
                OR (decision_kind = 'REVIEW_ACTION' AND execution_mode = 'APPLIED'
                    AND review_action IN ('ACCEPT', 'REJECT', 'KEEP_UNRESOLVED', 'CREATE_RECORDING')
                    AND supersedes_decision_id IS NOT NULL)
            )
        ),
    CONSTRAINT ck_match_decision_state
        CHECK (
            decision_state IN (
                'AUTO_MATCH', 'REVIEW_REQUIRED', 'NO_MATCH',
                'INTEGRITY_CONFLICT', 'DEFERRED_EVIDENCE'
            )
        ),
    CONSTRAINT ck_match_decision_evidence_mode
        CHECK (evidence_mode IN ('METADATA_ONLY', 'AUDIO_AVAILABLE', 'DETERMINISTIC_BYTES')),
    CONSTRAINT ck_match_decision_evidence_tier
        CHECK (evidence_tier IS NULL OR evidence_tier IN ('T0', 'T1', 'T2', 'T3', 'T4')),
    CONSTRAINT ck_match_decision_scores
        CHECK (
            (raw_score IS NULL OR raw_score BETWEEN 0 AND 1)
            AND (confidence IS NULL OR confidence BETWEEN 0 AND 1)
            AND (top2_confidence IS NULL OR top2_confidence BETWEEN 0 AND 1)
            AND (margin IS NULL OR margin BETWEEN 0 AND 1)
            AND (
                top2_confidence IS NULL AND margin IS NULL
                OR confidence IS NOT NULL AND top2_confidence IS NOT NULL
                    AND confidence >= top2_confidence
                    AND margin = confidence - top2_confidence
            )
        ),
    CONSTRAINT ck_match_decision_json
        CHECK (
            jsonb_typeof(feature_extractor_versions) = 'object'
            AND jsonb_typeof(feature_scores) = 'array'
            AND jsonb_typeof(hard_conflicts) = 'array'
            AND jsonb_typeof(candidate_origins) = 'array'
            AND octet_length(convert_to(feature_extractor_versions::text, 'UTF8')) <= 131072
            AND octet_length(convert_to(feature_scores::text, 'UTF8')) <= 131072
            AND octet_length(convert_to(hard_conflicts::text, 'UTF8')) <= 131072
            AND octet_length(convert_to(candidate_origins::text, 'UTF8')) <= 131072
            AND jsonb_array_length(feature_scores) <= 256
            AND jsonb_array_length(hard_conflicts) <= 64
            AND jsonb_array_length(candidate_origins) <= 256
        ),
    CONSTRAINT ck_match_decision_actor
        CHECK (
            actor_type IN ('SYSTEM', 'USER', 'ADMIN')
            AND ((actor_type = 'SYSTEM' AND actor_user_id IS NULL)
                OR (actor_type IN ('USER', 'ADMIN') AND actor_user_id IS NOT NULL))
        ),
    CONSTRAINT ck_match_decision_hashes
        CHECK (
            octet_length(query_snapshot_sha256) = 32
            AND
            octet_length(candidate_evidence_sha256) = 32
            AND octet_length(request_sha256) = 32
        ),
    CONSTRAINT ck_match_decision_auto_match
        CHECK (
            decision_state <> 'AUTO_MATCH'
            OR (decision_kind = 'EVALUATION' AND execution_mode = 'APPLIED'
                AND actor_type = 'SYSTEM' AND candidate_recording_id IS NOT NULL
                AND calibrator_version IS NOT NULL AND threshold_set_version IS NOT NULL
                AND confidence IS NOT NULL AND evidence_tier IS NOT NULL
                AND jsonb_array_length(hard_conflicts) = 0)
        ),
    CONSTRAINT ck_match_decision_supersession_reason
        CHECK (
            (supersedes_decision_id IS NULL AND supersession_reason IS NULL)
            OR (supersedes_decision_id IS NOT NULL
                AND length(supersession_reason) BETWEEN 1 AND 4000)
        )
);

CREATE INDEX ix_match_decision_query_time
    ON identity.match_decision (
        query_type, import_entry_id, user_track_ref_id, local_audio_id,
        external_reference_id, vault_object_id, audio_variant_id,
        owner_user_id, device_id, decided_at DESC, decision_id
    );

CREATE INDEX ix_match_decision_candidate_time
    ON identity.match_decision (candidate_recording_id, decided_at DESC, decision_id)
    WHERE candidate_recording_id IS NOT NULL;

CREATE INDEX ix_match_decision_matcher_time
    ON identity.match_decision (matcher_version, decided_at DESC, decision_id);

CREATE TABLE identity.match_candidate_evidence (
    match_candidate_evidence_id uuid PRIMARY KEY DEFAULT uuidv7(),
    decision_id uuid NOT NULL
        REFERENCES identity.match_decision(decision_id) ON DELETE RESTRICT,
    recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
    rank integer NOT NULL CHECK (rank BETWEEN 1 AND 100),
    raw_score numeric(7,6),
    confidence numeric(7,6),
    evidence_tier text NOT NULL,
    feature_scores jsonb NOT NULL,
    hard_conflicts jsonb NOT NULL DEFAULT '[]'::jsonb,
    candidate_origins jsonb NOT NULL,
    extractor_versions jsonb NOT NULL,
    evidence_schema_version text NOT NULL
        CHECK (length(evidence_schema_version) BETWEEN 1 AND 100),
    evidence_sha256 bytea NOT NULL,
    evidence_document_size_bytes integer NOT NULL
        CHECK (evidence_document_size_bytes BETWEEN 2 AND 131072),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_match_candidate_evidence_rank UNIQUE (decision_id, rank),
    CONSTRAINT uq_match_candidate_evidence_recording UNIQUE (decision_id, recording_id),
    CONSTRAINT uq_match_candidate_evidence_review_ref
        UNIQUE (match_candidate_evidence_id, decision_id, recording_id),
    CONSTRAINT ck_match_candidate_evidence_scores
        CHECK (
            (raw_score IS NULL OR raw_score BETWEEN 0 AND 1)
            AND (confidence IS NULL OR confidence BETWEEN 0 AND 1)
        ),
    CONSTRAINT ck_match_candidate_evidence_tier
        CHECK (evidence_tier IN ('T0', 'T1', 'T2', 'T3', 'T4')),
    CONSTRAINT ck_match_candidate_evidence_json
        CHECK (
            jsonb_typeof(feature_scores) = 'array'
            AND jsonb_typeof(hard_conflicts) = 'array'
            AND jsonb_typeof(candidate_origins) = 'array'
            AND jsonb_typeof(extractor_versions) = 'object'
            AND octet_length(convert_to(feature_scores::text, 'UTF8'))
                + octet_length(convert_to(hard_conflicts::text, 'UTF8'))
                + octet_length(convert_to(candidate_origins::text, 'UTF8'))
                + octet_length(convert_to(extractor_versions::text, 'UTF8')) <= 131072
            AND jsonb_array_length(feature_scores) <= 256
            AND jsonb_array_length(hard_conflicts) <= 64
            AND jsonb_array_length(candidate_origins) <= 256
        ),
    CONSTRAINT ck_match_candidate_evidence_hash_len
        CHECK (octet_length(evidence_sha256) = 32)
);

CREATE INDEX ix_match_candidate_evidence_recording
    ON identity.match_candidate_evidence (recording_id, decision_id);

ALTER TABLE identity.match_decision
    ADD CONSTRAINT fk_match_decision_reviewed_evidence
    FOREIGN KEY (
        reviewed_candidate_evidence_id, supersedes_decision_id, candidate_recording_id
    ) REFERENCES identity.match_candidate_evidence(
        match_candidate_evidence_id, decision_id, recording_id
    ) ON DELETE RESTRICT;

ALTER TABLE library.user_track_ref
    ADD CONSTRAINT fk_user_track_ref_current_match_decision
    FOREIGN KEY (current_match_decision_id)
    REFERENCES identity.match_decision(decision_id) ON DELETE RESTRICT;

ALTER TABLE importing.import_entry
    ADD CONSTRAINT fk_import_entry_current_match_decision
    FOREIGN KEY (current_match_decision_id)
    REFERENCES identity.match_decision(decision_id) ON DELETE RESTRICT;

CREATE FUNCTION app_private.reject_identity_history_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'identity history is append-only: %.%', TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

CREATE FUNCTION app_private.validate_match_policy_activation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    threshold_row identity.threshold_set%ROWTYPE;
    predecessor identity.match_policy_activation%ROWTYPE;
    actor_role text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'match policy activation history is append-only'
            USING ERRCODE = '55000';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(NEW.evidence_mode || ':' || NEW.evidence_tier, 0)
    );

    SELECT * INTO threshold_row
    FROM identity.threshold_set
    WHERE threshold_set_version = NEW.threshold_set_version;

    IF NOT FOUND
       OR threshold_row.evidence_mode <> NEW.evidence_mode
       OR threshold_row.minimum_evidence_tier <> NEW.evidence_tier THEN
        RAISE EXCEPTION 'threshold set does not match activation scope';
    END IF;

    IF NEW.action IN ('ACTIVATE', 'ROLLBACK')
       AND (threshold_row.calibrator_version IS NULL
            OR threshold_row.benchmark_report_sha256 IS NULL) THEN
        RAISE EXCEPTION 'activation requires calibrator and benchmark report';
    END IF;

    SELECT role INTO actor_role
    FROM account.user_account
    WHERE user_id = NEW.actor_user_id AND status = 'ACTIVE';
    IF actor_role IS NULL OR actor_role NOT IN ('OWNER', 'ADMIN') THEN
        RAISE EXCEPTION 'activation requires an active owner or admin';
    END IF;

    IF NEW.sequence_no = 1 THEN
        IF NEW.action <> 'ACTIVATE' THEN
            RAISE EXCEPTION 'the first policy event must activate a policy';
        END IF;
        IF EXISTS (
            SELECT 1 FROM identity.match_policy_activation
            WHERE evidence_mode = NEW.evidence_mode
              AND evidence_tier = NEW.evidence_tier
        ) THEN
            RAISE EXCEPTION 'activation scope already has a first event';
        END IF;
    ELSE
        SELECT * INTO predecessor
        FROM identity.match_policy_activation
        WHERE activation_id = NEW.supersedes_activation_id;
        IF NOT FOUND
           OR predecessor.evidence_mode <> NEW.evidence_mode
           OR predecessor.evidence_tier <> NEW.evidence_tier
           OR NEW.sequence_no <> predecessor.sequence_no + 1
           OR EXISTS (
                SELECT 1 FROM identity.match_policy_activation later
                WHERE later.evidence_mode = NEW.evidence_mode
                  AND later.evidence_tier = NEW.evidence_tier
                  AND later.sequence_no > predecessor.sequence_no
           ) THEN
            RAISE EXCEPTION 'activation must extend the latest same-scope event';
        END IF;
    END IF;

    IF NEW.action = 'DEACTIVATE'
       AND (NEW.sequence_no = 1
            OR predecessor.action NOT IN ('ACTIVATE', 'ROLLBACK')
            OR predecessor.threshold_set_version <> NEW.threshold_set_version) THEN
        RAISE EXCEPTION 'deactivate must close the currently active threshold set';
    END IF;

    IF NEW.action = 'ROLLBACK' AND NOT EXISTS (
        SELECT 1
        FROM identity.match_policy_activation prior
        WHERE prior.evidence_mode = NEW.evidence_mode
          AND prior.evidence_tier = NEW.evidence_tier
          AND prior.threshold_set_version = NEW.threshold_set_version
          AND prior.action = 'ACTIVATE'
          AND prior.sequence_no < NEW.sequence_no
    ) THEN
        RAISE EXCEPTION 'rollback target was not previously activated in scope';
    END IF;

    RETURN NEW;
END;
$$;

CREATE FUNCTION app_private.validate_match_decision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    decision_row identity.match_decision%ROWTYPE;
    predecessor identity.match_decision%ROWTYPE;
    matcher_row identity.matcher_release%ROWTYPE;
    calibrator_row identity.calibrator_release%ROWTYPE;
    threshold_row identity.threshold_set%ROWTYPE;
    top1 identity.match_candidate_evidence%ROWTYPE;
    top2 identity.match_candidate_evidence%ROWTYPE;
    evidence_count integer;
    evidence_size bigint;
    evidence_hash bytea;
    active_threshold text;
    query_owner uuid;
    actor_role text;
BEGIN
    IF TG_TABLE_SCHEMA = 'identity' AND TG_TABLE_NAME = 'match_candidate_evidence' THEN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'candidate evidence is append-only' USING ERRCODE = '55000';
        END IF;
        SELECT * INTO decision_row
        FROM identity.match_decision WHERE decision_id = NEW.decision_id;
    ELSIF TG_TABLE_SCHEMA = 'identity' AND TG_TABLE_NAME = 'match_decision' THEN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'match decisions are append-only' USING ERRCODE = '55000';
        END IF;
        decision_row := NEW;
    ELSIF TG_TABLE_SCHEMA = 'importing' AND TG_TABLE_NAME = 'import_entry' THEN
        IF NEW.current_match_decision_id IS NULL THEN
            IF TG_OP = 'UPDATE' AND OLD.current_match_decision_id IS NOT NULL THEN
                RAISE EXCEPTION 'import current-decision history pointer cannot be cleared';
            END IF;
            IF NEW.match_status NOT IN ('PENDING', 'REJECTED')
               OR NEW.selected_recording_id IS NOT NULL THEN
                RAISE EXCEPTION 'identity-derived import projection requires a decision';
            END IF;
            RETURN NEW;
        END IF;
        SELECT md.* INTO decision_row
        FROM identity.match_decision md
        WHERE md.decision_id = NEW.current_match_decision_id;
        SELECT ij.user_id INTO query_owner
        FROM importing.import_job ij
        WHERE ij.import_job_id = NEW.import_job_id;
        IF NOT FOUND OR decision_row.query_type <> 'IMPORT_ENTRY'
           OR decision_row.import_entry_id <> NEW.import_entry_id
           OR decision_row.owner_user_id IS DISTINCT FROM query_owner
           OR decision_row.execution_mode <> 'APPLIED'
           OR (
                NEW.match_status IN ('AUTO_MATCH', 'MANUAL_MATCH')
                AND decision_row.candidate_recording_id IS DISTINCT FROM NEW.selected_recording_id
            )
           OR NOT (CASE NEW.match_status
                WHEN 'AUTO_MATCH' THEN
                    decision_row.decision_kind = 'EVALUATION'
                    AND decision_row.decision_state = 'AUTO_MATCH'
                WHEN 'MANUAL_MATCH' THEN
                    decision_row.decision_kind = 'REVIEW_ACTION'
                    AND decision_row.review_action IN ('ACCEPT', 'CREATE_RECORDING')
                WHEN 'MANUAL_UNRESOLVED' THEN
                    decision_row.decision_kind = 'REVIEW_ACTION'
                    AND decision_row.review_action = 'KEEP_UNRESOLVED'
                WHEN 'REVIEW_REQUIRED' THEN
                    (decision_row.decision_kind = 'EVALUATION'
                        AND decision_row.decision_state = 'REVIEW_REQUIRED')
                    OR (decision_row.decision_kind = 'REVIEW_ACTION'
                        AND decision_row.review_action = 'REJECT')
                WHEN 'NO_MATCH' THEN
                    decision_row.decision_kind = 'EVALUATION'
                    AND decision_row.decision_state = 'NO_MATCH'
                WHEN 'INTEGRITY_CONFLICT' THEN
                    decision_row.decision_kind = 'EVALUATION'
                    AND decision_row.decision_state = 'INTEGRITY_CONFLICT'
                WHEN 'DEFERRED_EVIDENCE' THEN
                    decision_row.decision_kind = 'EVALUATION'
                    AND decision_row.decision_state = 'DEFERRED_EVIDENCE'
                ELSE false
           END IS TRUE)
           OR EXISTS (
                SELECT 1 FROM identity.match_decision successor
                WHERE successor.supersedes_decision_id = decision_row.decision_id
           )
           OR NEW.match_status IN ('PENDING', 'REJECTED') THEN
            RAISE EXCEPTION 'import current decision projection mismatch';
        END IF;
        RETURN NEW;
    ELSIF TG_TABLE_SCHEMA = 'library' AND TG_TABLE_NAME = 'user_track_ref' THEN
        IF NEW.current_match_decision_id IS NULL THEN
            IF TG_OP = 'UPDATE' AND OLD.current_match_decision_id IS NOT NULL THEN
                RAISE EXCEPTION 'user-track current-decision history pointer cannot be cleared';
            END IF;
            IF NEW.resolution_status <> 'UNRESOLVED'
               OR NEW.recording_id IS NOT NULL
               OR NEW.resolution_confidence IS NOT NULL THEN
                RAISE EXCEPTION 'identity-derived user-track projection requires a decision';
            END IF;
            RETURN NEW;
        END IF;
        SELECT * INTO decision_row
        FROM identity.match_decision WHERE decision_id = NEW.current_match_decision_id;
        IF NOT FOUND OR decision_row.query_type <> 'USER_TRACK_REF'
           OR decision_row.user_track_ref_id <> NEW.user_track_ref_id
           OR decision_row.owner_user_id IS DISTINCT FROM NEW.user_id
           OR decision_row.execution_mode <> 'APPLIED'
           OR (
                NEW.resolution_status = 'RESOLVED'
                AND decision_row.candidate_recording_id IS DISTINCT FROM NEW.recording_id
           )
           OR (NEW.resolution_status <> 'RESOLVED' AND NEW.recording_id IS NOT NULL)
           OR NOT (CASE NEW.resolution_status
                WHEN 'RESOLVED' THEN
                    (decision_row.decision_kind = 'EVALUATION'
                        AND decision_row.decision_state = 'AUTO_MATCH')
                    OR (decision_row.decision_kind = 'REVIEW_ACTION'
                        AND decision_row.review_action IN ('ACCEPT', 'CREATE_RECORDING'))
                WHEN 'CANDIDATES' THEN
                    (decision_row.decision_kind = 'EVALUATION'
                        AND decision_row.decision_state = 'REVIEW_REQUIRED')
                    OR (decision_row.decision_kind = 'REVIEW_ACTION'
                        AND decision_row.review_action = 'REJECT')
                WHEN 'NOT_FOUND' THEN
                    decision_row.decision_kind = 'EVALUATION'
                    AND decision_row.decision_state = 'NO_MATCH'
                WHEN 'AMBIGUOUS' THEN
                    decision_row.decision_kind = 'EVALUATION'
                    AND decision_row.decision_state = 'INTEGRITY_CONFLICT'
                WHEN 'UNRESOLVED' THEN
                    (decision_row.decision_kind = 'EVALUATION'
                        AND decision_row.decision_state = 'DEFERRED_EVIDENCE')
                    OR (decision_row.decision_kind = 'REVIEW_ACTION'
                        AND decision_row.review_action = 'KEEP_UNRESOLVED')
                ELSE false
           END IS TRUE)
           OR NEW.resolution_confidence IS DISTINCT FROM decision_row.confidence
           OR EXISTS (
                SELECT 1 FROM identity.match_decision successor
                WHERE successor.supersedes_decision_id = decision_row.decision_id
           ) THEN
            RAISE EXCEPTION 'user track reference current decision projection mismatch';
        END IF;
        RETURN NEW;
    ELSE
        RAISE EXCEPTION 'unexpected match-decision validator target';
    END IF;

    IF decision_row.query_type = 'IMPORT_ENTRY' THEN
        SELECT ij.user_id INTO query_owner
        FROM importing.import_entry ie
        JOIN importing.import_job ij ON ij.import_job_id = ie.import_job_id
        WHERE ie.import_entry_id = decision_row.import_entry_id;
        IF query_owner IS DISTINCT FROM decision_row.owner_user_id THEN
            RAISE EXCEPTION 'import query owner mismatch';
        END IF;
    ELSIF decision_row.query_type = 'USER_TRACK_REF' THEN
        SELECT user_id INTO query_owner
        FROM library.user_track_ref
        WHERE user_track_ref_id = decision_row.user_track_ref_id;
        IF query_owner IS DISTINCT FROM decision_row.owner_user_id THEN
            RAISE EXCEPTION 'user track query owner mismatch';
        END IF;
    ELSIF decision_row.query_type = 'LOCAL_AUDIO' AND NOT EXISTS (
        SELECT 1 FROM account.device d
        WHERE d.device_id = decision_row.device_id
          AND d.user_id = decision_row.owner_user_id
          AND d.revoked_at IS NULL
    ) THEN
        RAISE EXCEPTION 'local audio query device owner mismatch';
    END IF;

    IF decision_row.owner_user_id IS NOT NULL
       AND decision_row.actor_type = 'USER'
       AND decision_row.actor_user_id IS DISTINCT FROM decision_row.owner_user_id THEN
        RAISE EXCEPTION 'user actor is not the query owner';
    ELSIF decision_row.actor_type = 'ADMIN' THEN
        SELECT role INTO actor_role
        FROM account.user_account
        WHERE user_id = decision_row.actor_user_id AND status = 'ACTIVE';
        IF actor_role IS NULL OR actor_role NOT IN ('OWNER', 'ADMIN') THEN
            RAISE EXCEPTION 'admin actor requires an active owner/admin account';
        END IF;
    END IF;

    SELECT * INTO matcher_row
    FROM identity.matcher_release
    WHERE matcher_version = decision_row.matcher_version;
    IF NOT FOUND
       OR decision_row.candidate_generation_version
            <> matcher_row.candidate_generation_version
       OR decision_row.normalization_version <> matcher_row.normalization_version
       OR decision_row.feature_extractor_versions
            IS DISTINCT FROM matcher_row.feature_extractor_versions THEN
        RAISE EXCEPTION 'decision matcher release snapshot mismatch';
    END IF;

    IF decision_row.calibrator_version IS NOT NULL THEN
        SELECT * INTO calibrator_row
        FROM identity.calibrator_release
        WHERE calibrator_version = decision_row.calibrator_version;
        IF NOT FOUND
           OR calibrator_row.matcher_version <> decision_row.matcher_version
           OR calibrator_row.evidence_mode <> decision_row.evidence_mode THEN
            RAISE EXCEPTION 'decision calibrator release snapshot mismatch';
        END IF;
    END IF;

    IF decision_row.threshold_set_version IS NOT NULL THEN
        SELECT * INTO threshold_row
        FROM identity.threshold_set
        WHERE threshold_set_version = decision_row.threshold_set_version;
        IF NOT FOUND
           OR threshold_row.matcher_version <> decision_row.matcher_version
           OR threshold_row.calibrator_version
                IS DISTINCT FROM decision_row.calibrator_version
           OR threshold_row.evidence_mode <> decision_row.evidence_mode THEN
            RAISE EXCEPTION 'decision threshold release snapshot mismatch';
        END IF;
    END IF;

    SELECT count(*), COALESCE(sum(evidence_document_size_bytes), 0),
           sha256(
               COALESCE(
                   decode(string_agg(
                       encode(int4send(rank) || evidence_sha256, 'hex'), '' ORDER BY rank
                   ), 'hex'),
                   ''::bytea
               )
           )
      INTO evidence_count, evidence_size, evidence_hash
    FROM identity.match_candidate_evidence
    WHERE decision_id = decision_row.decision_id;

    IF evidence_count <> decision_row.candidate_count
       OR evidence_size <> decision_row.candidate_evidence_size_bytes
       OR evidence_hash <> decision_row.candidate_evidence_sha256
       OR EXISTS (
            SELECT 1 FROM identity.match_candidate_evidence e
            WHERE e.decision_id = decision_row.decision_id
              AND NOT EXISTS (
                  SELECT 1 FROM generate_series(1, decision_row.candidate_count) expected(rank)
                  WHERE expected.rank = e.rank
              )
       ) THEN
        RAISE EXCEPTION 'candidate evidence seal mismatch';
    END IF;

    SELECT * INTO top1 FROM identity.match_candidate_evidence
    WHERE decision_id = decision_row.decision_id AND rank = 1;
    SELECT * INTO top2 FROM identity.match_candidate_evidence
    WHERE decision_id = decision_row.decision_id AND rank = 2;

    IF decision_row.decision_kind = 'EVALUATION' AND decision_row.candidate_count > 0
       AND ((decision_row.candidate_recording_id IS NOT NULL
                AND top1.recording_id IS DISTINCT FROM decision_row.candidate_recording_id)
            OR top1.raw_score IS DISTINCT FROM decision_row.raw_score
            OR top1.confidence IS DISTINCT FROM decision_row.confidence
            OR top1.evidence_tier IS DISTINCT FROM decision_row.evidence_tier
            OR top1.feature_scores IS DISTINCT FROM decision_row.feature_scores
            OR top1.hard_conflicts IS DISTINCT FROM decision_row.hard_conflicts
            OR top1.candidate_origins IS DISTINCT FROM decision_row.candidate_origins
            OR top1.extractor_versions IS DISTINCT FROM decision_row.feature_extractor_versions) THEN
        RAISE EXCEPTION 'decision top-one summary mismatch';
    END IF;

    IF decision_row.candidate_count < 2
       AND decision_row.top2_confidence IS NOT NULL THEN
        RAISE EXCEPTION 'decision top-two presence mismatch';
    END IF;
    IF decision_row.candidate_count >= 2
       AND (top2.confidence IS NOT NULL)
          IS DISTINCT FROM (decision_row.top2_confidence IS NOT NULL) THEN
        RAISE EXCEPTION 'decision top-two presence mismatch';
    END IF;
    IF decision_row.top2_confidence IS NOT NULL
       AND top2.confidence IS DISTINCT FROM decision_row.top2_confidence THEN
        RAISE EXCEPTION 'decision top-two confidence mismatch';
    END IF;

    IF decision_row.decision_kind = 'EVALUATION'
       AND decision_row.decision_state <> 'DEFERRED_EVIDENCE'
       AND EXISTS (
            SELECT 1 FROM identity.match_candidate_evidence e
            WHERE e.decision_id = decision_row.decision_id
              AND (e.raw_score IS NULL OR e.confidence IS NULL)
       ) THEN
        RAISE EXCEPTION 'scored evaluation candidate cannot have null scores';
    END IF;

    IF decision_row.decision_kind = 'EVALUATION' THEN
        IF decision_row.execution_mode = 'SHADOW'
           AND decision_row.decision_state NOT IN (
                'REVIEW_REQUIRED', 'INTEGRITY_CONFLICT', 'DEFERRED_EVIDENCE'
           ) THEN
            RAISE EXCEPTION 'shadow evaluation cannot resolve or auto-match a query';
        END IF;
        IF decision_row.evidence_mode = 'DETERMINISTIC_BYTES'
           AND decision_row.execution_mode = 'APPLIED' THEN
            RAISE EXCEPTION 'pre-P00-D004 deterministic-byte evaluation must remain shadow';
        END IF;
        IF decision_row.decision_state = 'REVIEW_REQUIRED'
           AND (decision_row.candidate_count = 0
                OR decision_row.candidate_recording_id IS NULL) THEN
            RAISE EXCEPTION 'review-required evaluation needs a selected rank-one candidate';
        END IF;
        IF decision_row.decision_state IN ('NO_MATCH', 'INTEGRITY_CONFLICT', 'DEFERRED_EVIDENCE')
           AND decision_row.candidate_recording_id IS NOT NULL THEN
            RAISE EXCEPTION 'unresolved evaluation cannot select a recording';
        END IF;
    END IF;

    IF decision_row.supersedes_decision_id IS NOT NULL THEN
        SELECT * INTO predecessor FROM identity.match_decision
        WHERE decision_id = decision_row.supersedes_decision_id;
        IF NOT FOUND OR predecessor.query_type <> decision_row.query_type
           OR predecessor.import_entry_id IS DISTINCT FROM decision_row.import_entry_id
           OR predecessor.user_track_ref_id IS DISTINCT FROM decision_row.user_track_ref_id
           OR predecessor.local_audio_id IS DISTINCT FROM decision_row.local_audio_id
           OR predecessor.external_reference_id IS DISTINCT FROM decision_row.external_reference_id
           OR predecessor.vault_object_id IS DISTINCT FROM decision_row.vault_object_id
           OR predecessor.audio_variant_id IS DISTINCT FROM decision_row.audio_variant_id
           OR predecessor.owner_user_id IS DISTINCT FROM decision_row.owner_user_id
           OR predecessor.device_id IS DISTINCT FROM decision_row.device_id
           OR decision_row.decided_at <= predecessor.decided_at THEN
            RAISE EXCEPTION 'invalid decision supersession';
        END IF;
        IF decision_row.execution_mode = 'SHADOW'
           AND predecessor.execution_mode = 'APPLIED' THEN
            RAISE EXCEPTION 'shadow evaluation cannot supersede applied projection lineage';
        END IF;
        IF decision_row.decision_kind = 'REVIEW_ACTION' THEN
            SELECT role INTO actor_role
            FROM account.user_account
            WHERE user_id = decision_row.actor_user_id AND status = 'ACTIVE';
            IF predecessor.decision_state = 'AUTO_MATCH'
               OR decision_row.decision_state <> predecessor.decision_state
               OR decision_row.candidate_count <> predecessor.candidate_count
               OR decision_row.candidate_evidence_sha256
                    <> predecessor.candidate_evidence_sha256
               OR decision_row.candidate_evidence_size_bytes
                    <> predecessor.candidate_evidence_size_bytes
               OR decision_row.query_snapshot_schema_version
                    <> predecessor.query_snapshot_schema_version
               OR decision_row.snapshot_canonicalization_version
                    <> predecessor.snapshot_canonicalization_version
               OR decision_row.query_snapshot_sha256 <> predecessor.query_snapshot_sha256
               OR decision_row.evidence_mode <> predecessor.evidence_mode
               OR decision_row.candidate_generation_version
                    <> predecessor.candidate_generation_version
               OR decision_row.normalization_version <> predecessor.normalization_version
               OR decision_row.feature_extractor_versions
                    IS DISTINCT FROM predecessor.feature_extractor_versions
               OR decision_row.matcher_version <> predecessor.matcher_version
               OR decision_row.calibrator_version IS DISTINCT FROM predecessor.calibrator_version
               OR decision_row.threshold_set_version
                    IS DISTINCT FROM predecessor.threshold_set_version
               OR decision_row.raw_score IS DISTINCT FROM predecessor.raw_score
               OR decision_row.confidence IS DISTINCT FROM predecessor.confidence
               OR decision_row.top2_confidence IS DISTINCT FROM predecessor.top2_confidence
               OR decision_row.margin IS DISTINCT FROM predecessor.margin
               OR decision_row.evidence_tier IS DISTINCT FROM predecessor.evidence_tier
               OR decision_row.feature_scores IS DISTINCT FROM predecessor.feature_scores
               OR decision_row.hard_conflicts IS DISTINCT FROM predecessor.hard_conflicts
               OR decision_row.candidate_origins IS DISTINCT FROM predecessor.candidate_origins
               OR decision_row.explanation_schema_version
                    <> predecessor.explanation_schema_version
               OR decision_row.actor_type NOT IN ('USER', 'ADMIN')
               OR actor_role IS NULL
               OR (decision_row.actor_type = 'ADMIN'
                    AND actor_role NOT IN ('OWNER', 'ADMIN'))
               OR (decision_row.owner_user_id IS NOT NULL
                    AND decision_row.actor_user_id <> decision_row.owner_user_id
                    AND actor_role NOT IN ('OWNER', 'ADMIN')) THEN
                RAISE EXCEPTION 'invalid manual review lineage or actor';
            END IF;
            IF decision_row.review_action IN ('ACCEPT', 'REJECT')
               AND (decision_row.reviewed_candidate_evidence_id IS NULL
                    OR decision_row.candidate_recording_id IS NULL) THEN
                RAISE EXCEPTION 'accept/reject requires predecessor candidate evidence';
            ELSIF decision_row.review_action = 'KEEP_UNRESOLVED'
               AND (decision_row.reviewed_candidate_evidence_id IS NOT NULL
                    OR decision_row.candidate_recording_id IS NOT NULL) THEN
                RAISE EXCEPTION 'keep-unresolved cannot select a candidate';
            ELSIF decision_row.review_action = 'CREATE_RECORDING'
               AND (decision_row.reviewed_candidate_evidence_id IS NOT NULL
                    OR decision_row.candidate_recording_id IS NULL
                    OR predecessor.decision_state NOT IN (
                        'REVIEW_REQUIRED', 'NO_MATCH', 'DEFERRED_EVIDENCE'
                    )) THEN
                RAISE EXCEPTION 'invalid create-recording review action';
            END IF;
            IF decision_row.review_action IN ('ACCEPT', 'REJECT')
               AND predecessor.decision_state <> 'REVIEW_REQUIRED' THEN
                RAISE EXCEPTION 'accept/reject requires review-required predecessor';
            END IF;
            IF predecessor.decision_state = 'INTEGRITY_CONFLICT'
               AND decision_row.review_action <> 'KEEP_UNRESOLVED' THEN
                RAISE EXCEPTION 'integrity conflict permits only keep-unresolved';
            END IF;
            IF EXISTS (
                (
                    SELECT rank, recording_id, raw_score, confidence, evidence_tier,
                           feature_scores, hard_conflicts, candidate_origins,
                           extractor_versions, evidence_schema_version,
                           evidence_sha256, evidence_document_size_bytes
                    FROM identity.match_candidate_evidence
                    WHERE decision_id = decision_row.decision_id
                    EXCEPT
                    SELECT rank, recording_id, raw_score, confidence, evidence_tier,
                           feature_scores, hard_conflicts, candidate_origins,
                           extractor_versions, evidence_schema_version,
                           evidence_sha256, evidence_document_size_bytes
                    FROM identity.match_candidate_evidence
                    WHERE decision_id = predecessor.decision_id
                )
                UNION ALL
                (
                    SELECT rank, recording_id, raw_score, confidence, evidence_tier,
                           feature_scores, hard_conflicts, candidate_origins,
                           extractor_versions, evidence_schema_version,
                           evidence_sha256, evidence_document_size_bytes
                    FROM identity.match_candidate_evidence
                    WHERE decision_id = predecessor.decision_id
                    EXCEPT
                    SELECT rank, recording_id, raw_score, confidence, evidence_tier,
                           feature_scores, hard_conflicts, candidate_origins,
                           extractor_versions, evidence_schema_version,
                           evidence_sha256, evidence_document_size_bytes
                    FROM identity.match_candidate_evidence
                    WHERE decision_id = decision_row.decision_id
                )
            ) THEN
                RAISE EXCEPTION 'manual review must copy the predecessor candidate snapshot';
            END IF;
        END IF;
    END IF;

    IF decision_row.decision_state = 'AUTO_MATCH' THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended(decision_row.evidence_mode || ':' || decision_row.evidence_tier, 0)
        );
        SELECT * INTO threshold_row FROM identity.threshold_set
        WHERE threshold_set_version = decision_row.threshold_set_version;
        SELECT CASE
                   WHEN activation.action IN ('ACTIVATE', 'ROLLBACK')
                   THEN activation.threshold_set_version
               END INTO active_threshold
        FROM identity.match_policy_activation activation
        WHERE activation.evidence_mode = decision_row.evidence_mode
          AND activation.evidence_tier = decision_row.evidence_tier
        ORDER BY activation.sequence_no DESC
        LIMIT 1;
        IF threshold_row.threshold_set_version IS NULL
           OR active_threshold IS DISTINCT FROM decision_row.threshold_set_version
           OR threshold_row.calibrator_version IS DISTINCT FROM decision_row.calibrator_version
           OR threshold_row.matcher_version IS DISTINCT FROM decision_row.matcher_version
           OR decision_row.confidence < threshold_row.auto_threshold
           OR decision_row.margin IS NULL
           OR decision_row.margin < threshold_row.margin_threshold
           OR (CASE decision_row.evidence_tier
                  WHEN 'T0' THEN 0 WHEN 'T1' THEN 1 WHEN 'T2' THEN 2
                  WHEN 'T3' THEN 3 WHEN 'T4' THEN 4 ELSE -1
              END)
              < (CASE threshold_row.minimum_evidence_tier
                    WHEN 'T0' THEN 0 WHEN 'T1' THEN 1 WHEN 'T2' THEN 2
                    WHEN 'T3' THEN 3 WHEN 'T4' THEN 4 ELSE 5
                END)
           OR jsonb_array_length(decision_row.hard_conflicts) <> 0 THEN
            RAISE EXCEPTION 'applied auto-match policy gate failed';
        END IF;
    END IF;

    IF decision_row.decision_state = 'INTEGRITY_CONFLICT'
       AND jsonb_array_length(decision_row.hard_conflicts) = 0 THEN
        RAISE EXCEPTION 'integrity conflict requires conflict evidence';
    END IF;

    IF decision_row.execution_mode = 'APPLIED'
       AND decision_row.query_type = 'IMPORT_ENTRY'
       AND NOT EXISTS (
            SELECT 1 FROM importing.import_entry ie
            WHERE ie.import_entry_id = decision_row.import_entry_id
              AND ie.current_match_decision_id = decision_row.decision_id
       ) THEN
        RAISE EXCEPTION 'applied import decision must be projected atomically';
    ELSIF decision_row.execution_mode = 'APPLIED'
       AND decision_row.query_type = 'USER_TRACK_REF'
       AND NOT EXISTS (
            SELECT 1 FROM library.user_track_ref utr
            WHERE utr.user_track_ref_id = decision_row.user_track_ref_id
              AND utr.current_match_decision_id = decision_row.decision_id
       ) THEN
        RAISE EXCEPTION 'applied user-track decision must be projected atomically';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_matcher_release_append_only
BEFORE UPDATE OR DELETE ON identity.matcher_release
FOR EACH ROW EXECUTE FUNCTION app_private.reject_identity_history_mutation();

CREATE TRIGGER tr_calibrator_release_append_only
BEFORE UPDATE OR DELETE ON identity.calibrator_release
FOR EACH ROW EXECUTE FUNCTION app_private.reject_identity_history_mutation();

CREATE TRIGGER tr_threshold_set_append_only
BEFORE UPDATE OR DELETE ON identity.threshold_set
FOR EACH ROW EXECUTE FUNCTION app_private.reject_identity_history_mutation();

CREATE TRIGGER tr_match_policy_activation_validate
BEFORE INSERT OR UPDATE OR DELETE ON identity.match_policy_activation
FOR EACH ROW EXECUTE FUNCTION app_private.validate_match_policy_activation();

CREATE CONSTRAINT TRIGGER tr_match_decision_validate
AFTER INSERT OR UPDATE OR DELETE ON identity.match_decision
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_private.validate_match_decision();

CREATE CONSTRAINT TRIGGER tr_match_candidate_evidence_validate
AFTER INSERT OR UPDATE OR DELETE ON identity.match_candidate_evidence
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_private.validate_match_decision();

CREATE CONSTRAINT TRIGGER tr_import_entry_match_projection
AFTER INSERT OR UPDATE OF current_match_decision_id, match_status, selected_recording_id
ON importing.import_entry
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_private.validate_match_decision();

CREATE CONSTRAINT TRIGGER tr_user_track_ref_match_projection
AFTER INSERT OR UPDATE OF current_match_decision_id, resolution_status, recording_id,
    resolution_confidence
ON library.user_track_ref
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_private.validate_match_decision();

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
