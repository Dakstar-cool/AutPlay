"""Isolated GPU process boundary and safe accelerator diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

import uvicorn
from autplay.adapters.postgresql.readiness import PostgreSQLReadinessProbe
from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.application.enrichment import AcceleratorOutOfMemory
from autplay.runtime.logging import configure_json_logging
from autplay.runtime.settings import SettingsLoadError, load_worker_settings
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .devices import (
    AcceleratorProbeError,
    AcceleratorUnavailable,
    DeviceRequirements,
    NvidiaSmiInventory,
)
from .embedding import ModelArtifactError
from .settings import load_gpu_settings
from .sona_http import create_sona_shadow_app
from .sona_worker import SonaWorkerCompositionError, compose_sona_shadow_worker
from .worker import GpuWorkerCompositionError, compose_gpu_worker, run_gpu_worker

SERVICE_NAME = "autplay-ml-gpu"


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate isolation/readiness and expose bounded device inventory diagnostics."""

    parser = argparse.ArgumentParser(prog=SERVICE_NAME)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-config", action="store_true")
    mode.add_argument("--list-devices", action="store_true")
    mode.add_argument("--select-device", action="store_true")
    mode.add_argument("--check-readiness", action="store_true")
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--serve-sona-shadow", action="store_true")
    namespace = parser.parse_args(arguments)
    try:
        gpu = load_gpu_settings()
    except ValueError:
        return _error("gpu_config_invalid", 2)
    if namespace.check_config:
        return _json({"service": SERVICE_NAME, "status": "ok", "selector": gpu.device_selector})

    inventory = NvidiaSmiInventory(
        requirements=DeviceRequirements(
            minimum_total_memory_mib=gpu.minimum_total_memory_mib,
            minimum_compute_capability=(gpu.minimum_compute_major, gpu.minimum_compute_minor),
        )
    )
    try:
        if namespace.list_devices:
            return _json(
                {
                    "devices": [_device_document(device) for device in inventory.list_devices()],
                    "service": SERVICE_NAME,
                    "status": "ok",
                }
            )
        selection = inventory.select(gpu.device_selector)
    except AcceleratorUnavailable, AcceleratorProbeError, ValueError:
        return _error("gpu_accelerator_unavailable", 3)

    if namespace.select_device:
        return _json(
            {
                "device": _device_document(selection.device),
                "reason": selection.reason,
                "selector": selection.selector,
                "service": SERVICE_NAME,
                "status": "selected",
            }
        )

    if namespace.serve_sona_shadow:
        configure_json_logging(service=SERVICE_NAME, level="INFO")
        try:
            sona = compose_sona_shadow_worker(gpu, selection)
        except SonaWorkerCompositionError as error:
            return _error(error.code, 4)
        except ModelArtifactError:
            return _error("gpu_sona_artifact_invalid", 4)
        except AcceleratorOutOfMemory:
            return _error("gpu_accelerator_out_of_memory", 4)
        uvicorn.run(
            create_sona_shadow_app(
                sona.runtime,
                sona.artifact,
                max_admitted_inferences=gpu.sona_max_admitted_inferences,
                inference_timeout_seconds=gpu.sona_inference_timeout_seconds,
            ),
            host="127.0.0.1",
            port=gpu.sona_bind_port,
            workers=1,
            access_log=False,
        )
        return 0

    try:
        worker_settings = load_worker_settings()
    except SettingsLoadError:
        return _error("gpu_database_config_invalid", 2)
    configure_json_logging(service=SERVICE_NAME, level=worker_settings.log_level)
    engine = create_runtime_engine(worker_settings)
    try:
        readiness = PostgreSQLReadinessProbe(engine).check()
        if not readiness.ready:
            return _error(readiness.code or "gpu_database_unavailable", 3)
        if namespace.check_readiness:
            return _json(
                {
                    "device": _device_document(selection.device),
                    "reason": selection.reason,
                    "selector": selection.selector,
                    "service": SERVICE_NAME,
                    "status": "infrastructure_ready",
                }
            )
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        try:
            worker = compose_gpu_worker(
                sessions=sessions,
                selection=selection,
                gpu=gpu,
                runtime=worker_settings,
            )
        except GpuWorkerCompositionError as error:
            return _error(error.code, 4)
        except ModelArtifactError:
            return _error("gpu_model_artifact_invalid", 4)
        except AcceleratorOutOfMemory:
            return _error("gpu_accelerator_out_of_memory", 4)
        try:
            if namespace.once:
                tick = worker.run_once()
                return _json(
                    {
                        "outcome": tick.outcome.value,
                        "recovered_count": tick.recovered_count,
                        "service": SERVICE_NAME,
                        "status": "worker_tick",
                    }
                )
            run_gpu_worker(worker)
        except SQLAlchemyError:
            return _error("gpu_database_unavailable", 3)
    finally:
        engine.dispose()
    return 0


def _device_document(device: object) -> dict[str, object]:
    from autplay.domain.enrichment import AcceleratorDevice

    if not isinstance(device, AcceleratorDevice):
        raise TypeError("device inventory returned an invalid value")
    return {
        "compute_capability": device.compute_capability,
        "driver_version": device.driver_version,
        "free_memory_mib": device.free_memory_mib,
        "index": device.index,
        "name": device.name,
        "pci_bus_id": device.pci_bus_id,
        "total_memory_mib": device.total_memory_mib,
        "uuid": device.device_uuid,
        "vendor": device.vendor,
    }


def _json(document: dict[str, object]) -> int:
    sys.stdout.write(json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n")
    return 0


def _error(code: str, exit_code: int) -> int:
    sys.stderr.write(
        json.dumps({"event": code, "service": SERVICE_NAME}, separators=(",", ":")) + "\n"
    )
    return exit_code


__all__ = ("SERVICE_NAME", "main")
