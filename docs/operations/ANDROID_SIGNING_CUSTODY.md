# Android production signing custody

## What the keystore is

An Android signing keystore holds the private key that proves future APK updates are from the same
AutPlay publisher. Android accepts an in-place update only when its package and accepted signer
history match. Losing the long-lived key can make safe updates impossible; leaking it lets an
attacker produce packages that appear to come from the publisher.

The server P-256 identity key signs AutPlay server discovery. It is a different key with a
different trust boundary. The Jamendo client ID is also unrelated. Neither can replace the Android
APK signer.

## PA3 custody rule

The production Android private key must never be stored on the AutPlay server. The minimum
single-operator custody is:

- one encrypted working copy on the encrypted development laptop;
- two tested encrypted recovery copies outside that laptop and outside the production server,
  kept in separate failure domains;
- passwords/recovery material stored separately from the encrypted keystore copies;
- a restricted signing procedure that does not print passwords or private-key material.

Examples of separate recovery failure domains are an encrypted offline removable device kept away
from the laptop and a separately encrypted off-site store. PA3 does not choose a vendor, cloud
account or physical location without the operator's explicit decision.

Record only non-secret evidence: key algorithm/size, creation date, certificate validity, lowercase
SHA-256 certificate fingerprint, package/application ID, release version, backup copy locations at
a coarse non-sensitive level and the date each recovery copy was decrypted and verified.

## Existing development-signed APKs

The original AutPlay RC and locally installed pre-PA3 APKs are development-signed. PA3 now has a
separate stable production signer for `app.autplay`, but no production APK has been published. A
production signer cannot silently replace an already installed development signer while preserving
the ordinary update contract. Before distributing the first production APK, use one explicit
transition:

- preserve the existing signer under production-grade custody if it is eligible and intentionally
  promoted;
- use a new stable signer/package identity and treat it as a separate installation; or
- adopt a separately reviewed Android signing-lineage/store transition that is proven on every
  supported Android version.

Do not uninstall a development package merely to clear `INSTALL_FAILED_UPDATE_INCOMPATIBLE` when
its local data matters. Uninstalling normally deletes that package's private application data.

## Verification gate

Before PA3 can pass, verify without exposing the private key:

1. build two consecutive production release versions with the accepted signer;
2. inspect both APKs with the pinned Android SDK `apksigner verify --verbose --print-certs` and
   record the same expected SHA-256 certificate fingerprint;
3. install the first version on a test device, create synthetic local AutPlay state, and install
   the second with `adb install -r`;
4. prove the application data, Room migrations, local playback and server binding remain intact;
5. decrypt and verify both recovery copies, then return offline media to protected storage;
6. keep signing passwords, keystore bytes, aliases that reveal private inventory and raw device
   identifiers out of logs and committed evidence.

This gate passed on 2026-09-02 for an exact production-signed version-code 3 to 4 pair on an
independent API 26 emulator data image. PA3 remains `BLOCKED` only for explicit live-edge approval
and the external TLS/scan/renewal/rollback/mobile Range evidence; no APK publication is implied.

## Local signed-build procedure

From the repository root, verify the non-secret inputs first:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\build-pa3-signed-release.ps1 -Preflight
```

Run the real build in a visible Windows PowerShell process so both `Read-Host -AsSecureString`
prompts remain interactive and hidden. Do not redirect this command through a background PTY or
paste either secret into chat or logs:

```powershell
powershell.exe -NoExit -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\build-pa3-signed-release.ps1
```

The launcher disables the Gradle configuration cache for the signed build. The Android build also
fails closed if all signing environment variables are present while configuration caching is
requested. This prevents production signing secrets from being serialized into a project cache.

The preflight also requires the configured Java and Android SDK tools and verifies the pinned
SHA-256 hashes of `age.exe` and `age-plugin-batchpass.exe` before any secret is requested. Override
`-AgeDirectory` only when the replacement directory contains those exact reviewed binaries.

Every schema-v2 build receipt records the Git commit, whether the worktree was clean or dirty,
SHA-256 hashes of the captured status and tracked binary diff, and a per-file SHA-256 manifest for
all untracked inputs. The complete provenance is captured twice before the build and again after
the build; any instability or drift fails closed. The temporary directory and plaintext PKCS12 use
verified owner-only protected DACLs. Receipt publication is atomic and occurs only after the
plaintext file and temporary directory are both confirmed absent.

## Current evidence

[`PA3_ANDROID_SIGNING_EVIDENCE.json`](../release/PA3_ANDROID_SIGNING_EVIDENCE.json) is the bounded
non-secret index. It links the exact APK hashes used for the successful update proof, the later
schema-v1 interactive build hashes, and the independently approved schema-v2 workflow. It does not
transfer the update claim to different APK hashes. The current schema-v2 workflow has not yet been
run with production secrets, and no production APK has been published.
