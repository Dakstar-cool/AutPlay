# GPU admission authority v1

This contract implements the shared admission boundary in Revision 15 sections 5.1, 7.1,
and 11. The `0062` schema is inert until a GPU process uses the admission interface. The
stable device identity is the NVIDIA GPU UUID stored as a PostgreSQL `uuid`, independent of
the mutable CUDA ordinal. A trusted local inventory provisions one reviewed device row.

## Current and history

| Relation | Meaning |
| --- | --- |
| `ml.gpu_device_authority` | Reviewed total VRAM, safety margin, future Sona reservation, device-wide event generation, Face cancellation generation. |
| `ml.gpu_reservation_current` | One latest reservation per `(device_uuid, FACE/SONA)`; its holder and reservation generation are fenced. Terminal rows remain until the next acquire. |
| `ml.gpu_admission_receipt` | Immutable event history. A receipt with the next device generation is the sole mutation path for the current projection. |

The reviewed safety margin is at least 1 GiB and at least 10% of reviewed total VRAM.
The future Sona budget and margin must fit below total VRAM. Face requests fit within
`total - margin - reserved_sona`; Sona requests fit within `reserved_sona`. When Sona is
resident, Face additionally fits the measured residual, checked again against NVML before
CUDA work. Sona acquisition while Face has an active or cancelled reservation fails until
Face unload or process exit and an NVML release measurement are recorded.

## Event protocol

The caller appends one receipt with `authority_generation = current + 1`. The database
locks the device row, checks the current holder and reservation generation, and applies
the event atomically. Concurrent attempts with the same generation conflict. A new
`ACQUIRED` event increments the reservation generation, even after a prior holder has
released. `HEARTBEAT` keeps the same holder/generation and grants a lease no longer than
30 seconds. `CANCEL_FACE` increments the Face cancellation generation and leaves the
reservation occupied. `RELEASED` or `EXPIRED` requires an NVML release confirmation and
either session unload or process exit. Expiry alone never makes the slot reusable.

The process checks its holder, reservation generation, Face cancellation generation,
lease deadline, and database connectivity before each bounded batch or Sona inference.
Failure stops new GPU work; Face unloads or exits after the current bounded batch and
Sona becomes unready. The database does not assert driver-level hard preemption.

## Reviewable limits

- No model loads, GPU process startup, or selection are enabled by `0062` alone.
- Admission budgets are advisory. The process must compare NVML process-used and device-free
  VRAM before marking a CUDA session ready.
- `CANCEL_FACE` has no release effect. The caller waits up to 30 seconds and observes
  an actual `RELEASED`/`EXPIRED` receipt before Sona load; otherwise Sona stays unready.
- Device inventory changes, process adapters, and NVML measurements require separate
  reviewed implementation and tests before GPU activation.

## Unwired NVML boundary

`NvmlGpuMemoryProbe` resolves the exact `GPU-<uuid>` through NVML, reads device free/total
bytes and the exact compute PID's used bytes, and returns a timestamped sample. The
application memory gate checks the live database lease first. Before load it requires the
reviewed request plus safety margin to fit measured free memory; after load it requires
the PID to be present, its use to fit the reservation, and the margin to remain free.
It heartbeats the measured process bytes only after these checks. A release proof requires
the exact PID to disappear from the compute-process list and a fresh unload/exit report.
NVML errors, changing process lists, and unavailable per-process memory fail closed.

This adapter is not wired to a GPU process. The caller must supply the safety margin from
the reviewed device authority and the correct supervised PID. Synthetic tests cover the
boundary, but the target RTX 3060, process crash/restart and concurrent Face/Sona recovery
remain untested. [NVIDIA's NVML process-memory reference](https://docs.nvidia.com/deploy/nvml-api/api/group__nvmlDeviceQueries.html)
states that WDDM may report process memory as unavailable; this implementation rejects
that measurement rather than treating it as zero.
