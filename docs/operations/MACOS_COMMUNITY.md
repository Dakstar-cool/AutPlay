# Free community macOS signing and account storage

## Decision and scope

The project owner requested this opt-in provider on 2026-11-10: distribute the
macOS port to friends without paid Apple Developer membership, preserving the
strongest practical protection compatible with that choice. It is separate from
the existing Developer ID / provisioning-profile / Secure Enclave provider. A
failed Developer ID check never silently selects community storage.
The current package targets Apple Silicon (ARM64), with macOS 13 as its deployment
target. Native acceptance passed on macOS 15 and 26; the final package gate passed
on the standard macOS 26 ARM64 runner. This is not an Intel build.

## Verified final DMG, 2026-11-10

Source: `177f6430c7b7b03c37fc18d55bb463b59ac15728`.
The [final community run](https://github.com/Dakstar-cool/AutPlay/actions/runs/42842808249)
passed on macOS 26.7.2, build `25G332`, image `macos-26-arm64`
`20261102.0473.1`. It reran all 28 shared/desktop JUnit cases with zero failures or
skips, all 14 real Keychain/identity checks, and production GUI startup with the
bundled JDK.

The final `dmg-acceptance.json` also confirms the staged bundle seal, the signature
of the bundle mounted from the completed read-only DMG, production GUI startup
from that mounted image, and absence of test-only code in the distributed bundle.
The package artifact is `AutPlay-community-arm64-177f6430c7b7b03c37fc18d55bb463b59ac15728`
(artifact ID `13778285973`). The downloaded DMG is 113018142 bytes; independent
SHA-256 verification on Windows matched both `SHA256SUMS` and the DMG acceptance
report. The downloaded public certificate and its pin also match the repository.
The DMG SHA-256 is:

```text
f1640197dd8367878703641756ee73b50f6e993d7279ec569496c6f90fd6ed8a
```

This is the package to use for friend-installation testing. The manual Gatekeeper,
update/Keychain reauthorization and compatible-server login/sync gates below still
apply. The earlier foundation result remains applicable to the unchanged native
application code; subsequent changes only affect packaging, CI runner selection
and this evidence record.

## Native acceptance milestone, 2026-11-10

Source: `23d17a46406be95ab497c7cd2e1b9e4f1cdddbfe`.
Both [community acceptance](https://github.com/Dakstar-cool/AutPlay/actions/runs/42834919924)
and the [desktop foundation gate](https://github.com/Dakstar-cool/AutPlay/actions/runs/42834919676)
passed. Each ran 28 shared/desktop JUnit cases with zero failures or skips. Community
acceptance additionally passed 14 Keychain/identity checks and signed GUI startup;
the foundation gate passed all seven native WAV scenarios on macOS 15.8.2 ARM64.

Historical artifact: `AutPlay-community-arm64.dmg`, 113037635 bytes.
Its downloaded copy was independently hashed on Windows and matched CI:

```text
eeb4a9d0b66934272b21fec4a90fce49400e4e58fa8fc2357e6983c9441329d8
```

All three private-key export attempts were rejected with the final sensitive,
non-exportable key generator. The same certificate's sealed package survived
restart, enforced locked-Keychain refusal, rejected an unrelated signer spoofing
the AutPlay identifier, and rejected that process's private-key signing attempt.
The probe also rejected JVM attach, inherited Java agents and resource tampering.

This qualifies the community client storage/signing boundary. First-open Gatekeeper
approval, interactive reauthorization after an actual update, audible output and
end-to-end login/sync against a compatible server remain manual/integration gates.
This first artifact predates the final mounted-DMG acceptance gate. It is retained
as historical evidence, not the package to distribute; use a successful build that
also contains `dmg-acceptance.json`.

## Build identity

`autplay.macCommunitySignerSha256` selects the provider at compilation. Its value
is the SHA-256 of the DER certificate in
[`config/macos/community-signer.pem`](../../config/macos/community-signer.pem).
The sealed identity record and all native identity boundaries must agree with
this compiled pin. No environment variable or user file can change it at runtime.

The native bootstrap checks its certificate, designated requirement, Hardened
Runtime flags and the complete sealed bundle before loading the bundled JVM.
It removes inherited JVM injection options and `DYLD_*` variables, and disables
HotSpot's external attach/agent-loading mechanism before starting Java. Java and its
libraries are bundled and individually signed. Native account/source libraries
and the remote-media helper retain their exact identifiers and signer checks;
the helper still must pass PID, path and code-hash checks.

## Keychain behavior

The community provider uses the user's default file-based Keychain, constrained
to an owned, non-symlink encrypted file inside the owned `~/Library/Keychains`
directory, with other-user writes denied on both file and directory. It does not
use an entitlement access group, Data Protection Keychain, iCloud Keychain or
the Secure Enclave. Service/key-label namespaces include the certificate pin;
existing paid-provider items are neither read nor migrated.

Credential items use an explicit trusted-application ACL created from the signed
application. A null trusted-application list is forbidden because it means all
applications. Reads reject broad or multiple-application access lists. The OS
enforces the designated requirement and partition ACL. Background operations
cannot open a password prompt or automatically unlock the Keychain.

Device keys are permanent P-256 keys generated inside Security.framework and made
sensitive and non-exportable at creation. Only public keys and domain-separated
signatures cross JNI. No private-key export method is exposed to Java. Existing
bounded operation queues, writer lock, exact readback, revision checks, key intents
and recovery rules remain in force. Locked/missing/denied items are errors, never
permission to silently replace a key or recreate an account namespace.

The TLS, account enrollment, D1v2 proof domains, session renewal, revocation and
sync authorization protocols are unchanged. A compatible server is still required.
The branch includes the D1/B1 server continuation, with source qualification limited
to enrollment, session and portable for D1, and Android/macOS for B1. Operator flags
remain required. Native Keychain evidence alone is not server deployment or an
end-to-end account/sync acceptance claim.

## Account and sync acceptance, 2026-11-11

Source `4ce6a5af043c1d7d5199e5c0f191d6d1d5a8dd2f` passed
[the full macOS 26 community run](https://github.com/Dakstar-cool/AutPlay/actions/runs/43000705704),
including the signed DMG, native Keychain checks and all four account phases:
registration, restart with session rotation, login from a second Keychain and
incremental synchronization back to the first installation. The fixture used
PostgreSQL 18.4 and pgvector 0.8.6, verified HTTPS and the production account graph.

The account proof is `account-acceptance.json`; it identifies both the exact source
and the disposable candidate qualification. The server now publishes an actually
inserted nested library entry as the existing v1 `LIBRARY_ENTRY_UPSERTED` event,
in the same transaction as its track and ACK. This closes the incremental-pull
gap without manufacturing a relation when the requested library ID already exists.
The live server and production qualification registries are not enabled by this run.

Source `57a3383dbcb061c7b76bfc06c25b26c2fc103108`, with SQLAlchemy 2.0.57, also passed
[community run 43019153008](https://github.com/Dakstar-cool/AutPlay/actions/runs/43019153008).
All 30 shared/desktop tests and all four account phases passed, together with the
native Keychain, signed bundle and mounted-DMG checks. The downloaded DMG's SHA-256
is `dd44e0b70d69fb7fc77a8f90861b9714db8af418ccd2601b6e19d651ad63f94e`.
This run still uses the isolated candidate registry override; it does not qualify
the final source registries or the deployed port 9443 target.

## Selective activation and live target check

Production composition gates first effects by the source-owned D1 family registry,
in addition to the existing operator flags. Enrollment and session, and admission
and trusted reenrollment, have independent checks even though each pair shares a
service. Web approval of a v2 admission uses the same admission qualification.
Self, recovery, deletion and portable have their own source gates. The checks stay
at the existing first-effect boundaries; handlers for bounded committed replay,
terminal receipts and cleanup remain mounted. Android v1 feature flags are preserved.
An empty test policy continues to prove refusal independently of the release registry.

The B1 cancellation gate also checks physical PostgreSQL backend disposal. A failed
first-dialect initialization exposed [SQLAlchemy issue 13548](https://github.com/sqlalchemy/sqlalchemy/issues/13548)
in the previous 2.0.52 pin. The server and its dependent GPU/training lock graphs now
pin 2.0.57, including the upstream disposal and subsequent cancellation fixes. The
regression retains the raw driver connection so garbage collection cannot conceal
an ownership leak. Backend disappearance is observed within a fixed bound using
fresh observer transactions, after owner, pool and driver cleanup assertions.
This dependency change requires fresh complete qualification before deployment.

[Server run 43016503571](https://github.com/Dakstar-cool/AutPlay/actions/runs/43016503571)
passed 8,547 tests, including B1 cleanup, with 111 platform skips, but failed one
TCP deadline/EOF observation. Investigation independently reproduced a test-state
leak: worker startup left the root logger attached to pytest's closed stderr.
Server tests now restore the prior root handlers and level after each test.
Production transport, deadlines and test timeouts are unchanged. The logging leak
is verified; its role in the observed timeout was a hypothesis requiring a fresh
complete server gate.

Source `e1c4a3bc53cbe575e3c9b41888f8fc76466eb888` then passed
[server run 43029200688](https://github.com/Dakstar-cool/AutPlay/actions/runs/43029200688):
8,548 server tests and 342 contract/release tests, with 111 platform skips. Static
checks passed and the owned PostgreSQL container, network and volume were removed.
This baseline supports promoting only D1 enrollment/session/portable and B1
Android/macOS. Admission, trusted reenrollment, self, recovery and deletion v2 stay
closed; Android v1 flags and bounded committed replay remain unchanged.

The promotion removes the isolated launcher's qualification override. It checks the
exact source registries and their shared-policy aliases, then records them in
`account-acceptance.json` with `synthetic_candidate_qualification=false` and scope
`ISOLATED_SYNTHETIC_ACCOUNT`. Fresh full server and native Mac gates must verify this
promoted source before rollout. Neither the baseline nor isolated acceptance proves
deployment, fresh backup restoration, or successful login against the live port 9443.

`scripts/macos_account_acceptance.py live` is a prepared, separate live-target mode.
It has not been executed against production. It requires a disposable macOS GitHub
runner with the exact deployed source, the normal community signing setup, built
app and acceptance JAR, and an owner-only file named by
`AUTPLAY_LIVE_ACCEPTANCE_TARGET`. This mode never starts a server, migrates a
database or replaces qualification registries.

The private target file is prepared from the final deployment/restore receipts and
a fresh authenticated target observation, not from untrusted discovery. Its exact
fields are validated by `validate_live_target`: HTTPS origin on port 9443, expected
server UUID/epoch and identity/TLS SPKI, exact server source/image/schema, hashes of
the deployment and backup-restore receipts, both completed boundary indicators and
an observation less than one hour old. These fields are operator evidence inputs;
parsing the file does not independently prove deployment or backup restoration.

The probe generates its own new account ID/password and runs register, restart,
second-installation login and pull with two separate real Keychains. It checks the
pinned identity and HTTPS through the production client and retains the existing
v1 journal protocol. It accepts no owner account credentials. Synthetic server rows
are retained; there is no account deletion or change to the owner's password.
Only after all four phases pass does `live-account-acceptance.json` record live
9443 evidence. It contains hashes and aggregate results, not the private origin,
account ID, password or tokens. Ordinary isolated CI does not invoke this mode.
After the deployment and restoration boundary is actually complete, deliver the
fresh private target JSON through the repository secret
`AUTPLAY_LIVE_ACCEPTANCE_TARGET_JSON` and dispatch the exact deployed branch with
`live_account_acceptance=true`, `account_acceptance=false`. The workflow refuses
both scopes together and keeps live acceptance off by default. The target must be
reachable from that runner using its real advertised HTTPS origin; changing
identity/origins or bypassing TLS to make the probe connect is forbidden.

## Differences from the paid design

* A self-signed certificate provides a stable project identity, but Apple does not
  attest the publisher and does not notarize the package. Gatekeeper requires a
  deliberate per-app first-open decision by the recipient.
* P-256 key export is denied by the OS software provider. This does not provide
  Secure Enclave hardware non-extractability against a compromised OS or user
  session. File-based Keychain backup/migration semantics also differ from
  `WhenUnlockedThisDeviceOnly`.
* Keychain lock state is enforced on every protected operation. Locking the screen
  does not necessarily lock the login Keychain; its settings remain the user's.
* Without an Apple Team ID, library validation cannot accept the bundled JVM under
  its usual same-Team-ID rule. Only the main launcher receives `allow-jit` and
  `disable-library-validation`; native helpers retain library validation. The
  main executable validates the seal and strips injection settings before loading
  the pinned JDK's JLI in-process. No alternate account-authorized JVM launcher is
  shipped. Debugging, DYLD injection and unsigned executable memory
  entitlements remain forbidden. Bundle validation before Java starts reduces
  this exposure, but does not make it equivalent to Apple's library validation.
* macOS can partition self-signed Keychain access by code hash. A changed release
  can require explicit OS reauthorization even with the same certificate. Never
  fix this by granting all apps access or assigning broad Apple tool partitions
  to account keys. The explicit **Reopen accounts** action closes the old account
  owner, validates the new bundle and lets macOS request approval for existing
  items. It neither collects the password nor changes item contents. Interactive
  update/reauthorization qualification is tracked separately from automated CI.

## Signing custody and workflow

The stable private certificate is an encrypted PKCS#12 outside the repository.
Its local password is protected by Windows DPAPI and the directory has a private
ACL. GitHub receives only encrypted Actions secrets `AUTPLAY_COMMUNITY_P12` and
`AUTPLAY_COMMUNITY_P12_PASSWORD`; build artifacts never contain them. Keep a
separately protected backup before relying on the identity for long-term updates.
Losing or replacing this key changes the identity and is not an automatic migration.

[`CI / macOS community`](../../.github/workflows/ci-macos-community.yml) uses the
standard public-repository `macos-26` runner (`macos-15` is an explicit manual
choice), pinned JDK/Python and strict Gradle
dependency verification. It does not run on pull requests or use a paid larger
runner. Private signing material is installed in an ephemeral signing keychain;
synthetic account tests use a different disposable keychain. The CI-only
`apple-tool:,apple:` grant belongs to the signing certificate, never to account
items. Temporary keychains are removed and the previous search/default keychain
settings restored even on failure. Neither build nor runtime acceptance adds the
certificate to global trust settings.

The gate builds/tests the shared and desktop modules, signs the app, checks the
real production JNI in a separately sealed probe app, starts the production GUI
with its bundled JDK, then creates the DMG while preserving signed symlinks. It
mounts the final image read-only, verifies the shipped bundle's signature and
starts its production GUI before accepting the artifact. The probe classes/native checks are
never shipped in the production package. Keychain checks cover persistence after
process restart, D1v2 signature verification, private-key export rejection,
locked-Keychain refusal, foreign-program read/sign refusal and resource tampering.
The same probe also opens, initializes and reopens the real Kotlin secure-state
owner on a second empty keychain, checks the production sealed-identity reader,
and tests rejection of inherited Java agents and a real external `jcmd` attach.

The manual `account_acceptance=true` option additionally runs
[`macos_account_acceptance.py`](../../scripts/macos_account_acceptance.py).
It compiles the pinned PostgreSQL/pgvector fixture, starts the production server
behind verified HTTPS and exercises the actual signed Kotlin account graph.
The four phases cover registration, process restart and session renewal, login
from a second isolated Keychain, and a journal round trip between both devices.
The fixture enables its operator flags and requires the checked source D1/B1 policy;
it never adds qualification entries or replaces the production registries.
The required success evidence is `account-acceptance.json` with all four phases;
passing packaging or the earlier Keychain probe does not substitute for it.
Passwords, recovery material, private configuration and raw HTTP bodies remain
outside artifacts. This isolated acceptance does not verify a deployed server.

Successful DMGs are retained for one day; small reports for three days. Standard
public runner compute is free, but artifact storage is metered separately. Download
the DMG promptly and avoid accumulating old package artifacts.

## Installing a verified build

Use the DMG and `SHA256SUMS` from the same successful workflow run. Check the DMG
checksum against a value received through the trusted project channel. Copy
`AutPlay.app` into `/Applications`. Keep Gatekeeper enabled; use macOS's per-app
**Privacy & Security > Open Anyway** only after verifying the source and checksum.
Do not install the project certificate as a system-wide trusted root, disable
Gatekeeper globally, or remove quarantine recursively.

The account screen offers explicit storage initialization on first use. If the
Keychain is locked, unlock it through macOS and reopen account access. Existing
account metadata is not an instruction to reset protected storage. Reauthorization
after an update must preserve all existing keys and pending operations.

## Primary references

* [Apple TN3137: macOS keychains](https://developer.apple.com/documentation/technotes/tn3137-on-mac-keychains)
* [Apple: code signing and self-signed identities](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/AboutCS/AboutCS.html)
* [Apple: library validation entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.disable-library-validation)
* [Apple: Keychain exportability constraints](https://developer.apple.com/forums/thread/731001)
* [Apple: opening software outside App Store](https://support.apple.com/102445)

Verification status and exact run/artifact identity must be taken from the current
workflow evidence. Prior unsigned desktop successes do not qualify this provider.

## Initial unattended-signing diagnosis

The first hosted runs exposed interactive certificate trust-setting operations
and a signing-identity lookup failure. Options reviewed were user trust settings,
admin-domain trust through sudo, repairing PKCS#12/private-key identity lookup and
the keychain search list, and using an independent signing implementation.
The selected approach retains Apple's signing tool, follows GitHub's isolated
keychain/search-list setup, identifies the certificate by its full SHA-1 locator,
and performs a small signing check before Gradle. It makes no global trust changes.
The runtime separately pins SHA-256, so the SHA-1 identity locator is not the
project's certificate trust decision. See [Apple TN3161](https://developer.apple.com/documentation/technotes/tn3161-inside-code-signing-certificates),
[Apple TN2206](https://developer.apple.com/library/archive/technotes/tn2206/) and
[GitHub's runner signing guide](https://docs.github.com/en/actions/how-tos/deploy/deploy-to-third-party-platforms/sign-xcode-applications).

The native launcher calls the pinned JDK's JLI in-process, retaining its macOS
main-thread handoff. Its ABI and entry behavior were checked against OpenJDK's
[JLI declaration](https://github.com/openjdk/jdk17u/blob/master/src/java.base/share/native/libjli/java.h)
and [jpackage macOS launcher](https://github.com/openjdk/jdk17u/blob/master/src/jdk.jpackage/macosx/native/applauncher/MacLauncher.cpp).

Keychain file validation accepts the platform's encrypted-database permissions
only inside an owned keychain directory, with no symlinks or other-user
writes. Requiring a plaintext-secret file mode would reject normal macOS Keychains:
Apple's [file database implementation](https://github.com/apple-oss-distributions/Security/blob/main/OSX/libsecurity_filedb/lib/AppleDatabase.cpp)
defaults to mode `0666` before umask. Alternatives were changing the user's login
Keychain mode or introducing another password-protected keychain; neither is needed
to preserve the encrypted-store and item-ACL boundary.

Run `42825846281` located the remaining preflight failure at the directory's
overly strict `0700` assumption. The hosted user Keychain directory is readable
by other users, as macOS also permits for encrypted file Keychains. Reviewed
options were forcing `chmod` on the user's existing directory, creating a separate
app keychain with a new password lifecycle, switching to the entitlement-based
Data Protection provider, and accepting standard read/list permission while
rejecting other-user writes. The last option preserves platform ownership,
encryption and [per-item ACL enforcement](https://developer.apple.com/documentation/security/access-control-lists)
without modifying user system settings. CI tests the actual item boundary using
an unrelated signer with a spoofed AutPlay identifier, rather than relying on
directory read permissions to protect plaintext secrets.

Run `42828384378` passed protected item creation/readback and reached key creation.
Apple's legacy [SecItem attribute mapping](https://github.com/apple-oss-distributions/Security/blob/main/OSX/libsecurity_keychain/lib/SecItem.cpp)
does not return sensitive/extractable attributes. The provider therefore obtains
those flags through public `SecKeyCopyAttributes`, whose
[legacy key implementation](https://github.com/apple-oss-distributions/Security/blob/main/OSX/libsecurity_keychain/lib/SecKey.cpp)
exposes them. Non-exportability remains mandatory and is independently tested with
three export APIs. The same source shows the high-level legacy generator ignores
`kSecAttrIsSensitive`. Run `42832892641` then demonstrated why checking only the
non-exportable attribute is insufficient: wrapped/plain `SecItemExport` refused,
but `SecKeyCopyExternalRepresentation` returned 97 private-key bytes. No such build
was distributed; only synthetic disposable keys were involved.

Alternatives reviewed against Apple's APIs were explicit flags through public
[`SecKeyCreatePair`](https://developer.apple.com/documentation/security/seckeycreatepair),
generating/importing raw private bytes with `SecItemImport`, removing export/decrypt
permissions from key ACLs, or returning to the entitlement-based provider. The
selected generator sets `PERMANENT | SENSITIVE` without `EXTRACTABLE`, with signing
as its only private-key usage. Both immutable flags are verified on every read.
It attaches the intent's label/tag through the public metadata API, as Apple's
high-level generator does internally, and verifies exact public-key readback before
returning. A metadata failure cleans up only the just-created unpublished key.
No private key is imported/exported by production code, and all three adversarial
export checks remain mandatory. Abrupt OS/process death between provider creation
and metadata assignment can leave an unlabelled, never-published key; no account or
server operation can have used it, and an existing bound key is never replaced.
The launcher also forces the documented
[`DisableAttachMechanism` option](https://docs.oracle.com/en/java/javase/17/docs/specs/man/java.html)
to close HotSpot's same-user agent-loading channel.

The final DMG run `42839864898` was cancelled before acquiring any runner, with
GitHub's explicit macOS ARM64 capacity annotation. Options considered were a
bounded retry, waiting for the same pool, another standard ARM64 image, or a
self-hosted Mac. The workflow now defaults to `macos-26`, with only `macos-15`
as its other allowed choice. Both are listed as free standard public-repository
runners in [GitHub's runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
No larger/paid runner, toolchain upgrade, Intel substitution or relaxed security
acceptance is used to bypass the queue.
