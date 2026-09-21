"""Trusted local review and live limit changes for internal byte-work capacity."""

import argparse
import json
from pathlib import Path
from typing import Protocol, TextIO
from uuid import UUID

from autplay.domain.internal_io import InitializeInternalIoBudget, InternalIoMeasurement
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_measurements import MAX_MEASUREMENT_BYTES


class InternalIoBudget(Protocol):
    def initialize(self, command: InitializeInternalIoBudget) -> dict[str, object]: ...
    def set_limit(
        self, operation: UUID, expected_revision: int, limit: int
    ) -> dict[str, object]: ...


def configure_internal_io_parser(parser: argparse.ArgumentParser, *, measurement: bool) -> None:
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--limit", type=int, required=True)
    if measurement:
        parser.add_argument("--report", required=True)
        parser.add_argument("--reviewed-report-sha256", required=True)
        parser.add_argument("--expected-environment-sha256", required=True)
        parser.add_argument("--ceiling", type=int, required=True)


def run_internal_io_budget(
    repository: InternalIoBudget, arguments: argparse.Namespace, *, stdout: TextIO, stderr: TextIO
) -> int:
    try:
        operation = UUID(arguments.operation_id)
        if arguments.command == "internal-io-budget-apply":
            with Path(arguments.report).open("rb") as stream:
                report = InternalIoMeasurement.parse(stream.read(MAX_MEASUREMENT_BYTES + 1))
            result = repository.initialize(
                InitializeInternalIoBudget(
                    operation,
                    arguments.expected_revision,
                    report,
                    arguments.reviewed_report_sha256,
                    arguments.expected_environment_sha256,
                    arguments.limit,
                    arguments.ceiling,
                )
            )
        else:
            result = repository.set_limit(operation, arguments.expected_revision, arguments.limit)
    except OSError:
        code = "internal_io_measurement_file_unavailable"
    except ValueError:
        code = "internal_io_measurement_invalid"
    except ResourceAdmissionError as error:
        code = error.code
    else:
        stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
        return 0
    stderr.write(json.dumps({"error": {"code": code}}, separators=(",", ":")) + "\n")
    return 2
