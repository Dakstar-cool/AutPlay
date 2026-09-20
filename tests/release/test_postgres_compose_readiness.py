import json
import subprocess
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_postgres_healthcheck_waits_for_the_final_tcp_server() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "deploy/compose/compose.yaml",
            "-f",
            "deploy/compose/compose.test.yaml",
            "config",
            "--format",
            "json",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    config = cast(dict[str, Any], json.loads(result.stdout))

    assert config["services"]["postgres"]["healthcheck"]["test"] == [
        "CMD-SHELL",
        "pg_isready -h 127.0.0.1 -U autplay -d autplay",
    ]
