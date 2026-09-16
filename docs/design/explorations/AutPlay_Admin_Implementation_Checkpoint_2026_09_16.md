# Admin and account implementation checkpoint: 2026-09-16

This is an unfinished local implementation checkpoint for continuation in a fresh task.
The full accepted outcome remains open. It is not a release or production migration.

## Scope and decisions

Implement the complete `AutPlay_Admin_And_Account_Onboarding_Draft_v1.md` plan and ADR-053:
four Admin sections, passkeys, self-service QR pairing, one-time TXT/manual recovery,
complete authority revocation, reversible 30-day deletion, shared-training consent withdrawal,
editable account defaults 5/2/2, and measured global resource admission.

The user approved `AutPlay_Resource_IO_Stop_Amendment_v1.md`: stop application HTTP I/O
within the local five-second authorization window, but retain quota capacity and any upload
transaction until exact process exit is confirmed. A kill request or expired heartbeat is
not exit proof. Unknown crash executions remain charged until verified reconciliation.

## Implemented and checked locally

- Four responsive Admin section hubs, browser passkey registration/login/revocation,
  local CLI recovery, EN/RU presentation. Synthetic HTTPS/browser and PostgreSQL checks passed.
- Versioned self-device pairing protocol, server implementation, Android durable recipient/source
  state, QR/SAS approval, same-family rotation and local data materialization. Feature remains off.
- PostgreSQL resource admission, fair queues, editable account/device quotas, activation fencing,
  byte permits, exact target/authority checks, durable process execution registry (0035/0036).
- Retained Vault child processes, upload row/transaction retention, exact precommit checks,
  atomic permit/heartbeat renewal and optional upload/stream HTTP integration.
- Optional quota editor and HTTP admission API. Production byte/worker enforcement is unfinished.

Latest selected checks on the combined working directory:

| Checks | Result |
| --- | --- |
| Upload exclusion, exact precommit, actual process upload and adjacent Vault | 44 passed |
| Coordinator and real process/startup/response-loss failures | 16 passed |
| Stream/admission HTTP, headers and deadline runtime | 37 passed |
| ASGI normal completion/send backpressure (2.0 and 2.4) | 4 passed |
| Actual API upload/process plus adjacent Vault/body runtime | 21 passed |
| Upload HTTP test strict mypy, repeated before checkpoint | passed |

Earlier successful evidence includes schema migration roundtrips, policy and authority races,
root contracts, synthetic HTTPS layouts, Android selected unit tests/APK builds/lint and five
self-pairing UI cases on the owned API26 emulator. These are scoped checks, not full acceptance.
The laptop/M55/private-network acceptance and actual capacity measurements remain unverified.

## Start continuation with these open findings

1. `stream_http.py` awaits child file-open before the disconnect listener exists. ASGI 2.4 also
   has no receive listener while a next-block read is blocked. Use one raw receive owner across
   admission/open/stream response; upload needs it after body completion while the worker waits.
   Test actual disconnect during blocked open/read under both ASGI versions.
2. Admitted upload error responses bypass `IoScopedResponse`: `io.finish()` stops its guard,
   then ordinary API/middleware handlers can start an unbounded error send. Cover the whole
   admitted ASGI call, including error handlers, binding before the first admission RPC.
3. Preserve early bounded typed 422/503 errors only within an atomically frozen last-authorized
   monotonic expiry. Never create a new five-second budget or revive normal sends/receives.
   Disconnect/caller cancellation permanently aborts the scope. This is a reviewed proposal,
   not implemented code. Installed Uvicorn 0.51.0 sends its own fallback 500 after application
   failure/empty return; a FastAPI wrapper alone cannot prevent that. A narrow transport-abort
   integration and a real TCP test are needed before claiming no send after expiry.
4. Unexpected child EOF/protocol errors before stream headers escape stable storage-error
   mapping. Map these to the existing retryable 503 contract and test child exit before OPENED.
5. Check `readiness.py`: the working constant still names 0035 while schema head is 0036.

Both read-only reviews found the HTTP issues above; they are open. No fixes for these findings
were started before the user's request to checkpoint and move tasks.

## Remaining complete-plan work

- Dedicated admission control DB pool in production composition, all byte/worker enforcement,
  bounded maintenance and orphan observability; then quota editor/feature activation.
- Trusted local measured-budget report validation, initialization and audited exact replay.
  Do not invent measured throughput or physical-device evidence.
- Android durable playback/download/upload admission and worker acquisition/rebinding.
- TXT/manual recovery and complete authority revocation; reversible deletion and backup/purge
  protection. Existing non-ACTIVE social cleanup cannot be reused for pending deletion.
- Shared-training consent withdrawal. Existing signed dataset/artifact deletion policy has
  different semantics and must not be reinterpreted.
- Full acceptance, current review closure and actual target measurements/devices.

## Recovery and Git boundary

The saved project root is `D:/AutPlayProd`; the repository is its `AutPlay` child.
Continue in that same local directory. Branch at checkpoint preparation was
`codex/readme-current-state`, base commit `f48312544f8ccc8e0371a5cb69b453a582c2031b`.

The checkpoint commit contains only separable changes from this task. Mixed files stay
uncommitted, including `MainActivity.kt`, `api.py`, settings, model registration, dependencies
and aggregate migration expectations. Adjacent 0031/0032 music/metadata migrations also remain
outside this commit; 0033 depends on them. This commit alone is not an independently runnable
checkout. Do not cherry-pick it without reconciling these dependencies.

The ignored local directory `.codex-state/admin-implementation-20260916/` contains a verified
ZIP snapshot of all modified/untracked source files, a SHA-256 manifest, and the exact owned
checkpoint path list. The snapshot preserves adjacent changes without committing them.
Do not restore it blindly over newer working files.

Canonical bounded state: `.codex-state/admin-implementation-20260916.json`.
Read the applicable skills and validate the state guard before resuming; the final handoff
sets its sole writer to `admin-continuation-20260916`. Required full-plan criteria stay unknown.

The owned disposable PostgreSQL Compose project is `autplay-admin-20260916`; its latest
verified loopback port is 4142 after an external Docker restart. Recheck the owned container
before tests. Existing bounded probe scripts are in the local checkpoint directory.
Use locked Python dependencies and `python -m pytest/mypy/ruff`; Gradle runs sequentially
with one worker and the standard 2 GiB heap. The owned emulator is `emulator-5584`.
The connected M52 is not an authorized replacement for M55 acceptance.

No push, deployment, production migration, real credential registration, or adjacent-task
commit is authorized by this checkpoint request. New feature work resumes only in the new task.
