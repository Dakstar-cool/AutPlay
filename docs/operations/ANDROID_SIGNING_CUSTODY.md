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

The current AutPlay APKs are development-signed. A newly created production signer cannot silently
replace that signer while preserving the ordinary update contract. Before distributing the first
production APK, accept one explicit transition:

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

Until that gate is green, the APK is not production-update-ready and PA3 remains blocked even when
TLS and server routing are locally valid.
