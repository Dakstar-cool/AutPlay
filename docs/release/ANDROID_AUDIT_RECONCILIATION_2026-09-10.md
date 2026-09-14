# Android audit reconciliation and implementation - 2026-09-10

The user selected Android/audit, then AutPlay Face, then PA3. A1-A8 now have current local
implementation and targeted runtime evidence. This does not qualify a public release or replace
physical-device and hosted-CI gates.

## Current implementation evidence

The fresh sequential gate passed JVM tests, lint, debug build and connected tests:
**270 JVM tests, zero failures/errors/skips; 212 connected tests, zero failures/errors, three skips**.
The API 26 disposable AVD is `autplay_audit_clean_20260910`. XML totals, not the instrumentation
console's transient progress counter, are authoritative. Source and APK hashes are recorded in
[implementation evidence](evidence/android-audit-2026-09-10/implementation/IMPLEMENTATION.json).
That source manifest binds the Android audit snapshot before subsequent pure Face contract additions.

The portable process-death harness separately passed both process stages using the same installed
APKs/data and a verified force-stop boundary. It checks previous-track duration/completion, queue
identity, position, playback modes and restoration. The debug APK SHA-256 is
`58eac2b8194eae1b3bc782fb6ec9bee543c544f5554d69ecc5c4b8a3e5157e41`.
The test APK SHA-256 is `a2cc41bf6eb0b4b1dc1559cae24e6d8c4434a3a2d20bc1d618d215210775e083`.
The [receipt](evidence/android-audit-2026-09-10/implementation/process-receipt.json) and stage outputs
are retained beside normalized connected/JVM results and the source manifest.

| ID | Implemented behavior and verified acceptance |
| --- | --- |
| A1 | Real upload/import WorkManager workers use Room/Keystore M5 credentials; fresh access, one rotation, terminal/repeated unauthorized, inactive profile and profile replacement before refresh pass. HTTP 503 replays the identical pending rotation request and recovers without requiring login. |
| A2 | Real SyncWorker socket failure retries through WorkManager and ACKs the original event hash. Cancellable HTTP cancels both pending headers and response-body reads; cancellation releases only its own lease without spending retry budget. |
| A3 | A real 101-event backlog ACKs in batches of 100+1 and compacts correctly. History writes automatically enqueue work during an active run and after completion; durable enqueue is awaited. |
| A4 | Service destruction remains responsive while the real Room writer is held. Fresh observed time survives cancellation; shutdown persistence is ordered, checked against the current/finalized session, and awaited before a successor service restores. A held-writer reconnect and separate process-death stages pass. |
| A5 | Current local Media3 playback starts while remote next-source preflight is held. Queue replacement cancels the stale lookup and rejects its late result. Pending selected sources prepare when ready; terminal unavailable sources stop explicitly. |
| A6 | A real slow ContentProvider exercises Main heartbeat, CancellationSignal, bounded probe timeout and cleanup on service stop. A timed-out lookup does not falsely mark a source missing. |
| A7 | Real transitions from a 40-second source to a 2-second source preserve the previous duration and completion ratio. Queue refresh preserves current listening identity and shuffle/repeat; automatic history scheduling passes independently. |
| A8 | Real Wi-Fi/cellular transitions plus a setting change cancel the active lease, preserve attempts and enqueue UNMETERED work. It waits on cellular and resumes/ACKs on Wi-Fi. Request guards reread current policy and profile. |

Bounded read-only review approved the final Android audit changes after corrections. The ordinary
suite still skips the two separately orchestrated process stages and one M5 pairing E2E test.
The pairing E2E is not claimed here. CI now invokes the portable process harness after the ordinary
suite and retains both reports; no hosted run or required-check configuration has been verified.

## Retained earlier audit components

The earlier recovered run (192 connected tests, 265 JVM tests) remains in
[RECONCILIATION.json](evidence/android-audit-2026-09-10/RECONCILIATION.json); it is historical evidence,
not proof for later code. Its measured local FTS/10,000 rows p95 was **11.5697 ms**, and playlist/1,000
rows p95 was **11.0831 ms**, both below 150 ms. These are database query timings, not Face frame,
battery or physical-device measurements.

Fresh local CycloneDX/OSV, source secret scan, dependency and license inventories covered root,
server, GPU, SONA training, acquisition and Android as applicable: zero vulnerabilities/adverse
statuses and zero unresolved license metadata. Publication obligations remain separate from
inventory success. Historical P14 records and August APK/device receipts were not overwritten.

## Remaining qualification and Git scope

Q3 needs hosted clean runs and required-check inspection. Q4 needs the intended final release
artifact/inventory and physical Samsung A55 qualification; only the disposable emulator is attached.
G2 target Linux/CUDA proof and R1B quality-source/evaluation remain open independently.
No public listener or deployment was changed. PA3 follows Face in the selected sequence.

Branch: `codex/pa3-linux-migration`, HEAD `f208ce6`, dirty worktree. Existing Android UI parity/Wave
changes were preserved. No commit, push, release publication or production data migration occurred.
The earlier documentation-only root validation was 168 tests; fresh checks are recorded with the
current implementation/Face handoff, rather than attributed to historical APKs.
