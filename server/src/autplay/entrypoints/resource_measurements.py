"""Trusted local report import; no report paths or arbitrary text enter audit/logs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TextIO
from uuid import UUID

from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_measurements import (
    MAX_MEASUREMENT_BYTES,
    InitializeResourceBudget,
    ResourceMeasurement,
)
from autplay.domain.resource_policy import GlobalResourceLimits
from autplay.ports.resource_measurements import ResourceBudgetInitializer


def configure_resource_budget_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report", required=True)
    parser.add_argument("--reviewed-report-sha256", required=True)
    parser.add_argument("--expected-environment-sha256", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--playbacks", type=int, required=True)
    parser.add_argument("--transfers", type=int, required=True)
    parser.add_argument("--playback-ceiling", type=int, required=True)
    parser.add_argument("--transfer-ceiling", type=int, required=True)


def run_resource_budget_initialize(
    initializer: ResourceBudgetInitializer,
    arguments: argparse.Namespace,
    *,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    try:
        with Path(arguments.report).open("rb") as stream:
            payload = stream.read(MAX_MEASUREMENT_BYTES + 1)
        command = InitializeResourceBudget(
            UUID(arguments.operation_id),
            arguments.expected_revision,
            ResourceMeasurement.parse(payload),
            arguments.reviewed_report_sha256,
            arguments.expected_environment_sha256,
            GlobalResourceLimits(arguments.playbacks, arguments.transfers),
            GlobalResourceLimits(arguments.playback_ceiling, arguments.transfer_ceiling),
        )
        result = initializer.initialize(command)
    except OSError:
        code = "resource_measurement_file_unavailable"
    except ValueError:
        code = "resource_measurement_invalid"
    except ResourceAdmissionError as error:
        code = error.code
    else:
        stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")) + "\n")
        return 0
    stderr.write(json.dumps({"error": {"code": code}}, separators=(",", ":")) + "\n")
    return 2
