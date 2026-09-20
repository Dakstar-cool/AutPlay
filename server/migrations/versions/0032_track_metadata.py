"""Add descriptive metadata without changing audio or canonical identities."""

from alembic import op

revision = "0032_track_metadata"
down_revision = "0031_music_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE library.metadata_artwork (
      sha256 text PRIMARY KEY,
      content bytea NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),

      CONSTRAINT metadata_artwork_sha256_check
        CHECK(length(sha256)=64
          AND sha256 ~ '^[a-f0-9]{64}$'),

      CONSTRAINT metadata_artwork_content_check CHECK(octet_length(content) BETWEEN 1 AND 2097152)
    );
    CREATE TABLE library.track_metadata (
      user_track_ref_id uuid PRIMARY KEY REFERENCES library.user_track_ref(user_track_ref_id),
      revision bigint NOT NULL DEFAULT 1, generation bigint NOT NULL DEFAULT 1,
      document jsonb NOT NULL DEFAULT '{}'::jsonb, candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
      state text NOT NULL DEFAULT 'QUEUED', error_code text,
      artwork_sha256 text REFERENCES library.metadata_artwork(sha256),
      job_id uuid REFERENCES jobs.job(job_id), updated_at timestamptz NOT NULL DEFAULT now(),

      CONSTRAINT track_metadata_versions_check CHECK(revision >= 1 AND generation >= 1),

      CONSTRAINT track_metadata_state_check
        CHECK(state IN ('QUEUED','READY','REVIEW','NOT_FOUND','RETRY','FAILED')),

      CONSTRAINT track_metadata_document_check
        CHECK(jsonb_typeof(document)='object'
          AND octet_length(document::text)<=65536),

      CONSTRAINT track_metadata_candidates_check
        CHECK(jsonb_typeof(candidates)='array'
          AND jsonb_array_length(candidates)<=5
          AND octet_length(candidates::text)<=65536)
    );
    CREATE TABLE library.track_metadata_revision (
      user_track_ref_id uuid NOT NULL REFERENCES library.track_metadata(user_track_ref_id),
      revision bigint NOT NULL, snapshot jsonb NOT NULL, operation_id uuid, request_sha256 bytea,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(user_track_ref_id,revision),

      CONSTRAINT track_metadata_revision_operation_key UNIQUE(user_track_ref_id,operation_id),

      CONSTRAINT track_metadata_revision_revision_check CHECK(revision >= 1),

      CONSTRAINT track_metadata_revision_snapshot_check
        CHECK(octet_length(snapshot::text)<=140000
          AND jsonb_typeof(snapshot)='object'),

      CONSTRAINT track_metadata_revision_request_check
        CHECK(request_sha256 IS NULL
          OR octet_length(request_sha256)=32)
    );
    CREATE FUNCTION app_private.protect_metadata_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Metadata evidence is immutable'; END; $$;
    CREATE TRIGGER metadata_revision_immutable BEFORE UPDATE
          OR DELETE
      ON library.track_metadata_revision
      FOR EACH ROW EXECUTE FUNCTION app_private.protect_metadata_evidence();
    CREATE TRIGGER metadata_artwork_immutable BEFORE UPDATE OR DELETE ON library.metadata_artwork
      FOR EACH ROW EXECUTE FUNCTION app_private.protect_metadata_evidence();
    REVOKE ALL ON FUNCTION app_private.protect_metadata_evidence() FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("""
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM library.track_metadata)
          OR EXISTS(SELECT 1 FROM library.metadata_artwork)
        OR EXISTS(SELECT 1 FROM library.track_metadata_revision) THEN
        RAISE EXCEPTION 'Metadata evidence requires retention review before removal';
      END IF;
    END $$;
    DROP TABLE library.track_metadata_revision;
    DROP TABLE library.track_metadata;
    DROP TABLE library.metadata_artwork;
    DROP FUNCTION app_private.protect_metadata_evidence();
    """)
