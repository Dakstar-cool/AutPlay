"""Create ML/recommendation metadata and listening history.

Revision ID: 0008_ml_history
Revises: 0007_importing_identity_history
"""

from __future__ import annotations

from collections.abc import Sequence

from migration_support import drop_tables, execute_reference

revision: str = "0008_ml_history"
down_revision: str | None = "0007_importing_identity_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "embedding_model",
    "recording_embedding",
    "recommendation_request",
    "recommendation_item",
    "taste_cluster",
    "taste_cluster_member",
    "offline_recommendation_pack",
    "listening_event",
)
QUALIFIED_TABLES = (
    "ml.embedding_model",
    "ml.recording_embedding",
    "ml.recommendation_request",
    "ml.recommendation_item",
    "ml.taste_cluster",
    "ml.taste_cluster_member",
    "ml.offline_recommendation_pack",
    "library.listening_event",
)


def upgrade() -> None:
    execute_reference("table", TABLES)


def downgrade() -> None:
    drop_tables(tuple(reversed(QUALIFIED_TABLES)))
