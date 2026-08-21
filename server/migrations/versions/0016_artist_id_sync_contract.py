"""Add safe catalog Artist ID sync prerequisites.

Revision ID: 0016_artist_id_sync_contract
Revises: 0015_wave_runtime
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_artist_id_sync_contract"
down_revision: str | None = "0015_wave_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add opt-in snapshot capabilities and catalog-closure support indexes."""
    op.execute(
        "ALTER TABLE sync.bootstrap_session ADD COLUMN capabilities text[] "
        "NOT NULL DEFAULT ARRAY[]::text[]"
    )
    op.execute(
        "CREATE INDEX ix_user_track_ref_recording_user_active "
        "ON library.user_track_ref (recording_id, user_id) "
        "WHERE recording_id IS NOT NULL AND deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_release_artist_credit_active "
        "ON catalog.release (artist_credit_id, release_id) WHERE deleted_at IS NULL"
    )
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM catalog.artist_credit_name
                WHERE length(join_phrase) > 1000
            ) THEN
                RAISE EXCEPTION 'existing artist credit join phrase limit exceeded'
                    USING ERRCODE = 'check_violation';
            END IF;
        END;
        $$
    """)
    op.execute("ALTER TABLE catalog.artist_credit_name DROP CONSTRAINT ck_artist_credit_name_role")
    op.execute(
        "ALTER TABLE catalog.artist_credit_name "
        "ADD CONSTRAINT ck_artist_credit_name_role "
        "CHECK (length(role) BETWEEN 1 AND 100)"
    )
    op.execute(
        "ALTER TABLE catalog.artist_credit_name "
        "ADD CONSTRAINT artist_credit_name_join_phrase_check "
        "CHECK (length(join_phrase) <= 1000)"
    )
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM catalog.artist_credit_name
                GROUP BY artist_credit_id HAVING count(*) > 1000
            ) THEN
                RAISE EXCEPTION 'existing artist credit member limit exceeded'
                    USING ERRCODE = 'check_violation';
            END IF;
        END;
        $$
    """)
    op.execute("""
        CREATE FUNCTION app_private.enforce_artist_credit_name_limit()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE member_count integer;
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.artist_credit_id = NEW.artist_credit_id THEN
                RETURN NEW;
            END IF;
            PERFORM 1 FROM catalog.artist_credit
            WHERE artist_credit_id = NEW.artist_credit_id FOR UPDATE;
            SELECT count(*) INTO member_count FROM catalog.artist_credit_name
            WHERE artist_credit_id = NEW.artist_credit_id;
            IF member_count >= 1000 THEN
                RAISE EXCEPTION 'artist credit member limit exceeded'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION app_private.enforce_artist_credit_name_limit() FROM PUBLIC")
    op.execute("""
        CREATE TRIGGER tr_artist_credit_name_limit
        BEFORE INSERT OR UPDATE OF artist_credit_id ON catalog.artist_credit_name
        FOR EACH ROW EXECUTE FUNCTION app_private.enforce_artist_credit_name_limit()
    """)
    op.execute("""
        CREATE FUNCTION app_private.bump_artist_credit_for_member_change()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                UPDATE catalog.artist_credit SET updated_at = now()
                WHERE artist_credit_id = NEW.artist_credit_id;
            ELSIF TG_OP = 'DELETE' THEN
                UPDATE catalog.artist_credit SET updated_at = now()
                WHERE artist_credit_id = OLD.artist_credit_id;
            ELSE
                UPDATE catalog.artist_credit SET updated_at = now()
                WHERE artist_credit_id IN (OLD.artist_credit_id, NEW.artist_credit_id);
            END IF;
            RETURN NULL;
        END;
        $$
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION app_private.bump_artist_credit_for_member_change() FROM PUBLIC"
    )
    op.execute("""
        CREATE TRIGGER tr_artist_credit_name_parent_version
        AFTER INSERT OR UPDATE OR DELETE ON catalog.artist_credit_name
        FOR EACH ROW EXECUTE FUNCTION app_private.bump_artist_credit_for_member_change()
    """)


def downgrade() -> None:
    """Remove only empty/new contract objects; never merge or backfill catalog names."""
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM sync.sync_event WHERE aggregate_type IN "
        "('ARTIST', 'ARTIST_CREDIT', 'RECORDING_ARTIST_CREDIT', "
        "'RELEASE_ARTIST_CREDIT')) THEN "
        "RAISE EXCEPTION 'refusing Artist sync downgrade with catalog events'; "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM sync.bootstrap_session "
        "WHERE cardinality(capabilities) > 0) THEN "
        "RAISE EXCEPTION 'refusing Artist sync downgrade with capable bootstrap sessions'; "
        "END IF; END $$"
    )
    op.execute("DROP TRIGGER tr_artist_credit_name_parent_version ON catalog.artist_credit_name")
    op.execute("DROP TRIGGER tr_artist_credit_name_limit ON catalog.artist_credit_name")
    op.execute("DROP FUNCTION app_private.bump_artist_credit_for_member_change()")
    op.execute("DROP FUNCTION app_private.enforce_artist_credit_name_limit()")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM catalog.artist_credit_name "
        "WHERE role NOT IN ('PRIMARY', 'FEATURED', 'REMIXER', 'CONDUCTOR', 'OTHER')) THEN "
        "RAISE EXCEPTION 'refusing Artist sync downgrade with additive credit roles'; "
        "END IF; END $$"
    )
    op.execute(
        "ALTER TABLE catalog.artist_credit_name "
        "DROP CONSTRAINT artist_credit_name_join_phrase_check"
    )
    op.execute("ALTER TABLE catalog.artist_credit_name DROP CONSTRAINT ck_artist_credit_name_role")
    op.execute(
        "ALTER TABLE catalog.artist_credit_name "
        "ADD CONSTRAINT ck_artist_credit_name_role "
        "CHECK (role IN ('PRIMARY', 'FEATURED', 'REMIXER', 'CONDUCTOR', 'OTHER'))"
    )
    op.execute("DROP INDEX catalog.ix_release_artist_credit_active")
    op.execute("DROP INDEX library.ix_user_track_ref_recording_user_active")
    op.execute("ALTER TABLE sync.bootstrap_session DROP COLUMN capabilities")
