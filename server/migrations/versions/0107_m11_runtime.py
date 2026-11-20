"""Install default-off M11 priority and native byte-accounting authority.

0103--0106 remain reserved staged work. This fresh revision follows the actual
installed graph head and never imports mutable staged schema installers.
"""

from pathlib import Path

from alembic import op

revision = "0107_m11_runtime"
down_revision = "0102_native_dataset_export"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute(
        Path(__file__).with_name("0107_m11_runtime_rollback.sql").read_text(encoding="utf-8")
    )
