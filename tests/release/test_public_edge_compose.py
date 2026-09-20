import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CADDY_IMAGE = (
    "caddy:2.11.4-alpine@sha256:98eb57d882ccd5213d1688764db10c1ca2c58a1ca3a6717a3411ad798f7a423a"
)
COMPOSE_FILES = (
    "deploy/compose/compose.yaml",
    "deploy/compose/compose.runtime.yaml",
    "deploy/compose/compose.admin-local.yaml",
    "deploy/compose/compose.public-edge.yaml",
)


def test_server_image_precreates_private_ledger_mountpoints_for_non_root_runtime() -> None:
    dockerfile = (REPOSITORY_ROOT / "server" / "Dockerfile").read_text(encoding="utf-8")

    assert "install -d -o autplay -g autplay -m 0700" in dockerfile
    assert "/var/lib/autplay/privacy-ledger" in dockerfile
    assert "/var/lib/autplay/training-consent-ledger" in dockerfile
    assert "/var/lib/autplay/vault/staging" in dockerfile
    assert "/var/lib/autplay/vault/objects" in dockerfile
    assert "/var/lib/autplay/vault/quarantine" in dockerfile


def _config(tmp_path: Path) -> dict[str, object]:
    secret_names = (
        "AUTPLAY_RUNTIME_POSTGRES_PASSWORD_FILE",
        "AUTPLAY_RUNTIME_DATABASE_URL_FILE",
        "AUTPLAY_RUNTIME_AUTH_SECRET_FILE",
        "AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE",
        "AUTPLAY_RUNTIME_ADMIN_SOURCE_SECRET_FILE",
        "AUTPLAY_RUNTIME_ADMIN_CSRF_SECRET_FILE",
        "AUTPLAY_RUNTIME_PROFILE_IDENTITY_KEY_FILE",
        "AUTPLAY_RUNTIME_PRIVACY_LEDGER_KEY_FILE",
        "AUTPLAY_RUNTIME_TRAINING_CONSENT_LEDGER_KEY_FILE",
    )
    environment = os.environ.copy()
    for index, name in enumerate(secret_names):
        path = tmp_path / f"secret-{index}.txt"
        path.write_text(chr(ord("a") + index) * 48, encoding="utf-8")
        environment[name] = str(path)
    environment["AUTPLAY_ACME_EMAIL"] = "operator@example.test"
    environment["AUTPLAY_PRIVACY_LEDGER_KEY_ID"] = "deletion-ledger-v1"
    environment["AUTPLAY_TRAINING_CONSENT_LEDGER_KEY_ID"] = "consent-ledger-v1"
    (tmp_path / "secret-0.txt").write_text("database-password", encoding="utf-8")
    (tmp_path / "secret-1.txt").write_text(
        "postgresql+psycopg://autplay:database-password@postgres:5432/autplay",
        encoding="utf-8",
    )

    command = ["docker", "compose", "-p", "autplay-public-edge-test"]
    for compose_file in COMPOSE_FILES:
        command.extend(("-f", compose_file))
    command.extend(
        (
            "--profile",
            "runtime",
            "--profile",
            "public-edge",
            "--profile",
            "ledger-bootstrap",
            "config",
            "--format",
            "json",
        )
    )
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def test_public_edge_is_the_only_non_loopback_listener(tmp_path: Path) -> None:
    config = _config(tmp_path)
    services = config["services"]
    edge = services["edge"]  # type: ignore[index]
    api = services["api"]  # type: ignore[index]
    mobile_api = services["mobile-api"]  # type: ignore[index]
    stream = services["stream"]  # type: ignore[index]
    postgres = services["postgres"]  # type: ignore[index]

    assert [
        (port["host_ip"], int(port["published"]), port["protocol"]) for port in edge["ports"]
    ] == [("0.0.0.0", 443, "tcp")]
    assert [port["host_ip"] for port in api["ports"]] == ["127.0.0.1"]
    assert "ports" not in mobile_api
    assert "ports" not in stream
    assert edge["cap_drop"] == ["ALL"]
    assert edge["cap_add"] == ["NET_BIND_SERVICE"]
    assert edge["read_only"] is True
    public_edge_addresses = {
        "edge": edge["networks"]["public-edge"]["ipv4_address"],
        "mobile-api": mobile_api["networks"]["public-edge"]["ipv4_address"],
        "stream": stream["networks"]["public-edge"]["ipv4_address"],
    }
    assert public_edge_addresses == {
        "edge": "172.30.77.2",
        "mobile-api": "172.30.77.3",
        "stream": "172.30.77.4",
    }
    assert len(set(public_edge_addresses.values())) == len(public_edge_addresses)
    assert mobile_api["environment"]["AUTPLAY_PUBLIC_ACCESS_TRUSTED_PROXY_IP"] == "172.30.77.2"
    assert mobile_api["environment"]["AUTPLAY_PROFILE"] == "production"
    assert mobile_api["environment"]["AUTPLAY_PROFILE_API_ORIGIN"] == "https://api.autplay.win"
    assert mobile_api["environment"]["AUTPLAY_PROFILE_STREAM_ORIGIN"] == (
        "https://stream.autplay.win"
    )
    assert mobile_api["environment"]["AUTPLAY_SELF_DEVICE_PAIRING_ENABLED"] == "true"
    assert mobile_api["environment"]["AUTPLAY_ACCOUNT_RECOVERY_ENABLED"] == "true"
    assert mobile_api["environment"]["AUTPLAY_ACCOUNT_DELETION_ENABLED"] == "true"
    assert mobile_api["environment"]["AUTPLAY_SHARED_TRAINING_CONSENT_ENABLED"] == "true"
    assert "POSTGRES_PASSWORD" not in postgres["environment"]
    assert postgres["environment"]["POSTGRES_PASSWORD_FILE"] == (
        "/run/secrets/autplay-postgres-password"
    )
    for service in (api, mobile_api, stream, services["migrate"], services["worker-cpu"]):  # type: ignore[index]
        assert service["environment"].get("AUTPLAY_DATABASE_URL") is None
        assert service["environment"]["AUTPLAY_DATABASE_URL_FILE"] == (
            "/run/secrets/autplay-database-url"
        )
    assert {secret["source"] for secret in api["secrets"]} == {
        "autplay-auth-signing-secret",
        "autplay-public-access-source-hmac",
        "autplay-database-url",
        "autplay-privacy-ledger-key",
        "autplay-training-consent-ledger-key",
    }
    assert {secret["source"] for secret in mobile_api["secrets"]} == {
        "autplay-auth-signing-secret",
        "autplay-public-access-source-hmac",
        "autplay-profile-identity-key",
        "autplay-database-url",
        "autplay-privacy-ledger-key",
        "autplay-training-consent-ledger-key",
    }
    assert {secret["source"] for secret in stream["secrets"]} == {
        "autplay-auth-signing-secret",
        "autplay-database-url",
        "autplay-privacy-ledger-key",
        "autplay-training-consent-ledger-key",
    }
    worker = services["worker-cpu"]  # type: ignore[index]
    assert worker["command"] == [
        "python",
        "-m",
        "autplay.entrypoints.worker_cgroup_bootstrap",
    ]
    assert worker["user"] == "0:0"
    assert worker["cgroup"] == "private"
    assert worker["cap_drop"] == ["ALL"]
    assert worker["cap_add"] == [
        "CHOWN",
        "DAC_OVERRIDE",
        "SETGID",
        "SETUID",
        "SYS_ADMIN",
    ]
    assert worker["security_opt"] == ["no-new-privileges:true", "apparmor=unconfined"]
    assert worker["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "autplay.entrypoints.worker_cgroup_bootstrap",
        "--check-readiness",
    ]
    assert {secret["source"] for secret in worker["secrets"]} == {
        "autplay-database-url",
        "autplay-privacy-ledger-key",
        "autplay-training-consent-ledger-key",
    }
    bootstrap_commands = {
        "privacy-ledger-init": [
            "python",
            "-m",
            "autplay.entrypoints.privacy_admin",
            "initialize-ledger",
        ],
        "training-consent-ledger-init": [
            "python",
            "-m",
            "autplay.entrypoints.training_consent_restore",
            "initialize-ledger",
        ],
    }
    for name, command in bootstrap_commands.items():
        bootstrap = services[name]  # type: ignore[index]
        assert bootstrap["profiles"] == ["ledger-bootstrap"]
        assert bootstrap["command"] == command
        assert bootstrap["restart"] == "no"
        assert bootstrap["read_only"] is True
        assert "ports" not in bootstrap
        assert bootstrap["environment"]["AUTPLAY_VAULT_TOOL_MAX_OUTPUT_BYTES"] == "262144"
        assert bootstrap["depends_on"]["migrate"]["condition"] == ("service_completed_successfully")
        assert {secret["source"] for secret in bootstrap["secrets"]} == {
            "autplay-database-url",
            "autplay-privacy-ledger-key",
            "autplay-training-consent-ledger-key",
        }
        mounts = {mount["target"]: mount for mount in bootstrap["volumes"]}
        assert mounts["/var/lib/autplay/privacy-ledger"]["source"] == ("privacy-ledger-data")
        assert mounts["/var/lib/autplay/training-consent-ledger"]["source"] == (
            "training-consent-ledger-data"
        )
    for service in (api, mobile_api, worker, stream):
        assert service["environment"]["AUTPLAY_PRIVACY_LEDGER_PATH"] == (
            "/var/lib/autplay/privacy-ledger/deletion.sqlite3"
        )
        assert service["environment"]["AUTPLAY_PRIVACY_LEDGER_KEY_FILE"] == (
            "/run/secrets/autplay-privacy-ledger-key"
        )
        assert service["environment"]["AUTPLAY_PRIVACY_LEDGER_KEY_ID"] == "deletion-ledger-v1"
        assert service["environment"]["AUTPLAY_TRAINING_CONSENT_LEDGER_PATH"] == (
            "/var/lib/autplay/training-consent-ledger/consent.sqlite3"
        )
        assert service["environment"]["AUTPLAY_TRAINING_CONSENT_LEDGER_KEY_FILE"] == (
            "/run/secrets/autplay-training-consent-ledger-key"
        )
        assert service["environment"]["AUTPLAY_TRAINING_CONSENT_LEDGER_KEY_ID"] == (
            "consent-ledger-v1"
        )
        mounts = {mount["target"]: mount for mount in service["volumes"]}
        assert mounts["/var/lib/autplay/privacy-ledger"]["source"] == "privacy-ledger-data"
        assert mounts["/var/lib/autplay/training-consent-ledger"]["source"] == (
            "training-consent-ledger-data"
        )
        assert mounts["/var/lib/autplay/privacy-ledger"].get("read_only", False) is (
            service is stream
        )
        assert mounts["/var/lib/autplay/training-consent-ledger"].get("read_only", False) is (
            service is stream
        )
    assert worker["environment"]["AUTPLAY_VAULT_TOOL_MAX_OUTPUT_BYTES"] == "262144"
    assert config["volumes"]["postgres-data"]["labels"]["app.autplay.purpose"] == (  # type: ignore[index]
        "persistent-production-postgresql"
    )
    assert config["volumes"]["vault-data"]["labels"]["app.autplay.purpose"] == (  # type: ignore[index]
        "persistent-production-vault"
    )
    assert config["volumes"]["privacy-ledger-data"]["labels"]["app.autplay.purpose"] == (  # type: ignore[index]
        "persistent-production-deletion-ledger"
    )
    assert (
        config["volumes"]["training-consent-ledger-data"]["labels"][  # type: ignore[index]
            "app.autplay.purpose"
        ]
        == "persistent-production-training-consent-ledger"
    )


