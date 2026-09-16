"""Add shared resource policy, fair queue, activation and I/O fences."""

from alembic import op

revision = "0035_resource_admission"
down_revision = "0034_self_device_pairing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE account.resource_quota_policy (
	singleton_id SMALLSERIAL NOT NULL, 
	revision BIGINT DEFAULT 1 NOT NULL, 
	default_devices INTEGER DEFAULT 5 NOT NULL, 
	default_playbacks INTEGER DEFAULT 2 NOT NULL, 
	default_transfers INTEGER DEFAULT 2 NOT NULL, 
	global_playbacks INTEGER, 
	global_transfers INTEGER, 
	playback_ceiling INTEGER, 
	transfer_ceiling INTEGER, 
	budget_evidence TEXT, 
	grant_sequence BIGINT DEFAULT 0 NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT resource_quota_policy_pkey PRIMARY KEY (singleton_id), 
	CONSTRAINT quota_policy_singleton_check CHECK (singleton_id=1), 
	CONSTRAINT quota_policy_versions_check CHECK (revision>=1 AND grant_sequence>=0), 
	CONSTRAINT quota_policy_defaults_check CHECK (default_devices BETWEEN 1 AND 1000000 AND default_playbacks BETWEEN 1 AND 1000000 AND default_transfers BETWEEN 1 AND 1000000), 
	CONSTRAINT quota_policy_measured_budget_check CHECK ((global_playbacks IS NULL AND global_transfers IS NULL AND playback_ceiling IS NULL AND transfer_ceiling IS NULL AND budget_evidence IS NULL) OR (global_playbacks IS NOT NULL AND global_transfers IS NOT NULL AND playback_ceiling IS NOT NULL AND transfer_ceiling IS NOT NULL AND budget_evidence IS NOT NULL AND length(budget_evidence) BETWEEN 1 AND 240 AND global_playbacks BETWEEN 1 AND playback_ceiling AND global_transfers BETWEEN 1 AND transfer_ceiling AND playback_ceiling BETWEEN 1 AND 1000000 AND transfer_ceiling BETWEEN 1 AND 1000000))
);


CREATE TABLE account.account_quota_override (
	user_id UUID NOT NULL, 
	revision BIGINT NOT NULL, 
	devices INTEGER, 
	playbacks INTEGER, 
	transfers INTEGER, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT account_quota_override_pkey PRIMARY KEY (user_id), 
	CONSTRAINT quota_override_revision_check CHECK (revision>=1), 
	CONSTRAINT quota_override_limits_check CHECK ((devices IS NULL OR devices BETWEEN 1 AND 1000000) AND (playbacks IS NULL OR playbacks BETWEEN 1 AND 1000000) AND (transfers IS NULL OR transfers BETWEEN 1 AND 1000000)), 
	CONSTRAINT account_quota_override_user_id_fkey FOREIGN KEY(user_id) REFERENCES account.user_account (user_id)
);


CREATE TABLE account.resource_grant_cursor (
	user_id UUID NOT NULL, 
	kind TEXT NOT NULL, 
	last_grant BIGINT NOT NULL, 
	CONSTRAINT resource_grant_cursor_pkey PRIMARY KEY (user_id, kind), 
	CONSTRAINT quota_grant_kind_check CHECK (kind IN ('PLAYBACK','TRANSFER')), 
	CONSTRAINT quota_grant_sequence_check CHECK (last_grant>=0), 
	CONSTRAINT resource_grant_cursor_user_id_fkey FOREIGN KEY(user_id) REFERENCES account.user_account (user_id)
);


