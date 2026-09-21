from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_rollout():
    path = Path(__file__).resolve().parents[2] / "scripts" / "admin_target_rollout_e74694e.py"
    spec = importlib.util.spec_from_file_location("admin_target_rollout_e74694e", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inspected_specs_with_null_binds_transform_into_replacements() -> None:
    rollout = _load_rollout()
    records = {
        name: {
            "Id": "a" * 64,
            "Name": f"/{name}",
            "Config": {
                "Cmd": ["autplay-api"],
                "Env": [],
                "ExposedPorts": None,
                "Healthcheck": None,
                "Image": rollout.OLD_IMAGE,
                "Labels": {},
            },
            "HostConfig": {
                "Binds": None,
                "NetworkMode": rollout.NETWORK,
                "PortBindings": None,
                "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            },
            "NetworkSettings": {
                "Networks": {
                    rollout.NETWORK: {
                        "Aliases": ["a" * 64, "a" * 12, name],
                    }
                }
            },
        }
        for name in rollout.SERVER_CONTAINERS
    }
    for name in rollout.SERVER_CONTAINERS:
        replacement = rollout.replacement(records[name])
        assert replacement["Image"] == rollout.IMAGE
        assert replacement["NetworkingConfig"]["EndpointsConfig"]
        assert replacement["HostConfig"]["Binds"]
        assert "AUTPLAY_PRIVACY_LEDGER_PATH" in rollout.env_map(replacement)

    migration = rollout.helper_spec(
        records["autplay-production-operator"],
        ["alembic", "-c", "/opt/autplay/alembic.ini", "upgrade", "head"],
        include_private_state=False,
    )
    binds = migration["HostConfig"].get("Binds") or []
    assert all("privacy-ledger" not in bind for bind in binds)
    assert all("training-consent-ledger" not in bind for bind in binds)


def test_private_storage_uses_utf8_safe_independent_keys(monkeypatch) -> None:
    rollout = _load_rollout()
    input_programs: list[str] = []

    def fake_run(*arguments: str, input_text: str | None = None):
        del arguments
        if input_text is not None:
            input_programs.append(input_text)

    monkeypatch.setattr(rollout, "run", fake_run)
    monkeypatch.setattr(rollout, "docker", lambda *arguments: "")

    rollout.prepare_private_storage()

    assert len(input_programs) == 1
    compile(input_programs[0], "ledger-key-program", "exec")
    assert "secrets.token_urlsafe(48).encode('ascii')" in input_programs[0]
    assert "os.urandom" not in input_programs[0]


def test_processing_pause_covers_every_background_track_processor() -> None:
    rollout = _load_rollout()

    assert {
        "autplay-production-worker-cpu-1",
        "autplay-music-worker",
        "autplay-metadata-worker",
        "autplay-acquisition-vault-bridge",
    } == rollout.PROCESSING_CONTAINERS
    assert set(
        (*rollout.START_ORDER, *rollout.AUXILIARY_RESTART)
    ) >= rollout.PROCESSING_CONTAINERS
    assert not rollout.PROCESSING_CONTAINERS & set(rollout.HEALTH_REQUIRED)


def test_paused_rollout_has_no_music_proxy_precondition() -> None:
    rollout = _load_rollout()

    assert rollout.expected_running_containers(leave_processing_stopped=True) == {
        "open-webui"
    }
    assert rollout.expected_running_containers(leave_processing_stopped=False) == {
        "autplay-music-proxy",
        "open-webui",
    }
