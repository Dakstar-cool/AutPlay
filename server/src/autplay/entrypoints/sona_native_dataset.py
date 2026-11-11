"""Run the native adapter in counts-only mode using the API's current authority."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.sona_native_dataset import audit_native_dataset
from autplay.application.sona_native_dataset import NativeDatasetRejected
from autplay.application.training_consent import TrainingConsentService
from autplay.entrypoints.training_consent_restore import build_training_consent_ledger
from autplay.runtime.settings import load_api_settings


def _boundary(value: str) -> int:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return int(result.timestamp() * 1000)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-end", type=_boundary, required=True)
    parser.add_argument("--validation-end", type=_boundary, required=True)
    parser.add_argument("--excluded-events-file", type=Path)
    args = parser.parse_args(arguments)
    engine = None
    try:
        excluded = frozenset[UUID]()
        if args.excluded_events_file is not None:
            if args.excluded_events_file.stat().st_size > 1024 * 1024:
                raise NativeDatasetRejected("EXCLUSION_FILE_LIMIT")
            raw = json.loads(args.excluded_events_file.read_bytes())
            if not isinstance(raw, list):
                raise NativeDatasetRejected("EXCLUSION_FILE_INVALID")
            excluded = frozenset(UUID(item) for item in raw)
        settings = load_api_settings()
        ledger = build_training_consent_ledger(settings)
        if ledger is None:
            raise NativeDatasetRejected("INDEPENDENT_LEDGER_REQUIRED")
        history = ledger.read()
        engine = create_engine(
            settings.database_url.get_secret_value(),
            echo=False,
            hide_parameters=True,
            isolation_level="REPEATABLE READ",
            connect_args={
                "connect_timeout": 5,
                "options": (
                    "-c default_transaction_read_only=on "
                    "-c statement_timeout=20000 -c lock_timeout=1000"
                ),
            },
        )
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        consent = TrainingConsentService(sessions, ledger)
        with sessions() as session:
            report = audit_native_dataset(
                session,
                consent=consent,
                history=history,
                train_end_ms=args.train_end,
                validation_end_ms=args.validation_end,
                excluded_event_ids=excluded,
            )
            session.rollback()
        if ledger.read() != history:
            raise NativeDatasetRejected("CONSENT_CHANGED_DURING_AUDIT")
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as error:
        # Never log DB parameters, raw event documents or exception text from parsers.
        code = (
            str(error)
            if isinstance(error, NativeDatasetRejected)
            else "NATIVE_DATASET_AUDIT_FAILED"
        )
        print(json.dumps({"status": "blocked", "code": code}), file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
