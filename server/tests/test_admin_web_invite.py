from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from uuid import UUID, uuid4

from autplay.domain.web_admin import BrowserInvitation
from autplay.entrypoints.admin import run_web_session_invite


class _Tty(StringIO):
    def isatty(self) -> bool:
        return True


class _Service:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def issue_invitation(self, user_id: UUID) -> BrowserInvitation:
        if self.error:
            raise self.error
        return BrowserInvitation(uuid4(), uuid4(), user_id, datetime.now(UTC), b"one-time-secret")


def test_invitation_secret_is_tty_only() -> None:
    stdout, stderr = _Tty(), StringIO()
    assert (
        run_web_session_invite(
            _Service(),
            ["web-session-invite", "--user-id", str(uuid4())],
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert stdout.getvalue() == "one-time-secret\n"
    assert "one-time-secret" not in stderr.getvalue()


def test_invitation_refuses_redirected_stdout() -> None:
    stdout, stderr = StringIO(), StringIO()
    assert (
        run_web_session_invite(
            _Service(),
            ["web-session-invite", "--user-id", str(uuid4())],
            stdout=stdout,
            stderr=stderr,
        )
        == 2
    )
    assert stdout.getvalue() == ""
    assert "web_invitation_tty_required" in stderr.getvalue()


def test_invitation_rejects_invalid_user_id() -> None:
    stdout, stderr = _Tty(), StringIO()
    assert (
        run_web_session_invite(
            _Service(), ["web-session-invite", "--user-id", "bad"], stdout=stdout, stderr=stderr
        )
        == 4
    )
    assert stdout.getvalue() == ""
