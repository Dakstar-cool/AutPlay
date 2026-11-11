"""Protocol domains and SQL comments must not become Alembic bind parameters."""

from collections.abc import Callable
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import cast

import pytest
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Dialect

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
PORT_MIGRATIONS = tuple(
    path.name for path in sorted(MIGRATIONS.glob("*.py")) if "0067" <= path.name[:4] <= "0074"
)


@pytest.mark.parametrize("filename", PORT_MIGRATIONS)
@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_port_migration_sql_has_no_accidental_parameters(
    filename: str, direction: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    specification = spec_from_file_location("port_migration_under_test", MIGRATIONS / filename)
    assert specification is not None and specification.loader is not None
    migration = module_from_spec(specification)
    specification.loader.exec_module(migration)
    statements: list[str] = []

    def record(statement: str) -> None:
        statements.append(statement)

    monkeypatch.setattr(op, "execute", record)
    cast(Callable[[], None], getattr(migration, direction))()
    assert statements
    for statement in statements:
        compiled = text(statement).compile(
            dialect=cast(Callable[[], Dialect], postgresql.dialect)()
        )
        assert not compiled.params, (filename, direction, sorted(compiled.params))
