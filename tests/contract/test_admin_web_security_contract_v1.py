"""Implementation-independent M6-A administrative Web security decisions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "contracts" / "admin-web" / "v1" / "security-policy.json"
CONTRACT_PATH = ROOT / "docs" / "design" / "AutPlay_Admin_Web_Security_Contract_v1.md"
ADR_PATH = ROOT / "docs" / "adr" / "ADR-031-m6-administrative-web-session-security.md"


def policy() -> dict[str, object]:
    return cast(dict[str, object], json.loads(POLICY_PATH.read_text(encoding="utf-8")))


def test_m6a_is_explicitly_accepted_before_implementation() -> None:
    assert policy()["status"] == "ACCEPTED"
    assert "| Status | ACCEPTED |" in CONTRACT_PATH.read_text(encoding="utf-8")
    assert "Status: Accepted" in ADR_PATH.read_text(encoding="utf-8")


def test_only_owner_and_admin_can_open_web_sessions() -> None:
    assert policy()["allowed_roles"] == ["OWNER", "ADMIN"]


def test_cli_invitation_and_session_bounds_are_exact() -> None:
    invitation = policy()["browser_invitation"]
    session = policy()["session"]
    assert isinstance(invitation, dict) and isinstance(session, dict)
    assert invitation == {
        "delivery": "LOCAL_CLI_ATTACHED_TTY_ONLY",
        "entropy_bytes": 32,
        "default_ttl_seconds": 300,
        "max_ttl_seconds": 600,
        "max_active_per_user": 3,
        "max_issued_per_operator_hour": 10,
        "persisted_secret_form": "SHA256_ONLY",
    }
    assert session == {
        "bearer_entropy_bytes": 32,
        "idle_expiry_seconds": 1800,
        "absolute_expiry_seconds": 43200,
        "rotation_seconds": 900,
        "max_active_per_user": 8,
        "persisted_bearer_form": "SHA256_ONLY",
        "predecessor_authority": "NONE",
    }


def test_release_cookie_and_loopback_exception_are_unambiguous() -> None:
    release = policy()["release_cookie"]
    preauth = policy()["release_preauth_cookie"]
    development = policy()["unsafe_development"]
    assert isinstance(release, dict) and isinstance(preauth, dict)
    assert isinstance(development, dict)
    assert release == {
        "name": "__Host-autplay_admin",
        "secure": True,
        "http_only": True,
        "same_site": "Strict",
        "path": "/",
        "domain_attribute": False,
    }
    assert preauth == {
        "name": "__Host-autplay_login",
        "secure": True,
        "http_only": True,
        "same_site": "Strict",
        "path": "/",
        "max_age_seconds": 300,
        "domain_attribute": False,
    }
    assert development["cleartext_hosts"] == ["127.0.0.1", "[::1]"]
    assert development["preauth_cookie_name"] == "autplay_login_dev"
    assert development["preauth_cookie_path"] == "/admin/login"
    assert development["preauth_max_age_seconds"] == 300


def test_authenticated_and_login_mutations_have_distinct_complete_guards() -> None:
    assert policy()["authenticated_mutation_guards"] == [
        "ACTIVE_SERVER_SESSION",
        "EXACT_ORIGIN",
        "SYNCHRONIZER_TOKEN",
        "APPLICATION_OPERATION_ID",
    ]
    assert policy()["login_bootstrap_guards"] == [
        "EXACT_ORIGIN",
        "PREAUTH_COOKIE",
        "PREAUTH_FORM_NONCE",
        "LOGIN_OPERATION_ID",
        "BROWSER_INVITATION",
    ]
    assert policy()["login_lost_response"] == ("TERMINAL_UNKNOWN_NEW_CLI_INVITATION_REQUIRED")
    assert policy()["cross_origin_cors"] is False
    assert policy()["get_head_mutation"] is False


def test_rotation_is_an_explicit_auth_maintenance_exception() -> None:
    rotation = policy()["rotation"]
    assert isinstance(rotation, dict)
    assert rotation == {
        "safe_get_auth_maintenance": True,
        "head_rotation": False,
        "due_post_application_mutation": False,
        "lost_response": "NEW_CLI_INVITATION_REQUIRED",
    }


def test_revoked_initiating_cookie_can_only_read_exact_terminal_receipt() -> None:
    retry = policy()["terminal_lifecycle_retry"]
    assert isinstance(retry, dict)
    assert retry == {
        "applies_to": [
            "LOGOUT_CURRENT_BROWSER",
            "LOGOUT_ALL_BROWSER",
            "REVOKE_INITIATING_BROWSER_SESSION",
        ],
        "active_authority": False,
        "exact_bindings": [
            "SERVER_INSTANCE_ID",
            "USER_ID",
            "WEB_SESSION_ID",
            "TOKEN_GENERATION",
            "TOKEN_SHA256",
            "OPERATION_ID",
            "ACTION",
            "TARGET",
            "REASON_CODE",
            "CANONICAL_REQUEST_SHA256",
        ],
        "result": "ORIGINAL_TERMINAL_RESPONSE_AND_COOKIE_CLEAR_ONLY",
        "retention": "ABSOLUTE_SESSION_EXPIRY_PLUS_300_SECONDS",
        "cleanup_max_seconds_after_retention": 86400,
    }


def test_login_and_rotation_unknown_outcomes_have_stable_codes() -> None:
    codes = set(cast(list[str], policy()["stable_failure_codes"]))
    assert "browser_login_outcome_unknown" in codes
    assert "browser_session_rotation_required" in codes


def test_browser_never_receives_an_m5_enrollment_bearer() -> None:
    assert policy()["web_enrollment_invitation_capability"] == ("LIST_AND_CANCEL_METADATA_ONLY")
    prohibited = set(cast(list[str], policy()["prohibited_authentication_secret_locations"]))
    assert {"URL", "HTML_RESPONSE", "JAVASCRIPT_STORAGE", "LOG", "EXPORT"} <= prohibited
    assert policy()["request_integrity_nonce_html"] == ("HIDDEN_NO_STORE_SAME_ORIGIN_ONLY")
