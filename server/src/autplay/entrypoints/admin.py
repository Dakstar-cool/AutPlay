"""Local-only administrative CLI surfaces for authentication bootstrap."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import Protocol, TextIO
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.adapters.postgresql.vault_uow import SqlAlchemyVaultUnitOfWorkFactory
from autplay.application.auth import AuthService, BootstrapOwnerCommand
from autplay.application.profile_pairing import ProfilePairingService
from autplay.application.vault_reconciliation import ReconcileMode, VaultReconciliationService
from autplay.domain.auth import (
    AuthenticationError,
    DeviceDescription,
    DevicePlatform,
    TokenPair,
)
from autplay.domain.web_admin import BrowserInvitation, WebSessionMetadata
from autplay.entrypoints.composition import (
    build_auth_service,
    build_profile_pairing_service,
    build_web_admin_service,
)
from autplay.runtime.settings import SettingsLoadError, load_api_settings, load_worker_settings

SERVICE_NAME = "autplay-admin"


class WebInvitationIssuer(Protocol):
    def issue_invitation(self, user_id: UUID) -> BrowserInvitation: ...


class WebSessionRecovery(Protocol):
    def list_browser_sessions(
        self, user_id: UUID, *, limit: int = 100
    ) -> tuple[WebSessionMetadata, ...]: ...
    def revoke_browser_session_local(
        self, user_id: UUID, web_session_id: UUID, operation_id: UUID
    ) -> bool: ...
    def revoke_all_browser_sessions_local(self, user_id: UUID, operation_id: UUID) -> int: ...


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
    invitation = subcommands.add_parser(
        "issue-recovery-invitation",
        help="issue one bounded invitation for an explicitly selected active account",
    )
    invitation.add_argument("--user-id", required=True)
    invitation.add_argument("--operation-id", required=True)
    invitation.add_argument("--expires-in-seconds", type=int, default=600)
    cleanup = subcommands.add_parser(
        "profile-pairing-cleanup", help="delete a bounded batch of expired pairing receipts"
    )
    cleanup.add_argument("--limit", type=int, default=100)
    web_invitation = subcommands.add_parser(
        "web-session-invite", help="print one browser invitation only to an attached TTY"
    )
    web_invitation.add_argument("--user-id", required=True)
    web_list = subcommands.add_parser(
        "web-session-list", help="list bounded browser-session metadata for local recovery"
    )
    web_list.add_argument("--user-id", required=True)
    web_list.add_argument("--limit", type=int, default=100)
    web_revoke = subcommands.add_parser(
        "web-session-revoke", help="revoke one browser session without exposing its cookie"
    )
    web_revoke.add_argument("--user-id", required=True)
    web_revoke.add_argument("--session-id", required=True)
    web_revoke.add_argument("--operation-id", required=True)
    web_revoke_all = subcommands.add_parser(
        "web-session-revoke-all", help="revoke all browser sessions for one owner/admin"
    )
    web_revoke_all.add_argument("--user-id", required=True)
    web_revoke_all.add_argument("--operation-id", required=True)
    return parser


def run_web_session_invite(
    service: WebInvitationIssuer, arguments: Sequence[str], *, stdout: TextIO, stderr: TextIO
) -> int:
    """Issue a browser-only invitation; its bearer is never JSON/log/file output."""
    namespace = build_parser().parse_args(list(arguments))
    if namespace.command != "web-session-invite":
        _write_error(stderr, "unknown_admin_command")
        return 2
    if not stdout.isatty():
        _write_error(stderr, "web_invitation_tty_required")
        return 2
    try:
        invitation = service.issue_invitation(UUID(str(namespace.user_id)))
    except ValueError, RuntimeError:
        _write_error(stderr, "web_invitation_unavailable")
        return 4
    stdout.write(invitation.bearer.decode("ascii") + "\n")
    return 0


def run_web_session_recovery(
    service: WebSessionRecovery, arguments: Sequence[str], *, stdout: TextIO, stderr: TextIO
) -> int:
    """Execute bounded local recovery without ever loading browser cookie material."""

    namespace = build_parser().parse_args(list(arguments))
    try:
        user_id = UUID(str(namespace.user_id))
        document: dict[str, object]
        if namespace.command == "web-session-list":
            rows = service.list_browser_sessions(user_id, limit=int(namespace.limit))
            document = {
                "sessions": [
                    {
                        "web_session_id": str(row.web_session_id),
                        "token_generation": row.token_generation,
                        "issued_at": row.issued_at.isoformat(),
                        "last_activity_at": row.last_activity_at.isoformat(),
                        "idle_expires_at": row.idle_expires_at.isoformat(),
                        "absolute_expires_at": row.absolute_expires_at.isoformat(),
                        "state": "REVOKED" if row.revoked_at is not None else "ACTIVE",
                    }
                    for row in rows
                ]
            }
        elif namespace.command == "web-session-revoke":
            changed = service.revoke_browser_session_local(
                user_id, UUID(str(namespace.session_id)), UUID(str(namespace.operation_id))
            )
            document = {"outcome": "APPLIED" if changed else "ALREADY_TERMINAL"}
        elif namespace.command == "web-session-revoke-all":
            count = service.revoke_all_browser_sessions_local(
                user_id, UUID(str(namespace.operation_id))
            )
            document = {"outcome": "APPLIED", "revoked_count": count}
        else:
            _write_error(stderr, "unknown_admin_command")
            return 2
    except ValueError, RuntimeError:
        _write_error(stderr, "web_session_recovery_unavailable")
        return 4
    stdout.write(json.dumps(document, separators=(",", ":"), sort_keys=True) + "\n")
    return 0


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


def run_recovery_invitation(
    service: ProfilePairingService,
    arguments: Sequence[str],
    *,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Issue an invitation only at the local trusted recovery boundary."""
    namespace = build_parser().parse_args(list(arguments))
    if namespace.command != "issue-recovery-invitation":
        _write_error(stderr, "unknown_admin_command")
        return 2
    try:
        result = service.issue_recovery_invitation(
            UUID(str(namespace.user_id)),
            UUID(str(namespace.operation_id)),
            int(namespace.expires_in_seconds),
        )
    except (ValueError, AuthenticationError) as error:
        del error
        _write_error(stderr, "invalid_admin_input")
        return 4
    except RuntimeError as error:
        code = getattr(error, "code", "recovery_invitation_unavailable")
        _write_error(stderr, str(code))
        return 4
    json.dump(result, stdout, ensure_ascii=True, separators=(",", ":"))
    stdout.write("\n")
    return 0


