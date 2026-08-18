# ruff: noqa: E501
"""Add durable P13 Wave room, membership, commands and preflight state.

Revision ID: 0015_wave_runtime
Revises: 0014_gpu_enrichment
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015_wave_runtime"
down_revision: str | None = "0014_gpu_enrichment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS wave")
    op.execute("""CREATE TABLE wave.room (
      room_id uuid PRIMARY KEY DEFAULT uuidv7(), room_code_sha256 bytea NOT NULL UNIQUE,
      host_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
      host_device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
      state text NOT NULL DEFAULT 'OPEN', playback_state text NOT NULL DEFAULT 'IDLE', room_epoch bigint NOT NULL DEFAULT 1,
      queue_version bigint NOT NULL DEFAULT 1, timeline_position_ms bigint NOT NULL DEFAULT 0,
      timeline_recording_id uuid REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
      command_sequence bigint NOT NULL DEFAULT 0, timeline_effective_at timestamptz, expires_at timestamptz NOT NULL,
      host_lost_at timestamptz, closed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT ck_wave_room_code_hash CHECK (octet_length(room_code_sha256) = 32),
      CONSTRAINT ck_wave_room_state CHECK (state IN ('OPEN','ORPHANED','CLOSED','EXPIRED')),
      CONSTRAINT ck_wave_room_playback_state CHECK (playback_state IN ('IDLE','PREPARING','PLAYING','PAUSED')),
      CONSTRAINT ck_wave_room_versions CHECK (room_epoch >= 1 AND queue_version >= 1 AND command_sequence >= 0 AND timeline_position_ms >= 0),
      CONSTRAINT ck_wave_room_expiry CHECK (expires_at > created_at)
    )""")
    op.execute("""CREATE TABLE wave.member (
      room_id uuid NOT NULL REFERENCES wave.room(room_id) ON DELETE RESTRICT,
      user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
      device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
      role text NOT NULL DEFAULT 'MEMBER', status text NOT NULL DEFAULT 'ALLOWED',
      joined_at timestamptz NOT NULL DEFAULT now(), left_at timestamptz, last_present_at timestamptz,
      PRIMARY KEY (room_id,device_id), CONSTRAINT uq_wave_member_user_device UNIQUE(room_id,user_id,device_id),
      CONSTRAINT ck_wave_member_role CHECK(role IN ('HOST','MEMBER')),
      CONSTRAINT ck_wave_member_status CHECK(status IN ('ALLOWED','JOINED','LEFT','REVOKED')),
      CONSTRAINT ck_wave_member_presence CHECK (last_present_at IS NULL OR last_present_at >= joined_at)
    )""")
    op.execute("""CREATE TABLE wave.invitation (
      room_id uuid NOT NULL REFERENCES wave.room(room_id) ON DELETE RESTRICT,
      user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(room_id,user_id)
    )""")
    op.execute("""CREATE TABLE wave.queue_entry (
      queue_entry_id uuid PRIMARY KEY DEFAULT uuidv7(), room_id uuid NOT NULL REFERENCES wave.room(room_id) ON DELETE RESTRICT,
      recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT,
      position integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), removed_at timestamptz,
      CONSTRAINT uq_wave_queue_entry_room UNIQUE(queue_entry_id,room_id), CONSTRAINT ck_wave_queue_position CHECK(position >= 0 AND position < 100)
    )""")
    op.execute("""CREATE TABLE wave.command (
      room_id uuid NOT NULL REFERENCES wave.room(room_id) ON DELETE RESTRICT, command_sequence bigint NOT NULL,
      actor_user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
      actor_device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
      idempotency_key text NOT NULL, request_sha256 bytea NOT NULL, expected_queue_version bigint NOT NULL, expected_sequence bigint NOT NULL, command_kind text NOT NULL,
      command_document jsonb NOT NULL, effective_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(room_id,command_sequence), CONSTRAINT uq_wave_command_idempotency UNIQUE(room_id,idempotency_key),
      CONSTRAINT ck_wave_command_key CHECK(length(idempotency_key) BETWEEN 1 AND 128),
      CONSTRAINT ck_wave_command_hash CHECK(octet_length(request_sha256) = 32),
      CONSTRAINT ck_wave_command_base CHECK(expected_queue_version >= 1 AND expected_sequence >= 0), CONSTRAINT ck_wave_command_kind CHECK(command_kind IN ('PLAY','PAUSE','SEEK','SKIP','QUEUE','TRANSFER','CLOSE','LEAVE','EXPIRE','START_ABORTED'))
    )""")
    op.execute("""CREATE TABLE wave.preflight (
      room_id uuid NOT NULL REFERENCES wave.room(room_id) ON DELETE RESTRICT, user_id uuid NOT NULL REFERENCES account.user_account(user_id) ON DELETE RESTRICT,
      device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT, queue_entry_id uuid NOT NULL,
      recording_id uuid NOT NULL REFERENCES catalog.recording(recording_id) ON DELETE RESTRICT, queue_version bigint NOT NULL, availability text NOT NULL, final_ready boolean NOT NULL DEFAULT false,
      source_checked_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL, PRIMARY KEY(room_id,device_id,queue_entry_id),
      CONSTRAINT ck_wave_preflight_version CHECK(queue_version >= 1 AND expires_at > source_checked_at),
      CONSTRAINT fk_wave_preflight_queue_entry FOREIGN KEY(queue_entry_id,room_id) REFERENCES wave.queue_entry(queue_entry_id,room_id) ON DELETE RESTRICT,
      CONSTRAINT ck_wave_preflight_availability CHECK(availability IN ('LOCAL','DOWNLOADED','VAULT_STREAMABLE','UNAVAILABLE'))
    )""")
    op.execute("""CREATE TABLE wave.timing_report (
      room_id uuid NOT NULL REFERENCES wave.room(room_id) ON DELETE RESTRICT, device_id uuid NOT NULL REFERENCES account.device(device_id) ON DELETE RESTRICT,
      command_sequence bigint NOT NULL, rtt_ms integer NOT NULL, offset_ms integer NOT NULL, uncertainty_ms integer NOT NULL, start_skew_ms integer, drift_ms integer, reported_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(room_id,device_id,command_sequence), CONSTRAINT ck_wave_timing_bounds CHECK(rtt_ms BETWEEN 0 AND 1000 AND uncertainty_ms BETWEEN 0 AND 100 AND abs(offset_ms) <= 86400000)
    )""")
    for statement in (
        "CREATE INDEX ix_wave_room_expiry ON wave.room(expires_at) WHERE closed_at IS NULL",
        "CREATE INDEX ix_wave_member_presence ON wave.member(room_id,last_present_at)",
        "CREATE INDEX ix_wave_preflight_room_recording ON wave.preflight(room_id,recording_id,expires_at)",
    ):
        op.execute(statement)
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA wave FROM PUBLIC")


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT 1 FROM wave.room) OR EXISTS(SELECT 1 FROM wave.member) OR EXISTS(SELECT 1 FROM wave.invitation) OR EXISTS(SELECT 1 FROM wave.queue_entry) OR EXISTS(SELECT 1 FROM wave.command) OR EXISTS(SELECT 1 FROM wave.preflight) OR EXISTS(SELECT 1 FROM wave.timing_report) THEN RAISE EXCEPTION 'refusing destructive P13 downgrade with Wave data'; END IF; END $$"""
    )
    op.execute("DROP TABLE wave.timing_report")
    op.execute("DROP TABLE wave.preflight")
    op.execute("DROP TABLE wave.invitation")
    op.execute("DROP TABLE wave.command")
    op.execute("DROP TABLE wave.queue_entry")
    op.execute("DROP TABLE wave.member")
    op.execute("DROP TABLE wave.room")
    op.execute("DROP SCHEMA wave")
