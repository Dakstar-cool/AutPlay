"""Add owner-scoped immutable Internet search and selection receipts."""

from alembic import op

revision = "0031_music_library"
down_revision = "0030_temporal_snapshot_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE discovery.internet_search (
        search_id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES account.user_account(user_id),
        query text NOT NULL CHECK(length(query) BETWEEN 1 AND 200),
        candidates jsonb NOT NULL CHECK(jsonb_typeof(candidates)='array' AND jsonb_array_length(candidates)<=5),
        snapshot_sha256 bytea NOT NULL CHECK(octet_length(snapshot_sha256)=32),
        created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
        UNIQUE(user_id, search_id)
    );
    CREATE INDEX internet_search_owner_time ON discovery.internet_search(user_id,created_at DESC);
    CREATE TABLE discovery.internet_acquisition (
        acquisition_id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES account.user_account(user_id),
        device_id uuid NOT NULL REFERENCES account.device(device_id), search_id uuid NOT NULL,
        candidate_id text NOT NULL, selected_snapshot jsonb NOT NULL,
        job_id uuid NOT NULL REFERENCES jobs.job(job_id),
        state text NOT NULL DEFAULT 'QUEUED' CHECK(state IN ('QUEUED','DOWNLOADING','UPLOADING','PROCESSING','READY','FAILED')),
        user_track_ref_id uuid REFERENCES library.user_track_ref(user_track_ref_id),
        upload_id uuid REFERENCES vault.upload_session(upload_session_id),
        audio_variant_id uuid REFERENCES vault.audio_variant(audio_variant_id),
        error_code text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(user_id,search_id) REFERENCES discovery.internet_search(user_id,search_id),
        UNIQUE(user_id,search_id,candidate_id)
    );
    CREATE FUNCTION app_private.protect_music_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME='internet_search' OR
         ROW(NEW.acquisition_id,NEW.user_id,NEW.device_id,NEW.search_id,NEW.candidate_id,NEW.selected_snapshot,NEW.job_id)
         IS DISTINCT FROM ROW(OLD.acquisition_id,OLD.user_id,OLD.device_id,OLD.search_id,OLD.candidate_id,OLD.selected_snapshot,OLD.job_id)
      THEN RAISE EXCEPTION 'music selection snapshot is immutable'; END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER internet_search_immutable BEFORE UPDATE ON discovery.internet_search
      FOR EACH ROW EXECUTE FUNCTION app_private.protect_music_snapshot();
    CREATE TRIGGER internet_selection_immutable BEFORE UPDATE ON discovery.internet_acquisition
      FOR EACH ROW EXECUTE FUNCTION app_private.protect_music_snapshot();
    REVOKE ALL ON FUNCTION app_private.protect_music_snapshot() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM discovery.internet_search)
        OR EXISTS(SELECT 1 FROM discovery.internet_acquisition) THEN
        RAISE EXCEPTION 'Music selection receipts require explicit retention review before removal';
      END IF;
    END $$;
    DROP TABLE discovery.internet_acquisition;
    DROP TABLE discovery.internet_search;
    DROP FUNCTION app_private.protect_music_snapshot();
    """)