def run_profile_pairing_cleanup(
    service: ProfilePairingService,
    arguments: Sequence[str],
    *,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Run bounded local-only receipt maintenance without exposing receipt contents."""
    namespace = build_parser().parse_args(list(arguments))
    if namespace.command != "profile-pairing-cleanup":
        _write_error(stderr, "unknown_admin_command")
        return 2
    try:
        deleted = service.cleanup_expired_receipts(int(namespace.limit))
    except ValueError:
        _write_error(stderr, "invalid_admin_input")
        return 4
    json.dump({"deleted": deleted}, stdout, ensure_ascii=True, separators=(",", ":"))
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

    if command in {
        "issue-recovery-invitation",
        "profile-pairing-cleanup",
        "web-session-invite",
        "web-session-list",
        "web-session-revoke",
        "web-session-revoke-all",
    }:
        try:
            api_settings = load_api_settings()
        except SettingsLoadError as error:
            _write_error(sys.stderr, error.code)
            return 2
        engine = create_runtime_engine(api_settings)
        try:
            if command == "web-session-invite":
                web_service = build_web_admin_service(api_settings, engine)
                if web_service is None:
                    _write_error(sys.stderr, "web_admin_configuration_missing")
                    return 2
                return run_web_session_invite(
                    web_service, command_arguments, stdout=sys.stdout, stderr=sys.stderr
                )
            if command.startswith("web-session-"):
                web_service = build_web_admin_service(api_settings, engine)
                if web_service is None:
                    _write_error(sys.stderr, "web_admin_configuration_missing")
                    return 2
                return run_web_session_recovery(
                    web_service, command_arguments, stdout=sys.stdout, stderr=sys.stderr
                )
            pairing_service = build_profile_pairing_service(api_settings, engine)
            if pairing_service is None:
                _write_error(sys.stderr, "profile_identity_configuration_missing")
                return 2
            if command == "issue-recovery-invitation":
                return run_recovery_invitation(
                    pairing_service, command_arguments, stdout=sys.stdout, stderr=sys.stderr
                )
            return run_profile_pairing_cleanup(
                pairing_service, command_arguments, stdout=sys.stdout, stderr=sys.stderr
            )
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
    "run_profile_pairing_cleanup",
    "run_recovery_invitation",
    "run_vault_reconcile",
    "run_web_session_invite",
    "run_web_session_recovery",
)
