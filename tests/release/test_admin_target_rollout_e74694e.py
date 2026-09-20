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
