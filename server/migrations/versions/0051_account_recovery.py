"""One-use account recovery code and immutable exact-operation receipts."""

from alembic import op

revision = "0051_account_recovery"
down_revision = "0050_metadata_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE account.account_recovery_credential (
user_id UUID NOT NULL,
server_instance_id UUID NOT NULL,
identity_epoch BIGINT NOT NULL,
identity_thumbprint_sha256 BYTEA NOT NULL,
generation BIGINT NOT NULL,
verifier_sha256 BYTEA NOT NULL,
created_at TIMESTAMP WITH TIME ZONE NOT NULL,
updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
CONSTRAINT account_recovery_credential_pkey PRIMARY KEY (user_id),
CONSTRAINT recovery_credential_versions_check CHECK (generation>=1 AND identity_epoch>=1 AND
updated_at>=created_at),
CONSTRAINT recovery_credential_hashes_check CHECK (octet_length(verifier_sha256)=32 AND
octet_length(identity_thumbprint_sha256)=32),
CONSTRAINT account_recovery_credential_user_id_fkey FOREIGN KEY(user_id) REFERENCES
account.user_account (user_id),
CONSTRAINT account_recovery_credential_server_instance_id_fkey FOREIGN
KEY(server_instance_id) REFERENCES account.server_instance (server_instance_id)
);

CREATE TABLE account.account_recovery_operation (
operation_id UUID NOT NULL,
kind TEXT NOT NULL,
user_id UUID NOT NULL,
server_instance_id UUID NOT NULL,
identity_epoch BIGINT NOT NULL,
request_sha256 BYTEA NOT NULL,
actor_device_id UUID,
actor_family_id UUID,
previous_generation BIGINT NOT NULL,
result_generation BIGINT NOT NULL,
authority_generation BIGINT NOT NULL,
spent_verifier_sha256 BYTEA,
next_verifier_sha256 BYTEA NOT NULL,
result_device_id UUID,
result_session_id UUID,
binding_commit_id UUID,
created_at TIMESTAMP WITH TIME ZONE NOT NULL,
expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
CONSTRAINT account_recovery_operation_pkey PRIMARY KEY (operation_id),
CONSTRAINT recovery_operation_actor_fkey FOREIGN KEY(user_id, actor_device_id) REFERENCES
account.device (user_id, device_id),
CONSTRAINT recovery_operation_result_fkey FOREIGN KEY(user_id, result_device_id,
result_session_id) REFERENCES account.user_session (user_id, device_id, session_id),
CONSTRAINT recovery_operation_versions_check CHECK (previous_generation>=0 AND
result_generation=previous_generation+1 AND authority_generation>=1 AND identity_epoch>=1),
CONSTRAINT recovery_operation_hashes_check CHECK (octet_length(request_sha256)=32 AND
octet_length(next_verifier_sha256)=32 AND (spent_verifier_sha256 IS NULL OR
octet_length(spent_verifier_sha256)=32)),
CONSTRAINT recovery_operation_expiry_check CHECK (expires_at>created_at AND
expires_at<=created_at+interval '1 day'),
CONSTRAINT recovery_operation_shape_check CHECK ((kind='CONFIGURE' AND actor_device_id IS
NOT NULL AND actor_family_id IS NOT NULL AND spent_verifier_sha256 IS NULL AND
result_device_id IS NULL AND result_session_id IS NULL AND binding_commit_id IS NULL) OR
(kind='RECOVER' AND actor_device_id IS NULL AND actor_family_id IS NULL AND
spent_verifier_sha256 IS NOT NULL AND result_device_id IS NOT NULL AND result_session_id IS
NOT NULL AND binding_commit_id IS NOT NULL AND previous_generation>=1)),
CONSTRAINT account_recovery_operation_user_id_fkey FOREIGN KEY(user_id) REFERENCES
account.user_account (user_id),
CONSTRAINT account_recovery_operation_server_instance_id_fkey FOREIGN
KEY(server_instance_id) REFERENCES account.server_instance (server_instance_id),
CONSTRAINT account_recovery_operation_binding_commit_id_key UNIQUE (binding_commit_id)
);

CREATE INDEX ix_recovery_operation_expiry ON account.account_recovery_operation
(expires_at);

CREATE INDEX ix_recovery_operation_user ON account.account_recovery_operation (user_id,
created_at);
    CREATE FUNCTION account.guard_recovery_credential() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'recovery credential history cannot be removed';
      ELSIF TG_OP='INSERT' THEN
        IF NEW.generation<>1 THEN
          RAISE EXCEPTION 'recovery credential must start at generation one';
        END IF;
      ELSIF NEW.user_id<>OLD.user_id OR NEW.server_instance_id<>OLD.server_instance_id
        OR NEW.created_at<>OLD.created_at OR NEW.generation<>OLD.generation+1
        OR NEW.verifier_sha256=OLD.verifier_sha256 OR NEW.updated_at<OLD.updated_at THEN
        RAISE EXCEPTION 'recovery credential rotation is not monotonic';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_recovery_credential BEFORE INSERT OR UPDATE OR DELETE
      ON account.account_recovery_credential FOR EACH ROW
      EXECUTE FUNCTION account.guard_recovery_credential();

    CREATE FUNCTION account.guard_recovery_operation() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' AND OLD.expires_at<clock_timestamp() THEN RETURN OLD; END IF;
      RAISE EXCEPTION 'recovery operation receipt is immutable until expiry';
    END $$;
    CREATE TRIGGER guard_recovery_operation BEFORE UPDATE OR DELETE
      ON account.account_recovery_operation FOR EACH ROW
      EXECUTE FUNCTION account.guard_recovery_operation();
    REVOKE ALL ON account.account_recovery_credential,
      account.account_recovery_operation FROM PUBLIC;
    REVOKE ALL ON FUNCTION account.guard_recovery_credential(),
      account.guard_recovery_operation() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
    LOCK TABLE account.account_recovery_credential,
      account.account_recovery_operation IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM account.account_recovery_credential)
        OR EXISTS(SELECT 1 FROM account.account_recovery_operation) THEN
        RAISE EXCEPTION 'Refusing to discard account recovery evidence';
      END IF;
    END $$;
    DROP TABLE account.account_recovery_operation;
    DROP TABLE account.account_recovery_credential;
    DROP FUNCTION account.guard_recovery_operation();
    DROP FUNCTION account.guard_recovery_credential();
    """)
