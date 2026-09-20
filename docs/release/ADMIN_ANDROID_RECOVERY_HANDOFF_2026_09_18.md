# Android account recovery handoff: 2026-09-18

Superseded for current implementation/test status by
[Android recovery verification](ADMIN_ANDROID_RECOVERY_VERIFICATION_2026_09_18.md).
The historical snapshot below records the earlier interrupted turn.

Prepared at the user's request to save work and move to a new task, approximately
16:52 Europe/Moscow. This is the newest continuation entry. The feature and full
goal are unfinished. All implementation is saved in the existing working directory;
there is no commit, push, deployment or production migration.

## Continue here

- Saved project: `D:/AutPlayProd`; actual repository: `D:/AutPlayProd/AutPlay`.
- Branch: `codex/readme-current-state`; HEAD: `860511aae9f1fcdb98b7b9b944f97ef27929fb8b`.
- Preserve the extensive modified/untracked work, including adjacent music,
  metadata, resource, playback and sync changes. A fresh checkout of HEAD would
  lose the implementation. Work directly in this directory.
- Full prior context and all seven acceptance criteria are in
  `ADMIN_CONTINUATION_2026_09_18.md`; server recovery evidence is in
  `ADMIN_ACCOUNT_RECOVERY_2026_09_18.md`. Their statements that Android recovery
  still needs to be written are superseded by this handoff, not by a completion claim.
- Follow root `AGENTS.md` and `.agents/skills/autplay-development/SKILL.md`.
  Do not invoke skill-state-workflow, state guard, or modify `.codex-state`.
- The user strongly objects to unchanged speculative percentages and repeated
  broad tests. Report completed functions and remaining work. Finish a coherent
  code block, run targeted checks, then one affected regression group. Reuse
  passing evidence while its inputs remain unchanged.

## Written Android implementation (not yet build-verified)

New application files in
`apps/android/src/main/kotlin/app/autplay/application/accountrecovery/`:

- `AccountRecoveryProtocol.kt`: bounded strict TXT parsing/export, ASCII code
  normalization, server/account-scoped SHA-256 verifier, canonical signed P-256
  preview/commit requests and response binding validation. Secret byte arrays are
  erased when closed and excluded from generated diagnostic strings.
- `AccountRecoveryPendingStore.kt`: encrypted SOURCE and RECIPIENT journals in
  reserved credential slots; process-shared `AccountRecoveryJournalGate`.
- `AccountRecoveryTransport.kt`: bounded HTTP, code only in a private header,
  no cookies/redirects, no-store responses, source session refresh on one rejection.
- `AccountRecoverySourceRuntime.kt`: explicit configure/rotate with durable exact
  requests, encrypted next TXT, local/offline export, explicit fresh replacement
  of stale CONFIGURE_PENDING. `loadLocal` clears stale state for a different account.
- `AccountRecoveryRecipientRuntime.kt`: TXT import performs no network activity;
  manual inspection discovers without sending the code. Explicit server trust,
  then account confirmation. Durable key/code/refresh/request state precedes commit;
  unknown COMMIT_PENDING cannot be discarded. Exact replay after restart and local
  completion when the binding is already durable. Promotion preserves a newer
  saved code; source rotation is blocked while recipient cleanup remains pending.
- `ProfilePairingRecoveryBindingCommitter.kt`: adapts recovery to existing M5
  credential-first binding persistence, trusted identity and key checks.

Integration is written in CredentialStore envelope/codec, FirstBindCeremonyGate,
ProfilePairingRuntime, AccountRegistrationRuntime, SelfPairingRecipientRuntime,
SelfPairingProtocol, AutPlayRuntime, MainActivity, MainRouteActionFactories and
ProfilePairingScreen. MainActivity restores recovery before other first-bind paths;
SAF imports at most 4096 bytes, exports only on an explicit user action, wipes buffers,
and reloads the source after recipient Connected. New AccountRecoveryCard and
English/Russian resources implement import/manual entry, two confirmations,
rotation confirmation, retry and offline export; the profile window is protected.

Server GET `/account/recovery` now includes `account_label`; the implementation,
OpenAPI status schema and HTTP regression were updated together.

## Review: four closed, two still open

The bounded read-only reviewer confirmed closure of these first four issues:
recipient retry overwriting a newer backup; permanently stale configure request;
missing export after Connected; offline export hidden by live capability gating.
The fixes are saved, and runtime regressions were written but have not run yet.

Final integration review identified two remaining actionable defects. FIX THESE
BEFORE THE NEXT ANDROID ACCEPTANCE RUN:

1. **Reserved credential slots are not excluded from every first-bind path.**
   AccountRegistrationRuntime writes PA2 pending material directly under
   ServerProfileId(serverInstanceId). SelfPairingRecipientRuntime checks only
   SelfPairingRole slots, not recovery slots. A matching server UUID can overwrite
   the saved recovery document or corrupt the recipient journal. Add a common
   reserved-profile predicate covering both families, enforce before the first PA2
   write, during self-pairing inspection/resume, and before shared binding writes
   or exception cleanup. Also protect ordinary M5 pending writes/cleanup.
   Recovery recipient already rejects both families. Existing reserved UUIDs:
   self-pairing SOURCE `a89a7ef3-fb5a-46aa-a827-1bf120d55dab`, RECIPIENT
   `14c2edb0-f9ef-43d0-af74-63dc06373bfb`; recovery SOURCE
   `f5419a71-6b38-4cb1-a28d-f20d4d8ba780`, RECIPIENT
   `b623bd90-e84b-4621-9c83-a3bd69ba8db1`. Test rejection before any overwrite.
