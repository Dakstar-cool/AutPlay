"""Explicit offline provisioning of the independent R1C serving-consent head."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from autplay.adapters.filesystem.serving_consent_ledger import FilesystemServingConsentLedger
from autplay.domain.serving_consent import ServingConsentEvidenceError
from autplay.runtime.settings import (
    ApiSettings,
    SettingsLoadError,
    StreamSettings,
    WorkerSettings,
    load_worker_settings,
)

type ServingSettings = ApiSettings | StreamSettings | WorkerSettings


def build_serving_consent_ledger(
    settings: ServingSettings,
) -> FilesystemServingConsentLedger | None:
    path = settings.serving_consent_ledger_path
    if path is None:
        return None
    key, key_id = settings.serving_consent_ledger_key, settings.serving_consent_ledger_key_id
    if key is None or key_id is None:
        raise ServingConsentEvidenceError()
    return FilesystemServingConsentLedger(path, key.get_secret_value().encode(), key_id)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autplay-serving-consent-admin")
    parser.add_argument("command", choices=("initialize-ledger", "verify-head"))
    args = parser.parse_args(arguments)
    try:
        ledger = build_serving_consent_ledger(load_worker_settings())
        if ledger is None:
            raise ServingConsentEvidenceError()
        if args.command == "initialize-ledger":
            ledger.initialize()
            result: dict[str, bool | int] = {"initialized": True}
        else:
            history = ledger.read()
            result = {"head_valid": True, "event_count": len(history.operations)}
    except ServingConsentEvidenceError, SettingsLoadError, ValueError:
        print('{"error":"serving_consent_evidence_unavailable"}')
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
