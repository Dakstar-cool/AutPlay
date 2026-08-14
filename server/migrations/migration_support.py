"""Frozen reference-DDL loader and safe reversible migration helpers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

import sqlalchemy as sa
from alembic import op

REFERENCE_SQL_SHA256: Final = "596ec53be759a9c6851b3280d2a8335c8bbd5d1424bf152b43f5d13407fe02f9"
REFERENCE_SQL_PATH: Final = Path(__file__).with_name("reference_v1.sql")
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ReferenceStatement:
    """One classified statement from the immutable reference SQL asset."""

    sql: str
    kind: str
    name: str
    schema: str | None = None
    relation: str | None = None


def _split_sql(script: str) -> tuple[str, ...]:
    """Split PostgreSQL SQL while preserving quoted and dollar-quoted bodies."""
    statements: list[str] = []
    current: list[str] = []
    index = 0
    single_quoted = False
    double_quoted = False
    line_comment = False
    block_comment_depth = 0
    dollar_tag: str | None = None

    while index < len(script):
        if dollar_tag is not None:
            if script.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
            else:
                current.append(script[index])
                index += 1
            continue

        character = script[index]
        next_character = script[index + 1] if index + 1 < len(script) else ""

        if line_comment:
            current.append(character)
            index += 1
            if character == "\n":
                line_comment = False
            continue

        if block_comment_depth:
            if character == "/" and next_character == "*":
                current.extend((character, next_character))
                block_comment_depth += 1
                index += 2
            elif character == "*" and next_character == "/":
                current.extend((character, next_character))
                block_comment_depth -= 1
                index += 2
            else:
                current.append(character)
                index += 1
            continue

        if single_quoted:
            current.append(character)
            index += 1
            if character == "'":
                if index < len(script) and script[index] == "'":
                    current.append(script[index])
                    index += 1
                else:
                    single_quoted = False
            continue

        if double_quoted:
            current.append(character)
            index += 1
            if character == '"':
                if index < len(script) and script[index] == '"':
                    current.append(script[index])
                    index += 1
                else:
                    double_quoted = False
            continue

        if character == "-" and next_character == "-":
            current.extend((character, next_character))
            line_comment = True
            index += 2
            continue
        if character == "/" and next_character == "*":
            current.extend((character, next_character))
            block_comment_depth = 1
            index += 2
            continue
        if character == "'":
            current.append(character)
            single_quoted = True
            index += 1
            continue
        if character == '"':
            current.append(character)
            double_quoted = True
            index += 1
            continue
        if character == "$":
            tag_match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", script[index:])
            if tag_match is not None:
                dollar_tag = tag_match.group(0)
                current.append(dollar_tag)
                index += len(dollar_tag)
                continue
        current.append(character)
        index += 1
        if character == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current.clear()

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    if single_quoted or double_quoted or block_comment_depth or dollar_tag is not None:
        raise RuntimeError("frozen reference SQL contains an unterminated quoted construct")
    return tuple(statements)


def _statement_core(statement: str) -> str:
    lines = statement.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
        lines.pop(0)
    return "\n".join(lines).strip()


def _classify(statement: str) -> ReferenceStatement:
    core = _statement_core(statement)
    match = re.match(r"CREATE EXTENSION IF NOT EXISTS ([a-z0-9_]+)", core, re.I)
    if match:
        return ReferenceStatement(statement, "extension", match.group(1))
    match = re.match(r"CREATE SCHEMA ([a-z0-9_]+)", core, re.I)
    if match:
        return ReferenceStatement(statement, "schema", match.group(1))
    match = re.match(r"CREATE TABLE ([a-z0-9_]+)\.([a-z0-9_]+)\s*\(", core, re.I)
    if match:
        return ReferenceStatement(statement, "table", match.group(2), match.group(1))
    match = re.match(
        r"CREATE (?:UNIQUE )?INDEX ([a-z0-9_]+)\s+ON\s+"
        r"([a-z0-9_]+)\.([a-z0-9_]+)",
        core,
        re.I | re.S,
    )
    if match:
        return ReferenceStatement(
            statement, "index", match.group(1), match.group(2), match.group(3)
        )
    match = re.match(r"CREATE FUNCTION ([a-z0-9_]+)\.([a-z0-9_]+)\s*\(", core, re.I)
    if match:
        return ReferenceStatement(statement, "function", match.group(2), match.group(1))
    match = re.match(
        r"CREATE (?:CONSTRAINT )?TRIGGER ([a-z0-9_]+).*?\bON\s+"
        r"([a-z0-9_]+)\.([a-z0-9_]+)",
        core,
        re.I | re.S,
    )
    if match:
        return ReferenceStatement(
            statement, "trigger", match.group(1), match.group(2), match.group(3)
        )
    match = re.match(
        r"ALTER TABLE ([a-z0-9_]+)\.([a-z0-9_]+)\s+ADD CONSTRAINT\s+([a-z0-9_]+)",
        core,
        re.I | re.S,
    )
    if match:
        return ReferenceStatement(
            statement, "alter_constraint", match.group(3), match.group(1), match.group(2)
        )
    if re.match(r"REVOKE ALL ON SCHEMA app_private FROM PUBLIC", core, re.I):
        return ReferenceStatement(statement, "revoke", "app_private_schema_public")
    if re.match(r"REVOKE ALL ON ALL TABLES", core, re.I):
        return ReferenceStatement(statement, "revoke", "all_tables_public")
    if re.match(r"REVOKE ALL ON ALL SEQUENCES", core, re.I):
        return ReferenceStatement(statement, "revoke", "all_sequences_public")
    if re.match(r"REVOKE ALL ON ALL FUNCTIONS", core, re.I):
        return ReferenceStatement(statement, "revoke", "app_private_functions_public")
    if core.upper() in {"BEGIN;", "COMMIT;"}:
        return ReferenceStatement(statement, "transaction", core[:-1].lower())
    raise RuntimeError(f"unclassified frozen reference statement: {core[:120]!r}")


@lru_cache(maxsize=1)
def reference_statements() -> tuple[ReferenceStatement, ...]:
    """Load, hash-check and classify the vendored physical contract."""
    raw = REFERENCE_SQL_PATH.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != REFERENCE_SQL_SHA256:
        raise RuntimeError(
            "frozen migration reference SQL hash mismatch: "
            f"expected {REFERENCE_SQL_SHA256}, got {actual_hash}"
        )
    statements = tuple(_classify(value) for value in _split_sql(raw.decode("utf-8")))
    expected_counts = {
        "extension": 2,
        "schema": 12,
        "table": 57,
        "index": 53,
        "function": 13,
        "trigger": 40,
        "alter_constraint": 3,
        "revoke": 4,
        "transaction": 2,
    }
    actual_counts = {
        kind: sum(item.kind == kind for item in statements) for kind in expected_counts
    }
    if actual_counts != expected_counts:
        raise RuntimeError(
            f"frozen reference SQL inventory mismatch: {actual_counts!r} != {expected_counts!r}"
        )
    return statements


def _select(kind: str, names: tuple[str, ...]) -> tuple[ReferenceStatement, ...]:
    requested = set(names)
    if len(requested) != len(names):
        raise RuntimeError(f"duplicate {kind} name in migration manifest")
    selected = tuple(
        statement
        for statement in reference_statements()
        if statement.kind == kind and statement.name in requested
    )
    found = {statement.name for statement in selected}
    if found != requested or len(selected) != len(names):
        raise RuntimeError(
            f"frozen reference {kind} selection mismatch: missing={requested - found}, "
            f"unexpected={found - requested}"
        )
    return selected


def execute_reference(kind: str, names: tuple[str, ...]) -> None:
    """Execute selected reference objects in canonical source order."""
    for statement in _select(kind, names):
        op.execute(sa.text(statement.sql))


def _qualified(value: str) -> str:
    parts = value.split(".")
    if len(parts) != 2 or any(IDENTIFIER.fullmatch(part) is None for part in parts):
        raise RuntimeError(f"unsafe qualified migration identifier: {value!r}")
    return value


def drop_tables(tables: tuple[str, ...]) -> None:
    """Drop already dependency-ordered tables without CASCADE."""
    for table in tables:
        op.execute(sa.text(f"DROP TABLE {_qualified(table)}"))


def drop_constraints(constraints: tuple[tuple[str, str], ...]) -> None:
    """Drop named late-bound constraints without CASCADE."""
    for table, constraint in constraints:
        if IDENTIFIER.fullmatch(constraint) is None:
            raise RuntimeError(f"unsafe migration constraint identifier: {constraint!r}")
        op.execute(sa.text(f"ALTER TABLE {_qualified(table)} DROP CONSTRAINT {constraint}"))


def drop_indexes(index_names: tuple[str, ...]) -> None:
    """Drop reference indexes in caller-supplied reverse dependency order."""
    index_map = {statement.name: statement for statement in _select("index", index_names)}
    for name in index_names:
        statement = index_map[name]
        if statement.schema is None:
            raise RuntimeError(f"reference index has no schema: {name}")
        op.execute(sa.text(f"DROP INDEX {statement.schema}.{name}"))


def drop_triggers(trigger_names: tuple[str, ...]) -> None:
    """Drop reference triggers from their owning relations."""
    trigger_map = {statement.name: statement for statement in _select("trigger", trigger_names)}
    for name in trigger_names:
        statement = trigger_map[name]
        if statement.schema is None or statement.relation is None:
            raise RuntimeError(f"reference trigger has no owning relation: {name}")
        op.execute(sa.text(f"DROP TRIGGER {name} ON {statement.schema}.{statement.relation}"))


def drop_functions(functions: tuple[tuple[str, str], ...]) -> None:
    """Drop functions by explicit PostgreSQL identity arguments."""
    for function, identity_arguments in functions:
        op.execute(sa.text(f"DROP FUNCTION {_qualified(function)}({identity_arguments})"))


def drop_schemas(schemas: tuple[str, ...]) -> None:
    """Drop empty migration-owned schemas without CASCADE."""
    for schema in schemas:
        if IDENTIFIER.fullmatch(schema) is None:
            raise RuntimeError(f"unsafe migration schema identifier: {schema!r}")
        op.execute(sa.text(f"DROP SCHEMA {schema}"))


def drop_extensions(extensions: tuple[str, ...]) -> None:
    """Drop migration-owned extensions after all dependent objects are gone."""
    for extension in extensions:
        if IDENTIFIER.fullmatch(extension) is None:
            raise RuntimeError(f"unsafe migration extension identifier: {extension!r}")
        op.execute(sa.text(f"DROP EXTENSION {extension}"))
