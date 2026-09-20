# Android account recovery verification: 2026-09-18

This record supersedes the interrupted-build and open-review status in
`ADMIN_ANDROID_RECOVERY_HANDOFF_2026_09_18.md`. Local implementation continues on
the existing dirty checkout. No commit, push, production migration, rollout or
real credentials were used. The full seven-criterion Admin/account goal remains
unfinished.

## Implemented and reviewed

Both integration findings from the earlier handoff are closed. The four
self-pairing/recovery credential journal slots now share a reserved-ID policy;
registration, imported bindings, stale M5 cleanup and binding recovery cannot
overwrite or clear them. Regression checks cover all four slots and reject
registration before credential access or discovery.

An external recovery/self-pairing binding keeps its credential-first pair and
exact encrypted journal if the settings write commits and then throws. Real
adapter regressions cover I/O failure and cancellation, preserve the cancellation
instance, and complete locally after a 48-hour restart with HTTP forbidden.

New PA2 accounts atomically acquire a nonsecret recovery-setup checkpoint with
their binding. Exact binding replay preserves saved evidence. Existing accounts
are not silently backfilled. A pending new account opens Profile once per runtime
and binding, keeps an incomplete-setup notice until the exact document is saved,
and leaves local music navigation available.

TXT export captures an immutable account/binding/generation/document-hash ticket
before opening the system picker. It verifies that ticket before and after
external I/O, reads the saved file back, and only then records matching evidence.
Picker cancellation, process recreation, stale binding/code, write/read failure
and coroutine cancellation cannot acknowledge setup. External document I/O holds
neither the binding gate nor the journal gate. Secret buffers are wiped on failure,
stream-close failure and prompt coroutine cancellation. Saved URI, secret code and
document bytes do not enter nonsecret settings or saved UI state.

Recovery activity wiring was extracted into `MainAccountRecoveryUi.kt`. Bounded
independent read-only review closed the binding, first-presentation and secret
buffer ownership findings. That review is scoped to this block, not full feature
acceptance.

## Verification actually completed

- The affected JVM batch ran 83 cases: 82 passed and one new DataStore persistence
  case failed on Windows file replacement. The Android host SDK stub selected
  `File.renameTo`; the test now uses real Okio host atomic replacement with the
  real preferences serializer. No production code or assertion was weakened.
- Only the affected DataStore class was rerun: all four cases passed, no skips.
  Together with the unchanged initial results this is **83 distinct passing
  cases**, not 87. Coverage includes recovery protocol/export/runtime/UI policy,
  self-pairing recipient, shared binding, public registration, credential storage,
  settings persistence and secure-window policy.
- Final `assembleDebug` and `assembleDebugAndroidTest` passed in 3m 27s with the
  standard 2 GiB heap and one worker. Final application and test APKs were installed
  on the disposable read-only `autplay_admin_20260916` AVD, serial emulator-5554,
  Android API 26 / x86_64.
- `AccountRecoveryCardTest`: **6 passed in 8.631s**. Five synthetic EN/RU dark-mode
  screenshots were inspected: offline export, server confirmation, account and
  revocation confirmation, mandatory unfinished setup, and acknowledged save.
  Text and buttons were visible without clipping. These are component tests,
  not the complete live-server or system-document-picker flow.
- The initial combined build/lint invocation hit an internal Kotlin FIR lint
  analysis failure after memory pressure, not an application lint finding.
  A separate fresh `lintDebug` invocation on final sources **passed in 6m 9s**
  with the same standard heap: **0 errors, 0 warnings, 1 existing hint** in
  `M3VisualEvidenceActivity.kt`. No lint suppression, heap increase, tool upgrade
  or repeated broad unit batch was needed.
- Tracked `git diff --check` passed; existing line-ending warnings remain.

Local evidence is under `apps/android/build/recovery-verification/`:
`unit-initial/`, `unit-datastore-final/`,
`account-recovery-instrumentation.txt`, `screenshots/`, and `lint-debug.txt`.
These generated files are local build outputs. Tests used synthetic accounts and
secrets. The prior server/contract evidence is unchanged and was not rerun wholesale.
The owned disposable emulator was shut down after verification. No Gradle invocation
remains running.

## Subsequent lost-reply closure and remaining work

The later `/recovery/outcome` continuation closes the unknown recipient
`COMMIT_PENDING` case without a reset/discard shortcut. Android keeps the exact signed
request, fresh device key and client-created refresh secret; after ordinary receipt expiry
it proves that result secret and accepts only the still-current exact server binding. Real
PostgreSQL tests cover lost reply after cleanup, and Android transport/runtime tests cover
the fallback and encrypted-journal resolution. Changed, revoked or superseded results fail
closed. Real system-picker interruption and live server-to-Android evidence remain open.

Continue 30-day deletion pending/cancellation/purge with independent backup deletion
protection; revocable shared-training consent; outstanding worker/byte-path
composition; browser, physical Windows Hello/A55, private-network and joint
resource-measurement acceptance. No physical Android device was connected during
this verification. Passing this block does not complete those requirements.

## Next deletion block: read-only reconnaissance

No server implementation or migration was changed in this continuation; the head
remains `0051_account_recovery`. Bounded independent reconnaissance confirmed:

- Use a dedicated request lifecycle and purpose-bound cancellation, with database
  time and the existing identity -> admission -> sync owner -> account lock order.
  A deletion request must revoke authority atomically; cancellation proves the
  recovery code and a fresh device key and must not revive old credentials.
- `social.retire_account_state` from migration 0023 currently deletes durable
  friendship/block/settings rows for every non-ACTIVE status. Pending deletion
  needs preservation while temporary invitations/presence are retired.
- `public_access._lifecycle` currently treats every non-ACTIVE account as already
  terminal. Administrative disabling during deletion must record a real veto;
  cancellation cannot use it to restore ACTIVE access.
- Existing immutable recovery/worker/ingest records and RESTRICT foreign keys
  require an explicit owner-data inventory and narrowly authorized purge path.
  Do not implement generic recursive cascades or disable triggers. Retained
  execution capacity remains charged until exact process exit is verified.
- The recommendation contract defines a stable keyed owner tombstone
  `HMAC-SHA256("privacy-delete-v1", user_id)`, with completion/protection times as
  separate metadata. Do not reinterpret its compact policy label as concatenating
  timestamps into the owner identity. Evidence remains independent of PostgreSQL
  until all pre-delete backup generations expire or are verified destroyed.
- Restore protection must run before serving API traffic or starting workers.
  A failed readiness endpoint alone is insufficient. Ledger I/O must not hold
  account locks; intent, external durability and final purge need resumable steps.

The user resolved the asynchronous product question on 2026-09-18: prevent deletion
of the last ACTIVE, nondeleted OWNER until ownership has been transferred.
Current bootstrap refuses a replacement OWNER while any account exists; deleting
the only OWNER with invited accounts remaining would strand owner administration.
Do not auto-promote another account or relax bootstrap. Check the remaining OWNER
inside the serialized lifecycle transaction, including concurrent requests. The
accepted USB/backup retention policy is already documented and needs no generic
reconfirmation; real ledger/key custody remains deployment preparation.