def test_caddyfile_blocks_private_and_wave_routes_and_uses_tls_alpn_only() -> None:
    caddyfile = (REPOSITORY_ROOT / "deploy/compose/Caddyfile.public-edge").read_text(
        encoding="utf-8"
    )

    assert "auto_https disable_redirects" in caddyfile
    assert "disable_http_challenge" in caddyfile
    assert "protocols h1 h2" in caddyfile
    assert "h3" not in caddyfile
    assert caddyfile.count("\troute {") == 2
    assert "api.autplay.win" in caddyfile
    assert "stream.autplay.win" in caddyfile
    assert "/admin* /health* /metrics /api/v1/wave*" in caddyfile
    assert "X-AutPlay-Client-IP {remote_host}" in caddyfile
    assert "X-Forwarded-For {remote_host}" in caddyfile
    assert "reverse_proxy mobile-api:8787" in caddyfile
    assert "reverse_proxy stream:8788" in caddyfile


def _run_caddy(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/config:size=8m,mode=0700",
            "--tmpfs",
            "/data:size=8m,mode=0700",
            "--env",
            "AUTPLAY_ACME_EMAIL=operator@example.test",
            "--volume",
            f"{REPOSITORY_ROOT / 'deploy/compose/Caddyfile.public-edge'}:/etc/caddy/Caddyfile:ro",
            CADDY_IMAGE,
            "caddy",
            command,
            "--config",
            "/etc/caddy/Caddyfile",
            "--adapter",
            "caddyfile",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _site_group_routes(config: dict[str, Any], host: str) -> list[dict[str, Any]]:
    routes = config["apps"]["http"]["servers"]["srv0"]["routes"]
    site = next(route for route in routes if route["match"][0].get("host") == [host])
    site_handlers = site["handle"][0]["routes"][0]["handle"]
    ordered = next(handler for handler in site_handlers if handler["handler"] == "subroute")
    return cast(list[dict[str, Any]], ordered["routes"])


def _contains(value: object, *, key: str, expected: object) -> bool:
    if isinstance(value, dict):
        if value.get(key) == expected:
            return True
        return any(_contains(item, key=key, expected=expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains(item, key=key, expected=expected) for item in value)
    return False


def test_caddy_adapt_preserves_private_route_before_api_proxy() -> None:
    result = _run_caddy("adapt")
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)

    api_routes = _site_group_routes(config, "api.autplay.win")
    assert api_routes[0]["match"][0]["path"] == [
        "/admin*",
        "/health*",
        "/metrics",
        "/api/v1/wave*",
    ]
    assert _contains(api_routes[0], key="status_code", expected=404)
    assert api_routes[1]["match"][0]["path"] == ["/api/v1/*"]
    assert _contains(api_routes[1], key="dial", expected="mobile-api:8787")
    assert "match" not in api_routes[2]
    assert _contains(api_routes[2], key="status_code", expected=404)

    stream_routes = _site_group_routes(config, "stream.autplay.win")
    assert stream_routes[0]["match"][0]["path"] == ["/api/v1/stream/*"]
    assert _contains(stream_routes[0], key="dial", expected="stream:8788")
    assert "match" not in stream_routes[1]
    assert _contains(stream_routes[1], key="status_code", expected=404)


def test_caddyfile_validates_with_the_pinned_official_image() -> None:
    result = _run_caddy("validate")

    assert result.returncode == 0, result.stderr