2. **An uncertain settings commit removes already-written credentials.**
   ProfilePairingRuntime.persistBindingLocked catch currently clears credentials
   when preservePublicRegistration is false, including journal-backed external
   recovery/self-pairing. If settings.mutate commits then throws, the durable
   binding loses its credentials, fails isDurable, and becomes dependent on a
   server replay receipt that can expire. Preserve the credential-first pair and
   exact external journal on an uncertain outcome. Add a commit-then-throw settings
   regression proving restart finishes locally without another HTTP exchange.
   Ensure cancellation remains correctly propagated; do not discard unknown state.

The review agent made no edits and ran no tests. Its final integration review found
no other concrete issues within these boundaries. It is not an overall approval
until these two findings are fixed and reviewed.

## Verification actually completed in this turn

- **22 passed in 18.62 s**, no skips: real disposable PostgreSQL recovery HTTP tests
  plus portable TXT/domain tests, including the new shared Android wire vectors.
- **1 passed in 1.45 s**: new root OpenAPI/JSON-schema/canonical-hash contract test.
- Ruff check and format check passed on the four affected Python files; tracked
  `git diff --check` passed (only pre-existing line-ending warnings).
- Earlier server baseline remains 65 distinct cases at migration
  `0051_account_recovery`, documented in the server record. It was not rerun wholesale.

New tests saved on disk:

- `apps/android/src/test/kotlin/app/autplay/application/accountrecovery/`:
  AccountRecoveryProtocolTest, AccountRecoveryRuntimeTest,
  AccountRecoveryTransportTest, AccountRecoveryUiPolicyTest.
- `apps/android/src/androidTest/kotlin/app/autplay/ui/profilepairing/AccountRecoveryCardTest.kt`:
  synthetic EN/RU export and confirmation screens, explicit actions, protected window.
- `tests/fixtures/account-recovery/v1/proof-vectors.json`: test-only P-256 key and
  canonical server/client request, signature, verifier and TXT examples.
- `tests/contract/test_account_recovery_contract_v1.py`; existing server recovery
  document/HTTP tests gained interoperability and account-label assertions.

The first Gradle invocation reached `:apps:android:compileDebugKotlin` but had no
result when the user requested this handoff. It was interrupted with Ctrl+C.
**Do not claim Android compilation, unit tests, lint, APK or instrumented UI passed.**
No Android tests from this new block have produced results yet. There was no
compiler error reported before interruption. The build used one worker and the
standard 2 GiB heap; preserve those settings.

The read-only disposable AVD `autplay_admin_20260916` booted as emulator-5554;
`sys.boot_completed` was 1. No APK was installed and no screen test ran in this turn.
No physical Android devices were connected. Shut down the owned emulator for
handoff; relaunch when actual UI checks are ready. Its logs are under the user's
TEMP directory in `autplay-account-recovery/`.

## Next execution

1. Fix and add targeted regressions for the two open review findings above.
2. Run the affected Android unit group once. Then compile/build/lint the changed
   Android module and run the new card instrumentation on the disposable emulator.
   Inspect the synthetic screenshots generated by that instrumentation. A device
   pass alone does not prove the complete live server flow.
3. Close review and record only actual outcomes. Continue new-account onboarding:
   the requirement to durably save a recovery file before setup is complete is NOT
   yet enforced globally. Current UI provides explicit setup/save actions only.
4. Unknown recipient commit after the one-day receipt expires remains fail-closed
   pending. Do not add a destructive discard/reset shortcut. The full acceptance
   and user recovery path for that boundary remain open.
5. Continue deletion pending/cancel/purge/backup protection, consent withdrawal,
   remaining byte paths/composition and full browser/target-device/measurement
   acceptance. This Android slice does not complete any seven-criterion goal claim.

## Commands and environment

Use PowerShell in the actual repository, Java
`C:/Program Files/Microsoft/jdk-17.0.20.8-hotspot`, ANDROID_HOME
`D:/CodexUtilProgs/Android/Sdk`. Gradle invocations must remain sequential:

```powershell
./gradlew.bat --no-daemon --console=plain --max-workers=1 --dependency-verification=strict :apps:android:testDebugUnitTest --tests 'app.autplay.application.accountrecovery.*' --tests 'app.autplay.application.selfpairing.SelfPairingRecipientRuntimeTest' --tests 'app.autplay.application.profilepairing.ProfilePairingRuntimeTest' --tests 'app.autplay.application.publicaccess.AccountRegistrationRuntimeTest' --tests 'app.autplay.data.security.CredentialStoreTest' --tests 'app.autplay.ui.profilepairing.ProfilePairingSecureWindowTest'
```

Server Python uses `uv run --project server --frozen python`; root contract tooling
uses `uv run --frozen python` (root has JSON-schema/OpenAPI tools). Disposable DB
is container `autplay-admin-20260916-postgres-1`, loopback port 1520, PostgreSQL
18.4/pgvector. Use the existing disposable URL from the prior continuation only;
tests create/drop their own `autplay_p02_*` databases. Do not touch production.

Windows search correction: native `rg` receives positional `directory/*.kt`
literally. Use `rg PATTERN known-directory -g '*.kt'`, or enumerate with
`rg --files ... -g`, or `Get-ChildItem -LiteralPath ... -Filter`. Official ripgrep
GUIDE and Microsoft Get-ChildItem docs were consulted after a repeated glob error;
the directory plus `-g` form succeeded. Do not repeat shell positional globs.
