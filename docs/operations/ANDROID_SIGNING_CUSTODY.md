# Android production signing custody

## What the keystore is

An Android signing keystore holds the private key that proves future APK updates are from the same
AutPlay publisher. Android accepts an in-place update only when its package and accepted signer
history match. Losing the long-lived key can make safe updates impossible; leaking it lets an
attacker produce packages that appear to come from the publisher.

The server P-256 identity key signs AutPlay server discovery. It is a different key with a
different trust boundary. The Jamendo client ID is also unrelated. Neither can replace the Android
APK signer.

## Production custody rule

The production Android private key must never be stored on the AutPlay server. The minimum
single-operator custody is:

- one encrypted working copy on the encrypted development laptop;
- two tested encrypted recovery copies outside that laptop and outside the production server,
  kept in separate failure domains;
- passwords/recovery material stored separately from the encrypted keystore copies;
- a restricted signing procedure that does not print passwords or private-key material.

Examples of separate recovery failure domains are an encrypted offline removable device kept away
from the laptop and a separately encrypted off-site store. This procedure does not choose a vendor,
cloud account or physical location without the operator's explicit decision.

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

## First production transition decision

The existing qualification installation is a disposable test installation. The operator
confirmed that their local application state does not need to be retained for the first production
release. The accepted transition for `v1.0.0`, `versionCode 13`, is therefore a controlled clean
reinstall followed by fresh production qualification. APK signer lineage and a full Room/data
export-import mechanism are not required for this release.

Before removal, verify and record that the selected device is a test device and that it contains no
valuable local-only or unsynchronized state. Uninstalling development-signed `app.autplay` removes
its Room database, DataStore and encrypted preferences, Android Keystore entries, persisted access
grants, offline downloads and application-private caches. Shared media files and server-side data
are not application-private backup evidence and still require fresh permission, indexing, pairing
and synchronization checks after installation.

The controlled transition is:

1. record the installed package version and development certificate fingerprint;
2. confirm that any test state may be discarded and stop the application;
3. uninstall development-signed `app.autplay` explicitly;
4. install the exact production APK and verify package, version, APK SHA-256 and production
   certificate SHA-256 before first launch;
5. grant media access again, re-index local media, create a new server binding and complete sync;
6. repeat the exact-release physical-device playback, process-death, pairing/recovery and accessibility gates.

This is not a general migration promise. Any future development-signed installation containing
valuable local-only data must stop before uninstall and receive a separately reviewed lineage or
export/import migration.

## Historical PA3 update proof

The retained PA3 proof used the following procedure without exposing the private key:

1. build two consecutive production release versions with the accepted signer;
2. inspect both APKs with the pinned Android SDK `apksigner verify --verbose --print-certs` and
   record the same expected SHA-256 certificate fingerprint;
3. install the first version on a test device, create synthetic local AutPlay state, and install
   the second with `adb install -r`;
4. prove the application data, Room migrations, local playback and server binding remain intact;
5. decrypt and verify both recovery copies, then return offline media to protected storage;
6. keep signing passwords, keystore bytes, aliases that reveal private inventory and raw device
   identifiers out of logs and committed evidence.

This gate passed on 2026-09-02 for the exact production-signed version-code 3 to 4 APK hashes
recorded in [`PA3_ANDROID_SIGNING_EVIDENCE.json`](../release/PA3_ANDROID_SIGNING_EVIDENCE.json), on
an independent API 26 emulator data image. It remains historical update-path evidence only. It is
not a production release manifest, does not cover another APK hash, and must not be regenerated by
substituting the first production version. PA3 remains `BLOCKED` only for explicit live-edge
approval and the external TLS/scan/renewal/rollback/mobile Range evidence; no APK publication is
implied.

## Exact production signed-build procedure

The compatibility entry point remains `scripts/build-pa3-signed-release.ps1`, so reviewed signer
storage, environment-variable names and operator tooling do not change. Its production behavior is
no longer tied to the historical `0.3.0-pa3.1` / `0.3.0-pa3.2` pair. Every invocation, including
preflight, requires the approved `versionCode`, `versionName`, release tag and full source commit.
The command fails before requesting secrets unless the worktree is clean, `HEAD` equals the stated
commit, and the exact `refs/tags/<tag>` resolves to that same commit.

