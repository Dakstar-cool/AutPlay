# ADR-027: P14 conditional reachability and GPU evidence deferral

- Status: Accepted under explicit user authorization
- Date: 2026-08-17
- Phase: P14

## Context

P00-D005 requires P12 and P13 to be green or explicitly deferred before P14. P13 is green inside
its trusted-local, single-API-process boundary. P12 delivered A-029 and A-031, but the development
host has no NVIDIA accelerator and no reviewed model artifact. It therefore cannot produce the
real CUDA OOM, throughput, p95 job time, peak-VRAM or quality-delta evidence required by A-030.

The user explicitly authorized Codex on 2026-08-17 to approve in-scope corrections and
improvements autonomously and requested completion without routine approval round-trips. This is
an explicit approval for the already designed P00-D005 release-governance choice; it is not
evidence that the missing hardware experiment ran.

## Decision

1. P00-D005 is accepted. P14 may execute after P11 when every skipped optional P12/P13 capability
   is explicitly named in the matrix, release notes and handoff.
2. A-030 and only the unavailable real-accelerator/model portion of P12 are
   `DEFERRED_WITH_APPROVAL`. A-029 and A-031 retain their executable PASS evidence.
3. No model is approved or activated. The deterministic P11 CPU baseline is the only RC
   recommendation path. The default/runtime-only Compose service set excludes `ml-gpu`.
4. A future GPU candidate must resume the exact P12 gate on a selected compatible NVIDIA host with
   a reviewed hash-addressed artifact. It must create a new immutable benchmark report and rerun
   the CPU independence, vulnerability/SBOM and rollout/rollback checks before activation.
5. This decision does not defer Samsung A55 qualification, mandatory P14 security/data-loss gates,
   external deployment decisions, production signing, or publication.

## Consequences

- P14 can harden the CPU/local-first release path without fabricating unavailable accelerator
  metrics or making GPU availability a core dependency.
- RC documentation must visibly state that GPU enrichment is experimental and excluded.
- Missing physical-device or end-to-end P14 evidence remains a release blocker; this ADR cannot be
  reused as a blanket deferral.

## Evidence

- `docs/implementation/HANDOFF_P12.md`
- `docs/implementation/evidence/P12_GPU_HARDWARE_PROBE_2026-08-17.json`
- `docs/implementation/HANDOFF_P13.md`
- `docs/build-pack/MVP_ACCEPTANCE_MATRIX.md`