CREATE TABLE account.resource_admission (
	operation_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	authority_generation BIGINT NOT NULL, 
	authority_kind TEXT NOT NULL, 
	device_id UUID, 
	session_family_id UUID, 
	session_mode TEXT, 
	job_id UUID, 
	job_worker_id TEXT, 
	job_attempt INTEGER, 
	acquisition_attempt_id UUID, 
	source_authorization_id UUID, 
	source_authorization_revision BIGINT, 
	policy_id UUID, 
	policy_revision BIGINT, 
	kind TEXT NOT NULL, 
	resource_type TEXT NOT NULL, 
	resource_id UUID NOT NULL, 
	target_id UUID, 
	request_sha256 BYTEA NOT NULL, 
	state TEXT NOT NULL, 
	activation_id UUID, 
	generation BIGINT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	waiting_until TIMESTAMP WITH TIME ZONE, 
	enqueued_at TIMESTAMP WITH TIME ZONE NOT NULL,
	lease_until TIMESTAMP WITH TIME ZONE, 
	claim_until TIMESTAMP WITH TIME ZONE, 
	claimed_at TIMESTAMP WITH TIME ZONE, 
	terminal_at TIMESTAMP WITH TIME ZONE, 
	attachment_revision BIGINT NOT NULL, 
	current_recording_id UUID, 
	next_recording_id UUID, 
	CONSTRAINT resource_admission_pkey PRIMARY KEY (operation_id), 
	CONSTRAINT admission_device_owner_fkey FOREIGN KEY(user_id, device_id) REFERENCES account.device (user_id, device_id), 
	CONSTRAINT admission_source_revision_fkey FOREIGN KEY(source_authorization_id, source_authorization_revision) REFERENCES discovery.source_authorization (authorization_id, revision), 
	CONSTRAINT admission_policy_revision_fkey FOREIGN KEY(policy_id, policy_revision) REFERENCES discovery.artist_policy_revision (policy_id, revision), 
	CONSTRAINT admission_activation_key UNIQUE (operation_id, activation_id, generation), 
	CONSTRAINT admission_versions_check CHECK (authority_generation>=1 AND generation>=0 AND attachment_revision>=0), 
	CONSTRAINT admission_request_hash_check CHECK (octet_length(request_sha256)=32), 
	CONSTRAINT admission_source_shape_check CHECK ((source_authorization_id IS NULL AND source_authorization_revision IS NULL) OR (source_authorization_id IS NOT NULL AND source_authorization_revision IS NOT NULL AND source_authorization_revision>=1)), 
	CONSTRAINT admission_policy_shape_check CHECK ((policy_id IS NULL AND policy_revision IS NULL) OR (policy_id IS NOT NULL AND policy_revision IS NOT NULL AND policy_revision>=1)), 
	CONSTRAINT admission_state_check CHECK (state IN ('WAITING','ACTIVE','RELEASED','EXPIRED')), 
	CONSTRAINT admission_deadlines_check CHECK (updated_at>=enqueued_at AND enqueued_at>=created_at AND (lease_until IS NULL OR lease_until>created_at) AND (waiting_until IS NULL OR waiting_until>created_at)), 
	CONSTRAINT admission_authority_shape_check CHECK ((authority_kind='DEVICE_SESSION' AND device_id IS NOT NULL AND session_family_id IS NOT NULL AND session_mode IS NOT NULL AND session_mode IN ('V2','LEGACY') AND acquisition_attempt_id IS NULL) OR (authority_kind='SERVER_ACQUISITION' AND kind='TRANSFER' AND device_id IS NULL AND session_family_id IS NULL AND session_mode IS NULL AND acquisition_attempt_id IS NOT NULL AND job_id IS NOT NULL AND source_authorization_id IS NOT NULL AND source_authorization_revision IS NOT NULL)), 
	CONSTRAINT admission_job_fence_check CHECK ((job_id IS NULL AND job_worker_id IS NULL AND job_attempt IS NULL) OR (job_id IS NOT NULL AND job_worker_id IS NOT NULL AND job_attempt IS NOT NULL AND length(job_worker_id) BETWEEN 1 AND 120 AND job_attempt>=1)), 
	CONSTRAINT admission_resource_shape_check CHECK ((kind='PLAYBACK' AND resource_type='PLAY_INSTANCE' AND target_id IS NULL) OR (kind='TRANSFER' AND target_id IS NOT NULL AND resource_type IN ('DOWNLOAD_INTENT','UPLOAD_INTENT','INTERNET_ACQUISITION','DISCOVERY_ACQUISITION') AND current_recording_id IS NULL AND next_recording_id IS NULL)), 
	CONSTRAINT admission_worker_target_check CHECK ((resource_type<>'DISCOVERY_ACQUISITION' OR (authority_kind='SERVER_ACQUISITION' AND target_id=acquisition_attempt_id)) AND (authority_kind<>'SERVER_ACQUISITION' OR resource_type='DISCOVERY_ACQUISITION') AND (resource_type<>'INTERNET_ACQUISITION' OR (authority_kind='DEVICE_SESSION' AND job_id IS NOT NULL))), 
	CONSTRAINT admission_activation_shape_check CHECK ((activation_id IS NULL AND generation=0) OR (activation_id IS NOT NULL AND generation>=1)), 
	CONSTRAINT admission_active_shape_check CHECK (state<>'ACTIVE' OR (activation_id IS NOT NULL AND lease_until IS NOT NULL AND claim_until IS NOT NULL)), 
	CONSTRAINT admission_waiting_shape_check CHECK (state<>'WAITING' OR waiting_until IS NOT NULL), 
	CONSTRAINT admission_terminal_shape_check CHECK (state NOT IN ('RELEASED','EXPIRED') OR terminal_at IS NOT NULL), 
	CONSTRAINT resource_admission_user_id_fkey FOREIGN KEY(user_id) REFERENCES account.user_account (user_id), 
	CONSTRAINT resource_admission_job_id_fkey FOREIGN KEY(job_id) REFERENCES jobs.job (job_id), 
	CONSTRAINT resource_admission_acquisition_attempt_id_fkey FOREIGN KEY(acquisition_attempt_id) REFERENCES discovery.acquisition_attempt (acquisition_attempt_id), 
	CONSTRAINT resource_admission_current_recording_id_fkey FOREIGN KEY(current_recording_id) REFERENCES catalog.recording (recording_id), 
	CONSTRAINT resource_admission_next_recording_id_fkey FOREIGN KEY(next_recording_id) REFERENCES catalog.recording (recording_id)
);
CREATE INDEX ix_admission_account_state ON account.resource_admission (user_id, kind, state);
CREATE INDEX ix_admission_lease_expiry ON account.resource_admission (lease_until) WHERE state='ACTIVE';
CREATE INDEX ix_admission_waiting ON account.resource_admission (kind, enqueued_at) WHERE state='WAITING';

