"""Explicit local operator commands for independent deletion evidence and bounded purge."""

import argparse
import json
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.readiness import PostgreSQLReadinessProbe
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.account_recovery import database_now
from autplay.domain.privacy_deletion import DeletionEvidenceError
from autplay.entrypoints.privacy_deletion import (
    build_deletion_ledger,
    build_privacy_deletion_service,
)
from autplay.runtime.settings import SettingsLoadError, load_worker_settings


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autplay-privacy-admin")
    parser.add_argument("command", choices=("initialize-ledger", "restore-guard", "purge-due"))
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(arguments)
    result: dict[str, bool | int]
    try:
        settings = load_worker_settings()
        ledger = build_deletion_ledger(settings)
        if ledger is None:
            raise DeletionEvidenceError()
        engine = create_runtime_engine(settings)
        try:
            if not PostgreSQLReadinessProbe(engine).check().ready:
                raise DeletionEvidenceError()
            if args.command == "initialize-ledger":
                # Offline cutover: old acceptors must already be stopped. The same
                # PostgreSQL clock defines their freshness window and this boundary.
                with Session(engine) as session, session.begin():
                    coverage_started_at = database_now(session)
                ledger.initialize(coverage_started_at=coverage_started_at)
                result = {"initialized": True}
            else:
                service = build_privacy_deletion_service(settings, engine)
                if service is None:
                    raise DeletionEvidenceError()
                result = {"reapplied": service.restore_guard()}
                if args.command == "purge-due":
                    result["purged"] = service.run_due(args.limit)
        finally:
            engine.dispose()
    except DeletionEvidenceError, SettingsLoadError, SQLAlchemyError, ValueError:
        print('{"error":"privacy_deletion_unavailable"}')
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
