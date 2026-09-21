"""Explicit independent consent provisioning and conservative restore reconciliation."""

import argparse
import json
from collections.abc import Sequence

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.training_consent_ledger import FilesystemTrainingConsentLedger
from autplay.adapters.postgresql.readiness import PostgreSQLReadinessProbe
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.training_consent import TrainingConsentError, TrainingConsentService
from autplay.domain.training_consent import TrainingConsentEvidenceError
from autplay.runtime.settings import (
    ApiSettings,
    SettingsLoadError,
    StreamSettings,
    WorkerSettings,
    load_worker_settings,
)

type ConsentSettings = ApiSettings | StreamSettings | WorkerSettings


def build_training_consent_ledger(
    settings: ConsentSettings,
) -> FilesystemTrainingConsentLedger | None:
    if settings.training_consent_ledger_path is None:
        return None
    if (
        settings.training_consent_ledger_key is None
        or settings.training_consent_ledger_key_id is None
    ):
        raise TrainingConsentEvidenceError()
    return FilesystemTrainingConsentLedger(
        settings.training_consent_ledger_path,
        settings.training_consent_ledger_key.get_secret_value().encode(),
        settings.training_consent_ledger_key_id,
    )


def enforce_training_consent_restore_guard(
    settings: ConsentSettings, engine: Engine | None = None
) -> None:
    ledger = build_training_consent_ledger(settings)
    if ledger is None:
        return
    ledger.read()
    owned_engine = engine is None
    resolved_engine = engine or create_runtime_engine(settings)
    try:
        TrainingConsentService(
            sessionmaker(resolved_engine, class_=Session, expire_on_commit=False), ledger
        ).restore_guard()
    except SQLAlchemyError, TrainingConsentError:
        raise TrainingConsentEvidenceError() from None
    finally:
        if owned_engine:
            resolved_engine.dispose()


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autplay-training-consent-admin")
    parser.add_argument("command", choices=("initialize-ledger", "restore-guard"))
    args = parser.parse_args(arguments)
    result: dict[str, bool | int]
    try:
        settings = load_worker_settings()
        ledger = build_training_consent_ledger(settings)
        if ledger is None:
            raise TrainingConsentEvidenceError()
        if args.command == "initialize-ledger":
            ledger.initialize()
            result = {"initialized": True}
        else:
            ledger.read()
            engine = create_runtime_engine(settings)
            try:
                if not PostgreSQLReadinessProbe(engine).check().ready:
                    raise TrainingConsentEvidenceError()
                result = {
                    "reconciled": TrainingConsentService(
                        sessionmaker(engine, class_=Session, expire_on_commit=False), ledger
                    ).restore_guard()
                }
            finally:
                engine.dispose()
    except (
        TrainingConsentEvidenceError,
        TrainingConsentError,
        SettingsLoadError,
        SQLAlchemyError,
        ValueError,
    ):
        print('{"error":"training_consent_restore_unavailable"}')
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
