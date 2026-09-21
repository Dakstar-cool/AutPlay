"""CLI rejects invalid work budgets before opening a database or filesystem."""

import io
from pathlib import Path

import pytest
from autplay.application.vault_reconciliation import ReconcileMode
from autplay.entrypoints.admin import run_vault_reconcile
from autplay.entrypoints.vault_reconciliation import build_vault_reconciliation_service
from sqlalchemy.orm import sessionmaker


@pytest.mark.parametrize("value", [-1, 0, 101, 1000, True])
def test_invalid_budget_cannot_start_work(tmp_path: Path, value: int) -> None:
    root = tmp_path / "absent"
    service = build_vault_reconciliation_service(sessionmaker(), root)
    with pytest.raises(ValueError, match="vault_reconcile_request_invalid"):
        service.run(mode=ReconcileMode.APPLY, limit=value)
    assert not root.exists()


def test_drain_only_requires_explicit_apply(tmp_path: Path) -> None:
    root = tmp_path / "absent"
    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run_vault_reconcile(
            build_vault_reconciliation_service(sessionmaker(), root),
            ["vault-reconcile", "--drain-only"],
            stdout=stdout,
            stderr=stderr,
        )
        == 4
    )
    assert not stdout.getvalue() and "invalid_admin_input" in stderr.getvalue()
    assert not root.exists()
