"""Create immutable Vault metadata and audio-variant relations.

Revision ID: 0006_vault
Revises: 0005_library_playlists
"""

from __future__ import annotations

from collections.abc import Sequence

from migration_support import drop_tables, execute_reference

revision: str = "0006_vault"
down_revision: str | None = "0005_library_playlists"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "vault_object",
    "vault_replica",
    "audio_variant",
    "audio_fingerprint",
    "recording_canonical_variant",
    "acquisition_record",
)
QUALIFIED_TABLES = (
    "vault.vault_object",
    "vault.vault_replica",
    "vault.audio_variant",
    "vault.audio_fingerprint",
    "vault.recording_canonical_variant",
    "vault.acquisition_record",
)


def upgrade() -> None:
    execute_reference("table", TABLES)


def downgrade() -> None:
    drop_tables(tuple(reversed(QUALIFIED_TABLES)))