Set the following non-secret variables to values approved for the release. The examples
deliberately do not choose the first production version or distribution channel:

```powershell
$releaseVersionCode = [int](Read-Host "Approved positive versionCode")
$releaseVersionName = Read-Host "Approved versionName"
$releaseTag = Read-Host "Existing release tag"
$releaseCommit = Read-Host "Full source commit"
```

From the repository root, verify the non-secret inputs first:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\build-pa3-signed-release.ps1 `
  -Preflight `
  -VersionCode $releaseVersionCode `
  -VersionName $releaseVersionName `
  -ReleaseTag $releaseTag `
  -SourceCommit $releaseCommit
```

Run the real build in a visible Windows PowerShell process so both `Read-Host -AsSecureString`
prompts remain interactive and hidden. Do not redirect this command through a background PTY or
paste either secret into chat or logs:

```powershell
powershell.exe -NoExit -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\build-pa3-signed-release.ps1 `
  -VersionCode $releaseVersionCode `
  -VersionName $releaseVersionName `
  -ReleaseTag $releaseTag `
  -SourceCommit $releaseCommit
```

The launcher disables the Gradle configuration cache for the signed build. The Android build also
fails closed if all signing environment variables are present while configuration caching is
requested. This prevents production signing secrets from being serialized into a project cache.

The preflight also requires the configured Java and Android SDK tools and verifies the pinned
SHA-256 hashes of `age.exe` and `age-plugin-batchpass.exe` before any secret is requested. Override
`-AgeDirectory` only when the replacement directory contains those exact reviewed binaries.

The launcher performs a clean, minified `assembleRelease` with an explicit production Gradle flag
and rejects unsigned, QA-side-by-side or implicit-version production configuration. It accepts
exactly one generated release APK, then verifies `app.autplay`, the requested version code/name,
the APK signature, a single signer and the certificate fingerprint from the signer receipt.

Every schema-v3 build receipt records the clean Git commit and tag match, SHA-256 hashes of the
captured status and tracked binary diff, and the empty untracked-input manifest. The companion
`android-production-manifest.json` binds that source identity to the exact APK filename, byte size,
SHA-256, package/version, signer certificate SHA-256, and the latest tracked Room schema version,
identity hash, repository path and SHA-256. Source and Room identity are checked before and after
the build; instability or drift fails closed.

The temporary directory and plaintext PKCS12 use verified owner-only protected DACLs. The APK,
manifest and receipt are first assembled in a staging directory; the two JSON files are written
atomically, and the complete directory is renamed into place only after plaintext cleanup is
confirmed. Failed staging output is removed. Receipts and manifests contain no passwords,
keystore bytes, aliases or recovery material.

## Production input finalization

Signing produces Android evidence, not a deployable cross-project release by itself. After the
exact CPU server image archive and Android release dependency report have been produced from the
same clean tag and commit, finalize them without signing credentials:

```powershell
uv run --frozen python -m scripts.finalize_production_release `
  --output-directory .\dist\production\v1.0.0 `
  --android-manifest $androidProductionManifest `
  --android-apk $androidProductionApk `
  --android-dependency-report $androidDependencyReport `
  --server-archive $serverArchive `
  --source-commit $releaseCommit `
  --release-tag $releaseTag `
  --version-code $releaseVersionCode `
  --version-name $releaseVersionName `
  --signer-certificate-sha256 $productionCertificateSha256 `
  --artifact "server_installer=$serverInstaller"
```

The finalizer re-hashes every copied input, derives the Docker image digest from its configuration
object, binds the repository's one Alembic head and runs the fresh release-audit gate. Its output is
a signed-but-not-deployed `PRODUCTION_RELEASE_CANDIDATE`; live activation remains blocked until the
exact physical-device, target, public-edge and operator gates pass.

## Current evidence

[`PA3_ANDROID_SIGNING_EVIDENCE.json`](../release/PA3_ANDROID_SIGNING_EVIDENCE.json) is the bounded
non-secret index. It links the exact APK hashes used for the successful update proof, the later
schema-v1 interactive build hashes, and the independently approved historical schema-v2 workflow.
It does not transfer the update claim to different APK hashes. The parameterized schema-v3
production workflow has not been run with production secrets, and no production APK has been
published.
