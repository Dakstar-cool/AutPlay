"""Add account authority generation and self-service device pairing evidence."""

from alembic import op

revision = "0034_self_device_pairing"
down_revision = "0033_web_passkeys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE account.user_account ADD COLUMN authority_generation bigint NOT NULL DEFAULT 1 CONSTRAINT user_account_authority_generation_check CHECK (authority_generation >= 1);

CREATE TABLE account.self_device_pairing (
	ceremony_id UUID NOT NULL,
	server_instance_id UUID NOT NULL,
	user_id UUID NOT NULL,
	authority_generation BIGINT NOT NULL,
	source_device_id UUID NOT NULL,
	source_family_id UUID NOT NULL,
	start_operation_id UUID NOT NULL,
	start_document JSONB NOT NULL,
	state TEXT NOT NULL,
	revision BIGINT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	claim_id UUID,
	claim_document JSONB,
	approval_operation_id UUID,
	last_polled_at TIMESTAMP WITH TIME ZONE,
	exchange_id UUID,
	exchange_document JSONB,
	result_device_id UUID,
	result_session_id UUID,
	receipt_expires_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT self_device_pairing_pkey PRIMARY KEY (ceremony_id),
	CONSTRAINT self_pairing_source_owner_fkey FOREIGN KEY(user_id, source_device_id) REFERENCES account.device (user_id, device_id),
	CONSTRAINT self_pairing_result_owner_fkey FOREIGN KEY(user_id, result_device_id, result_session_id) REFERENCES account.user_session (user_id, device_id, session_id),
	CONSTRAINT self_pairing_versions_check CHECK (authority_generation >= 1 AND revision >= 1),
	CONSTRAINT self_pairing_state_check CHECK (state IN ('OPEN','CLAIMED','APPROVED','EXCHANGED','CANCELLED','REJECTED')),
	CONSTRAINT self_pairing_expiry_check CHECK (expires_at > created_at AND expires_at <= created_at + interval '15 minutes'),
	CONSTRAINT self_pairing_start_document_check CHECK (jsonb_typeof(start_document)='object' AND octet_length(start_document::text)<=8192),
	CONSTRAINT self_pairing_claim_document_check CHECK (claim_document IS NULL OR (jsonb_typeof(claim_document)='object' AND octet_length(claim_document::text)<=8192)),
	CONSTRAINT self_pairing_exchange_document_check CHECK (exchange_document IS NULL OR (jsonb_typeof(exchange_document)='object' AND octet_length(exchange_document::text)<=8192)),
	CONSTRAINT self_pairing_claim_shape_check CHECK ((claim_id IS NULL) = (claim_document IS NULL)),
	CONSTRAINT self_pairing_claim_state_check CHECK (state NOT IN ('CLAIMED','APPROVED','EXCHANGED','REJECTED') OR claim_id IS NOT NULL),
	CONSTRAINT self_pairing_approval_check CHECK (state NOT IN ('APPROVED','EXCHANGED') OR approval_operation_id IS NOT NULL),
	CONSTRAINT self_pairing_exchange_shape_check CHECK ((state='EXCHANGED' AND exchange_id IS NOT NULL AND exchange_document IS NOT NULL AND result_device_id IS NOT NULL AND result_session_id IS NOT NULL AND receipt_expires_at IS NOT NULL) OR (state<>'EXCHANGED' AND exchange_id IS NULL AND exchange_document IS NULL AND result_device_id IS NULL AND result_session_id IS NULL AND receipt_expires_at IS NULL)),
	CONSTRAINT self_device_pairing_server_instance_id_fkey FOREIGN KEY(server_instance_id) REFERENCES account.server_instance (server_instance_id),
	CONSTRAINT self_device_pairing_user_id_fkey FOREIGN KEY(user_id) REFERENCES account.user_account (user_id),
	CONSTRAINT self_device_pairing_start_operation_id_key UNIQUE (start_operation_id),
	CONSTRAINT self_device_pairing_claim_id_key UNIQUE (claim_id),
	CONSTRAINT self_device_pairing_exchange_id_key UNIQUE (exchange_id)
);

CREATE INDEX ix_self_pairing_expiry ON account.self_device_pairing (expires_at);

CREATE INDEX ix_self_pairing_user_expiry ON account.self_device_pairing (user_id, expires_at);

CREATE TABLE account.self_pairing_command (
	operation_id UUID NOT NULL,
	ceremony_id UUID NOT NULL,
	user_id UUID NOT NULL,
	device_id UUID NOT NULL,
	family_id UUID NOT NULL,
	request_hash BYTEA NOT NULL,
	result JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT self_pairing_command_pkey PRIMARY KEY (operation_id),
	CONSTRAINT self_pairing_command_actor_fkey FOREIGN KEY(user_id, device_id) REFERENCES account.device (user_id, device_id),
	CONSTRAINT self_pairing_command_hash_check CHECK (octet_length(request_hash)=32),
	CONSTRAINT self_pairing_command_result_check CHECK (jsonb_typeof(result)='object' AND octet_length(result::text)<=8192),
	CONSTRAINT self_pairing_command_ceremony_id_fkey FOREIGN KEY(ceremony_id) REFERENCES account.self_device_pairing (ceremony_id) ON DELETE CASCADE,
	CONSTRAINT self_pairing_command_user_id_fkey FOREIGN KEY(user_id) REFERENCES account.user_account (user_id)
);

CREATE INDEX ix_self_pairing_command_ceremony ON account.self_pairing_command (ceremony_id);

CREATE TABLE account.self_pairing_rate (
	key_hash BYTEA NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	count BIGINT NOT NULL,
	CONSTRAINT self_pairing_rate_pkey PRIMARY KEY (key_hash),
	CONSTRAINT self_pairing_rate_check CHECK (octet_length(key_hash)=32 AND count >= 1)
);

CREATE INDEX ix_self_pairing_rate_expiry ON account.self_pairing_rate (expires_at);
    """)


def downgrade() -> None:
    op.execute("""
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM account.self_device_pairing)
         OR EXISTS(SELECT 1 FROM account.self_pairing_command)
         OR EXISTS(SELECT 1 FROM account.self_pairing_rate)
         OR EXISTS(SELECT 1 FROM account.user_account WHERE authority_generation<>1) THEN
        RAISE EXCEPTION 'Refusing to discard self-pairing or authority-generation evidence';
      END IF;
    END $$;
    DROP TABLE account.self_pairing_rate;
    DROP TABLE account.self_pairing_command;
    DROP TABLE account.self_device_pairing;
    ALTER TABLE account.user_account DROP COLUMN authority_generation;
    """)
