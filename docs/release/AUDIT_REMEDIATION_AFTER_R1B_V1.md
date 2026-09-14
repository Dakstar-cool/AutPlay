# AutPlay audit remediation after R1B — review pack v1

This review pack binds the remediation work to the independent audit report SHA-256
`97cc85280d83cfd3426d681d47bdb123e048c9e617227a2c39c1ad7f563c4550` and evidence archive
SHA-256 `b1b05d9ab2c7a93d0fbcd9cfd5e31fc7dfff24d1474cbebeed4f51d53ab4b701`.
The complete machine-readable status is in
[`AUDIT_REMEDIATION_AFTER_R1B_V1.json`](AUDIT_REMEDIATION_AFTER_R1B_V1.json).

The pre-remediation worktree was preserved before edits as commit
`3b45a311cbd123fcc90d9c8afa0cd5d93ce5e57f` and pushed to
`origin/codex/pa3-linux-migration`. Local recovery environments, caches and `.codex-state` are not
release evidence and are not included in Git.

## Outcome boundary

Server S1-S5, retention G1, acquisition T1-T2 and reproducibility Q1/Q2/Q5/Q6 have local
regressions or deterministic static evidence. The fresh 2026-09-10 Android implementation gate
passed 270 JVM tests, 212 API 26 connected tests (zero failures, three ordinary-suite skips),
lint and debug build. Both separately orchestrated process-death stages also passed on the same
installed APK/data with a verified force-stop boundary. A1-A8 now have real worker/lifecycle
acceptance, mapped in the [dated reconciliation](ANDROID_AUDIT_RECONCILIATION_2026-09-10.md) and
bound to source/APK hashes in its implementation manifest. Q3 still requires hosted clean CI and
required-check inspection. Q4 still requires final release inventory and physical Samsung A55
qualification; historical August APK/device receipts are not reused for the current snapshot.

G2 and the SONA execution policy are implemented in locks, configuration and code, including
fail-closed CPU fallback, bounded admission/deadline/cancellation, an eager/ORT export matrix and an
untimed ORT node-placement trace. They do not constitute real Linux/CUDA hardware evidence. R1B
remains `BLOCKED` because its final evaluation lacks canonical causally joined outcomes, complete
eligible candidate sets and approved embeddings. R1C remains inactive.

## Compatibility and rollback

- Migration `0030` is forward-only and narrows retention behavior without rewriting applied
  migration `0028`. Roll back application activation before considering a schema downgrade; no
  destructive production reset is authorized.
- Sync changes preserve protocol v1 and terminal ACK semantics. Previously affected projections
  require diagnosis and data-preserving bootstrap/reconciliation; this work does not assert that
  production damage exists.
- Android scheduler/playback changes preserve Room data and queue identity. Reverting code does not
  require deleting the journal or Room database.
- Acquisition publishes exclusively and removes incomplete temporary files. Provider failures do
  not authorize overwriting an existing audio file.
- SONA remains shadow-only and fail-closed. Reverting its runtime changes requires no model or data
  migration because no activation is claimed.

## Canonical verification

The root `scripts/check.ps1` / `scripts/check.sh` full gate now includes all five Python projects,
strict Gradle dependency verification, Android lint/unit/debug/trusted-LAN/release artifacts and
the disposable PostgreSQL suite. The server-only variant remains the CPU/root boundary. Android
connected and target Linux/CUDA gates are separate and must remain explicitly `NOT RUN` when their
required device is unavailable.

The complete Windows host gate passed on 2026-09-09: root `168 passed`; GPU runtime `33 passed,
2 skipped` for unavailable Windows symlink creation; SONA training `37 passed`; acquisition
`66 passed`; Android `144 actionable tasks` across lint, JVM tests and Debug/TrustedLan/Release
assemblies; server `878 passed, 1 skipped` against disposable PostgreSQL 18.4 with pgvector 0.8.6.
The PostgreSQL container, volume and network were removed by the gate.

Gradle dependency verification also passed `assembleDebug` from a new isolated cache. Fresh
vulnerability audits report zero vulnerabilities and zero adverse statuses in all five Python
graphs after updating `httpx2` and `httpcore2` to 2.12.0. The 2026-09-10 reconciliation extracted
current performance measurements from the connected log: FTS p95 11.5697 ms and playlist p95
11.0831 ms, both below the 150 ms threshold. Fresh local audit components are retained under
`docs/release/evidence/android-audit-2026-09-10/`. The full P14 generator was not rerun with
historical physical-device receipts; Q4 remains open on its final build/device/inventory obligations.
