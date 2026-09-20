# Android deletion and explicit cancellation: 2026-09-18

Local implementation of ADR-053(6) is complete for the request/cancellation client
slice. The full account goal and production/device acceptance remain unfinished.
Server lifecycle/purge evidence is in
[the server deletion record](ADMIN_ACCOUNT_DELETION_2026_09_18.md).

## Result and ownership

The profile screen now requires the typed account ID, current recovery code and
an explicit destructive confirmation before requesting deletion. It explains the
30-day cancellation window, immediate authority revocation and preservation of
local music. Last-active-OWNER rejection is displayed without a deletion action.

The source runtime persists one encrypted exact signed request, its original bearer,
code and captured binding before detaching that binding or contacting the server.
Request submission never refreshes/replaces its bearer. After a lost reply it first
queries the exact historical receipt without bearer authority; only the original
request may be resent. Positive receipt publication checks the full journal under
the shared binding gate. A delayed result cannot detach a newer account/device.
Recorded receipts retain only the proof needed to refresh current deletion phase.

Cancellation uses a separate DELETE_CANCEL recipient journal and purpose-bound
proof. The confirmation names the account and exact UTC deadline. It persists the
server result and local binding before resolving an interrupted commit or exporting
the rotated TXT. It clears only a matching known source request. An unknown source
operation blocks new binding and cannot be discarded; a known pending deletion of
another account does not block an unrelated first binding.

Recovery/deletion envelopes use flat bounded JSON. The receipt is encoded in
`receipt_b64`; only bounded HTTP error parsing explicitly permits depth two.
Reserved journal slots cannot become application session profiles. Session refresh,
rotation, first binding and interrupted-binding cleanup respect pending authority.
Sensitive deletion/recovery screens retain the secure-window guard.

Implementation entry points: `application/accountrecovery/AccountDeletionProtocol.kt`,
`AccountDeletionTransport.kt`, `AccountDeletionSourceRuntime.kt`,
`SettingsAccountDeletionBindingPort.kt`, `MainAccountDeletionUi.kt`, and
`ui/profilepairing/AccountDeletionCard.kt`, under `apps/android/src/main/kotlin/app/autplay`.

## Verified evidence

- Final affected authority/protocol batch: **62 JVM passes**. Final affected network,
  credential and feature batch: **34 JVM passes**. These are 96 distinct cases in
  the two final batches; earlier recovery counts overlap and are not added.
- App APK and instrumentation APK built with strict dependency verification,
  one Gradle worker and the standard heap.
- Final deletion UI class: **4 passes**, 9.733 s. The unchanged six recovery UI cases
  passed in the earlier combined run. This is 10 distinct passing cases, not a
  claimed single final 10-case run.
- Three synthetic RU/EN dark-theme screenshots were inspected: request, last-owner
  rejection and explicit cancellation with account/deadline. They are fixtures,
  not physical-device or live-server evidence.
- Separate final deletion lint: **0 errors, 0 warnings, 1 pre-existing hint**.
- Server deletion capability: six role/flag cases passed; the selected settings,
  profile and runtime group passed 54 cases. Scoped Ruff and mypy passed.
- Independent read-only review is closed after journal CAS, own-slot, binding-veto
  and purpose/cancellation findings were fixed. The implementing agent ran checks.

Ignored local evidence lives in `apps/android/build/deletion-verification/`:
`unit-fixed/`, `unit-network/`, `instrumentation-deletion-final.txt`, `screenshots/`.
APK paths are `apps/android/build/outputs/apk/debug/android-debug.apk` and
`outputs/apk/androidTest/debug/android-debug-androidTest.apk`.

## Limits

The subsequent [exact resolution slice](ADMIN_DELETION_RESOLUTION_2026_09_19.md)
implements authoritative nonacceptance for expired originals covered by independent
early evidence. Android clears only an exact validated negative after durable journal
CAS, retaining no old binding. ACTIVE status, denial or elapsed time alone still
cannot clear it. A later refresh-bound `/deletion/cancel/outcome` repair closes an
unknown committed cancellation after ordinary receipt expiry only when the exact rotated
refresh secret, signed request, cancellation intent and live binding all still match.
Pre-cutover requests and ambiguous attempted request commits remain fail closed. These
local tests do not substitute for real server/device acceptance.

Checks used only the owned read-only API-26 x86_64 emulator on `emulator-5556`, then
shut it down. The connected M52 was untouched and does not substitute for A55.
Real server/SAF/Keystore/device acceptance, Windows Hello and private-network
acceptance remain open. No production migration, enablement, commit or push occurred.
