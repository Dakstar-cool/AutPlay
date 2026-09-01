import json
import os
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_admin_local_overlay_forces_the_api_to_literal_loopback(tmp_path: Path) -> None:
    secret_paths = {
        "AUTPLAY_RUNTIME_AUTH_SECRET_FILE": tmp_path / "auth.txt",
        "AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE": tmp_path / "public-access-source.txt",
        "AUTPLAY_RUNTIME_ADMIN_SOURCE_SECRET_FILE": tmp_path / "admin-source.txt",
        "AUTPLAY_RUNTIME_ADMIN_CSRF_SECRET_FILE": tmp_path / "admin-csrf.txt",
        "AUTPLAY_RUNTIME_PROFILE_IDENTITY_KEY_FILE": tmp_path / "profile-key.pem",
    }
    for index, path in enumerate(secret_paths.values()):
        path.write_text(chr(ord("a") + index) * 48, encoding="utf-8")
    environment = os.environ.copy()
    environment.update({name: str(path) for name, path in secret_paths.items()})
    environment["AUTPLAY_RUNTIME_BIND_HOST"] = "192.0.2.20"
    environment["AUTPLAY_MOBILE_BIND_HOST"] = "192.0.2.10"
    environment["AUTPLAY_MOBILE_API_PORT"] = "18787"
    environment["AUTPLAY_MOBILE_STREAM_PORT"] = "18788"
    environment["AUTPLAY_PROFILE_API_ORIGIN"] = "http://192.0.2.99:9999"
    environment["AUTPLAY_PROFILE_STREAM_ORIGIN"] = "http://192.0.2.99:9998"

    result = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            "autplay-admin-config-test",
            "-f",
            "deploy/compose/compose.yaml",
            "-f",
            "deploy/compose/compose.runtime.yaml",
            "-f",
            "deploy/compose/compose.admin-local.yaml",
            "--profile",
            "runtime",
            "config",
            "--format",
            "json",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(result.stdout)
    api = config["services"]["api"]
    mobile_api = config["services"]["mobile-api"]
    stream = config["services"]["stream"]

    assert [port["host_ip"] for port in api["ports"]] == ["127.0.0.1"]
    assert [(port["host_ip"], int(port["published"])) for port in mobile_api["ports"]] == [
        ("192.0.2.10", 18787)
    ]
    assert [(port["host_ip"], int(port["published"])) for port in stream["ports"]] == [
        ("192.0.2.10", 18788)
    ]
    assert {secret["target"] for secret in api["secrets"]} == {
        "autplay-admin-csrf-hmac",
        "autplay-admin-source-hmac",
        "autplay-auth-signing-secret",
        "autplay-public-access-source-hmac",
        "autplay-profile-identity-key",
    }
    assert mobile_api["environment"]["AUTPLAY_PROFILE_API_ORIGIN"] == ("http://192.0.2.10:18787")
    assert mobile_api["environment"]["AUTPLAY_PROFILE_STREAM_ORIGIN"] == ("http://192.0.2.10:18788")
    assert "AUTPLAY_ADMIN_WEB_ENABLED" not in mobile_api["environment"]
    assert {secret["target"] for secret in mobile_api["secrets"]} == {
        "autplay-auth-signing-secret",
        "autplay-public-access-source-hmac",
        "autplay-profile-identity-key",
    }
    assert config["services"]["admin-init"]["depends_on"]["mobile-api"]["condition"] == (
        "service_healthy"
    )
    assert "ports" not in config["services"]["admin-init"]


def test_admin_local_runbook_keeps_browser_bootstrap_interactive() -> None:
    runbook = (REPOSITORY_ROOT / "deploy" / "compose" / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(runbook.split())

    assert "exec -it api autplay-admin web-session-invite" in runbook
    assert "does not create an implicit or permanent administrator session" in normalized
