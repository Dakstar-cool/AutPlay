"""Local-only administrative CLI surfaces for authentication bootstrap."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.adapters.postgresql.vault_uow import SqlAlchemyVaultUnitOfWorkFactory
from autplay.application.auth import AuthService, BootstrapOwnerCommand
from autplay.application.vault_reconciliation import ReconcileMode, VaultReconciliationService
from autplay.domain.auth import (
    AuthenticationError,
    DeviceDescription,
    DevicePlatform,
    TokenPair,
)
from autplay.entrypoints.composition import build_auth_service
from autplay.runtime.settings import SettingsLoadError, load_api_settings, load_worker_settings

SERVICE_NAME = "autplay-admin"


def build_parser() -> argparse.ArgumentParser:
    """Build the bounded local administrative command parser."""

    parser = argparse.ArgumentParser(prog="autplay-admin")
    subcommands = parser.add_subparsers(dest="command", required=True)
    bootstrap = subcommands.add_parser(
        "bootstrap-owner",
        help="create the first owner, first device, and first device session",
    )
    bootstrap.add_argument("--display-name", required=True)
    bootstrap.add_argument("--device-name", required=True)
    bootstrap.add_argument(
        "--platform",
        choices=[item.value for item in DevicePlatform],
        default=DevicePlatform.OTHER.value,
    )
    bootstrap.add_argument("--app-version", required=True)
    reconcile = subcommands.add_parser(
        "vault-reconcile", help="run bounded Vault reconciliation without revealing storage keys"
    )
    reconcile.add_argument("--apply", action="store_true", help="apply safe metadata repairs")
    reconcile.add_argument("--limit", type=int, default=100)
    return parser


def run_bootstrap(
    service: AuthService,
    arguments: Sequence[str],
    *,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Execute the local bootstrap and intentionally emit its tokens once.

    This function writes directly to the caller-selected stream. It never uses
    the logging subsystem, and token-bearing results have redacted ``repr``.
    """

    namespace = build_parser().parse_args(list(arguments))
    if namespace.command != "bootstrap-owner":
        _write_error(stderr, "unknown_admin_command")
        return 2
    try:
        result = service.bootstrap_owner(
            BootstrapOwnerCommand(
                display_name=str(namespace.display_name),
                device=DeviceDescription(
                    name=str(namespace.device_name),
                    platform=DevicePlatform(str(namespace.platform)),
                    app_version=str(namespace.app_version),
                ),
            )
        )
    except (AuthenticationError, ValueError) as error:
        code = error.code if isinstance(error, AuthenticationError) else "invalid_admin_input"
        _write_error(stderr, code)
        return 4
    _write_tokens(stdout, result)
    return 0


def run_vault_reconcile(
    service: VaultReconciliationService,
    arguments: Sequence[str],
    *,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Execute the bounded P06 local reconciliation command with aggregate output."""

    namespace = build_parser().parse_args(list(arguments))
    if namespace.command != "vault-reconcile":
        _write_error(stderr, "unknown_admin_command")
        return 2
    try:
        report = service.run(
            mode=ReconcileMode.APPLY if namespace.apply else ReconcileMode.DRY_RUN,
            limit=int(namespace.limit),
        )
    except ValueError:
        _write_error(stderr, "invalid_admin_input")
        return 4
    json.dump(
        {
            "inspected": report.inspected,
            "repaired": report.repaired,
            "quarantined": report.quarantined,
            "remaining": report.remaining,
        },
        stdout,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    stdout.write("\n")
    return 0


def main(
    arguments: Sequence[str] | None = None,
    *,
    service_factory: Callable[[], AuthService] | None = None,
) -> int:
    """Load API auth settings and execute the trusted local command."""

    command_arguments = sys.argv[1:] if arguments is None else arguments
    command = command_arguments[0] if command_arguments else None
    if service_factory is not None:
        return run_bootstrap(
            service_factory(),
            command_arguments,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    if command == "vault-reconcile":
        try:
            settings = load_worker_settings()
        except SettingsLoadError as error:
            _write_error(sys.stderr, error.code)
            return 2
        engine = create_runtime_engine(settings)
        try:
            sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
            with SqlAlchemyVaultUnitOfWorkFactory(sessions)() as unit:
                result = run_vault_reconcile(
                    VaultReconciliationService(
                        repository=unit.vault,
                        storage=FilesystemVaultStorage(settings.vault_root),
                    ),
                    command_arguments,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
                if result == 0:
                    unit.commit()
                return result
        except SQLAlchemyError:
            _write_error(sys.stderr, "database_unavailable")
            return 3
        finally:
            engine.dispose()

    try:
        api_settings = load_api_settings()
    except SettingsLoadError as error:
        _write_error(sys.stderr, error.code)
        return 2
    engine = create_runtime_engine(api_settings)
    try:
        return run_bootstrap(
            build_auth_service(api_settings, engine),
            command_arguments,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except SQLAlchemyError:
        _write_error(sys.stderr, "database_unavailable")
        return 3
    finally:
        engine.dispose()


def _write_tokens(stream: TextIO, result: TokenPair) -> None:
    document = {
        "access_token": result.access_token,
        "refresh_token": result.refresh_token,
        "token_type": result.token_type,
        "access_expires_at": result.access_expires_at.isoformat(),
        "refresh_expires_at": result.refresh_expires_at.isoformat(),
        "user_id": str(result.user_id),
        "device_id": str(result.device_id),
        "session_id": str(result.session_id),
    }
    json.dump(document, stream, ensure_ascii=True, separators=(",", ":"))
    stream.write("\n")


def _write_error(stream: TextIO, code: str) -> None:
    json.dump({"error": {"code": code}}, stream, ensure_ascii=True, separators=(",", ":"))
    stream.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "SERVICE_NAME",
    "build_parser",
    "main",
    "run_bootstrap",
    "run_vault_reconcile",
)
