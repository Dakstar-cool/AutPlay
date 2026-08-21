from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from autplay.domain.web_admin import WebSessionMetadata
from autplay.entrypoints.admin import run_web_session_recovery


class _RecoveryService:
    def __init__(self) -> None:
        self.session_id = uuid4()

    def list_browser_sessions(
        self, user_id: UUID, *, limit: int = 100
    ) -> tuple[WebSessionMetadata, ...]:
        assert limit == 7
        now = datetime.now(UTC)
        return (
            WebSessionMetadata(
                self.session_id,
                user_id,
                2,
                now,
                now,
                now + timedelta(minutes=30),
                now + timedelta(hours=12),
                None,
            ),
        )

    def revoke_browser_session_local(
        self, user_id: UUID, session_id: UUID, operation_id: UUID
    ) -> bool:
        del user_id, operation_id
        assert session_id == self.session_id
        return True

    def revoke_all_browser_sessions_local(self, user_id: UUID, operation_id: UUID) -> int:
        del user_id, operation_id
        return 3


def test_cli_list_is_bounded_metadata_without_cookie_or_hash() -> None:
    service = _RecoveryService()
    stdout, stderr = io.StringIO(), io.StringIO()
    result = run_web_session_recovery(
        service,
        ["web-session-list", "--user-id", str(uuid4()), "--limit", "7"],
        stdout=stdout,
        stderr=stderr,
    )

    document = json.loads(stdout.getvalue())
    assert result == 0 and stderr.getvalue() == ""
    assert document["sessions"][0]["web_session_id"] == str(service.session_id)
    assert "token_sha256" not in stdout.getvalue().lower()
    assert "cookie" not in stdout.getvalue().lower()
    assert "sha" not in stdout.getvalue().lower()


def test_cli_revoke_one_and_all_return_only_terminal_counts() -> None:
    service = _RecoveryService()
    user_id, operation_id = uuid4(), uuid4()
    one_out, all_out = io.StringIO(), io.StringIO()

    assert (
        run_web_session_recovery(
            service,
            [
                "web-session-revoke",
                "--user-id",
                str(user_id),
                "--session-id",
                str(service.session_id),
                "--operation-id",
                str(operation_id),
            ],
            stdout=one_out,
            stderr=io.StringIO(),
        )
        == 0
    )
    assert json.loads(one_out.getvalue()) == {"outcome": "APPLIED"}
    assert (
        run_web_session_recovery(
            service,
            [
                "web-session-revoke-all",
                "--user-id",
                str(user_id),
                "--operation-id",
                str(operation_id),
            ],
            stdout=all_out,
            stderr=io.StringIO(),
        )
        == 0
    )
    assert json.loads(all_out.getvalue()) == {"outcome": "APPLIED", "revoked_count": 3}
