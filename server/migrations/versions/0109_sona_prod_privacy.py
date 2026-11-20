"""Verify that both branches retain their complete owner erasure functions."""

from hashlib import sha256

from alembic import op
from sqlalchemy import text

revision = "0109_sona_prod_privacy"
down_revision = "0108_m11_native_owner"
branch_labels = None
depends_on = None

# Independent clean replays of production 0075 and R15 supplied these bodies.
_EXPECTED = (
    (
        "account.purge_account(uuid,uuid)",
        "991245c0724df60a97d24639efa9572f3abe50233298ea7b870561afd688ecfe",
    ),
    (
        "account.verify_account_purge_ready(uuid)",
        "e83ef2eee46072e9242cf65d51674b46321502e59d7bb6f0b1e8512f3cf6f7f0",
    ),
)


def upgrade() -> None:
    connection = op.get_bind()
    for signature, expected in _EXPECTED:
        body = connection.scalar(
            text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
            {"signature": signature},
        )
        if not isinstance(body, str) or sha256(body.encode()).hexdigest() != expected:
            raise RuntimeError("integrated production privacy function mismatch")


def downgrade() -> None:
    # The preceding M11 upgrade and rollback both carry the production erasure
    # clauses. This verification-only revision owns no DDL to reverse.
    pass
