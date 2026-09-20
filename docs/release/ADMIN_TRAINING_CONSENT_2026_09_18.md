# Shared-training account policy and Android controls: 2026-09-18

The current account decision and Android controls are implemented and independently
reviewed. This is a verified slice of ADR-053(7), not end-to-end shared-training or
full-goal acceptance. See [the contract](../design/AutPlay_Training_Consent_Contract_v1.md).

## Result

Participation defaults off. Refusal/withdrawal persist for the account and are read
by its other devices. Revision-checked grant, privacy-preserving stale withdrawal,
exact receipts and current-policy replay prevent old requests restoring permission.
Current ACTIVE account/device/session authority is rechecked under PostgreSQL locks.
Final purge removes policy/receipt rows; evidence prevents destructive downgrade.

Android has RU/EN equal opt-in/refusal, explicit withdrawal, unknown-result retry and
model-weight limitation text. The encrypted journal is scoped to server/account/origin.
Rebinding does not automatically replay another binding's grant. Explicit withdrawal
can replace it while offline. Delayed responses/failures cannot overwrite a newer
decision or erase its journal. Signed capability is opt-in for every role; it defaults
off, and production settings reject enablement until execution/cleanup are verified.

Head verified for this slice: **0054_training_consent**, **164 tables, 1854 columns, 154 explicit
indexes**; mapping fingerprint
`31a3cfeced8dc50b26b6ae787f4df37026525f78e58a3d164b10ae103a1b364a`.
The subsequent [registry foundation record](ADMIN_TRAINING_REGISTRY_2026_09_18.md)
owns current head 0055 and its terminal-revision/invalidation checks.

## Verification

- **35 PostgreSQL passes** in the policy/migration/close-gate batch, 72.87 s. It covers
  13 policy cases, clean upgrade/down/reupgrade, all 54 adjacent cycles, live drift,
  evidence retention and PUBLIC guards. Policy cases include grant/withdrawal races,
  exact/changed replay, rollback, actor revocation, final purge, stale REPEATABLE READ
  serialization and downgrade waiting on a concurrent decision writer.
- **57 runtime/HTTP/settings passes**, 2.23 s; **six capability role/flag passes**,
  14.29 s. Strict/no-store/error/bounds/feature-default/production-veto behavior is covered.
  Unchanged earlier metadata evidence is reused; the final contract check passed once.
- Ruff/format passed on 15 affected Python files; strict mypy passed on eight
  implementation/test files. Root wire-contract validation: **one pass**, 1.87 s.
- **46 Android JVM passes** in the final affected batch: 13 consent runtime, one
  consent transport, 27 pairing runtime and five credential-store cases. This includes
  lost replies, stale grant rejection, account isolation, offline rebinding, delayed
  success/error/read races, storage failures, cancellation and reserved-slot protection.
- App and test APK built with strict dependency verification and one worker; **three
  emulator UI cases passed**, 6.476 s. All three synthetic RU/EN dark screenshots were
  inspected; these test the card, not a live account/server or full Settings layout.
- Separate final lint after terminal-revision/card changes passed:
  **0 errors, 0 warnings, 1 pre-existing hint**, 4m47s.
- Bounded independent server and Android reviews are closed; reviewers did not execute
  tests. The implementing agent ran the checks and fixed the reported lock/CAS races.

Ignored local evidence: `apps/android/build/consent-verification/unit/`,
`instrumentation.txt`, `screenshots/`. UI checks used only the owned read-only API-26
emulator `emulator-5556`, then shut it down. Physical M52 was untouched; A55 acceptance
remains open. PostgreSQL cases used isolated disposable test databases on loopback
1520, not production. No commit/push/deploy/production migration occurred.

## Remaining required block

Migration 0054 does **not** fence existing offline extraction/training/export/serving.
The next block needs a composed preparation command with separate historical 0026
and current-authority connections, verified complete participant lineage, terminal
run invalidation, bounded process cancellation and training-root cleanup. Publication
must be atomic PostgreSQL authority for exact candidate hashes, not an earlier callback
or local COMMITTED marker. Published model dependencies must survive ordinary withdrawal.
Restore reconciliation must prevent a pre-withdrawal backup restoring consent/run
authority. Full physical/browser/private-network and joint measurement gates also remain.