CREATE TABLE account.resource_io_permit (
	permit_id UUID NOT NULL, 
	operation_id UUID NOT NULL, 
	activation_id UUID NOT NULL, 
	generation BIGINT NOT NULL, 
	target_id UUID NOT NULL, 
	opened_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	renewed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT resource_io_permit_pkey PRIMARY KEY (permit_id), 
	CONSTRAINT io_permit_activation_fkey FOREIGN KEY(operation_id, activation_id, generation) REFERENCES account.resource_admission (operation_id, activation_id, generation), 
	CONSTRAINT io_permit_generation_check CHECK (generation>=1), 
	CONSTRAINT io_permit_deadline_check CHECK (renewed_at>=opened_at AND expires_at>renewed_at AND expires_at<=renewed_at+interval '5 seconds')
);
CREATE INDEX ix_io_permit_expiry ON account.resource_io_permit (expires_at);
CREATE INDEX ix_io_permit_operation ON account.resource_io_permit (operation_id);

CREATE TABLE account.quota_operation_receipt (
	operation_id UUID NOT NULL, 
	actor_user_id UUID NOT NULL, 
	web_session_id UUID, 
	web_generation BIGINT, 
	action TEXT NOT NULL, 
	target_user_id UUID, 
	request_sha256 BYTEA NOT NULL, 
	result JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT quota_operation_receipt_pkey PRIMARY KEY (operation_id), 
	CONSTRAINT quota_receipt_action_check CHECK (action IN ('DEFAULTS','OVERRIDE','BUDGET')), 
	CONSTRAINT quota_receipt_hash_check CHECK (octet_length(request_sha256)=32), 
	CONSTRAINT quota_receipt_target_check CHECK ((action='OVERRIDE' AND target_user_id IS NOT NULL) OR (action IN ('DEFAULTS','BUDGET') AND target_user_id IS NULL)), 
	CONSTRAINT quota_receipt_web_check CHECK ((web_session_id IS NULL AND web_generation IS NULL) OR (web_session_id IS NOT NULL AND web_generation IS NOT NULL AND web_generation>=0)), 
	CONSTRAINT quota_receipt_result_check CHECK (jsonb_typeof(result)='object' AND octet_length(result::text)<=16384), 
	CONSTRAINT quota_operation_receipt_actor_user_id_fkey FOREIGN KEY(actor_user_id) REFERENCES account.user_account (user_id), 
	CONSTRAINT quota_operation_receipt_target_user_id_fkey FOREIGN KEY(target_user_id) REFERENCES account.user_account (user_id)
);
CREATE INDEX ix_quota_receipt_created ON account.quota_operation_receipt (created_at);
INSERT INTO account.resource_quota_policy (singleton_id) VALUES (1);
    """)


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
IF EXISTS(SELECT 1 FROM account.account_quota_override) OR EXISTS(SELECT 1 FROM account.resource_grant_cursor) OR EXISTS(SELECT 1 FROM account.resource_admission) OR EXISTS(SELECT 1 FROM account.resource_io_permit) OR EXISTS(SELECT 1 FROM account.quota_operation_receipt) OR EXISTS(SELECT 1 FROM account.resource_quota_policy WHERE revision<>1 OR default_devices<>5 OR default_playbacks<>2 OR default_transfers<>2 OR global_playbacks IS NOT NULL OR grant_sequence<>0) THEN
RAISE EXCEPTION 'Refusing to discard resource quota or admission evidence';
END IF;
END $$;
DROP TABLE account.quota_operation_receipt;
DROP TABLE account.resource_io_permit;
DROP TABLE account.resource_admission;
DROP TABLE account.resource_grant_cursor;
DROP TABLE account.account_quota_override;
DROP TABLE account.resource_quota_policy;    """)
