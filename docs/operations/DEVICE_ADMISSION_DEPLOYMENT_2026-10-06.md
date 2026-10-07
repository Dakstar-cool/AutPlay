# Device admission deployment - 2026-10-06

The approved update was deployed at 2026-10-06 08:11:28 UTC. The new APK was
installed on the connected A55 at 08:11:22 UTC without clearing application data.

After scanning the server QR and sending a connection request, Android now waits
for the administrator's decision. Admin Web lists pending requests directly and
allows naming the device before trusting it or approving one connection. The
user no longer transfers the locator or comparison code. Revoked Android devices
and removed trusted keys disappear from the active lists. Legacy locator routes
remain available for older clients. Explicit account confirmation remains on
Android after approval.

## Artifact identity

- Source base: `a17a4569ab462f2c650c757d1da433cdd58c0b03` with the reviewed overlay.
- Migration: `0060_local_bridge_authority` to `0061_admission_device_name`.
- Admin image: `autplay-device-admission:20261006-admin-new-v2`.
- Admin image ID: `sha256:53667fd369a7d5ac2f6ed115713b26e9751334376dbc4c01d7aa506adf11f425`.
- Common runtime image: `autplay-device-admission:20261006-runtime-92fceaee35e6-new-v1`.
- Mobile API image: `autplay-device-admission:20261006-runtime-552be719675a-new-v1`.
- CPU-worker image: `autplay-device-admission:20261006-runtime-7f77649eb2ff-new-v1`.
- Android: `app.autplay`, debug 1.0.5, version code 18.
- APK SHA-256: `6c5898b10d0db2f9c9f467cb4768922a68cbf6e8447105ad27abcda5ac7a3260`.

The seven active server containers were updated from their exact installed image
identities. The Admin overlay changes fourteen allowlisted files; other images
change four shared application/model/readiness files and the new migration.
Environment, commands, non-root users, groups, mounts, ports, networks and restart
policies were preserved. The new image label is explicitly included in the
expected configuration. Unrelated workspace changes were excluded. The CSS URL
uses version 7 to avoid an existing immutable version-6 browser cache.

## Verification and recovery

- 60 selected server tests and 14 Android unit tests passed. Admin HTTP/browser
  checks were rerun after the CSS URL update: 43 passed.
- Ruff, formatting, mypy, Android lint, APK assembly and instrumentation source
  compilation passed. Device instrumentation tests were not run.
- All eight new/recovery image import checks passed. The final Admin template
  check passed, and a disposable PostgreSQL instance upgraded from 0060 to 0061.
- A custom PostgreSQL dump was completed and its restore catalog checked before
  migration. The 1,048,783,877-byte backup remains host-local, mode 0600. Its
  SHA-256 and schema baseline are recorded in the backup receipt.
- The migration adds a nullable administrator-name column and its length check;
  existing device and admission rows were preserved.
- All seven updated services are running; every service with a configured health
  check is healthy. Deployed file hashes match the reviewed artifacts.
- The live read projection excludes all eleven existing revoked device rows.
  The new admission model loads against the migrated database. Pending forms
  render in RU/EN with the name field and no locator input.
- Private HTTPS login, all four connection-page assets and signed mobile discovery
  return 200 with certificate validation enabled. Asset hashes match. Anonymous
  protected Admin pages redirect to login, and the acquisition agent is online.
- Seven other containers retained their identities and running states; the Vault
  bridge was temporarily quiesced and resumed. Admin autostart remains enabled and
  active, Docker enabled. A physical reboot was not performed.
- The installed APK hash matches the frozen build, and its signing certificate
  matches the prior APK. The first-install timestamp, database inode and row counts
  across all 56 Room tables were unchanged. AP started and remained running.
  Physical camera scanning and a real new-device approval were not performed.

The initial configuration comparison detected Docker's additional image label
and automatically restored the prior behavior using forward-compatible recovery
images. The expected specification was corrected to include that exact label;
the final deployment passed strict configuration comparison.

Original and intermediate recovery containers remain stopped. Recovery images
retain migration 0061 while restoring the prior application behavior, preserving
any administrator names. Do not blindly start an original 0060 container or
downgrade the schema. The retained deployment script's `recover` command uses the
compatible images without deleting production volumes or approved names.

Host deployment scripts, private configuration snapshots, the database backup and
receipts are in `host-deployment-material/device-admission-20261006`. Local frozen
sources, task-only patch, APK, validation and sanitized receipts are in
`local-checkpoints/device-admission-20261006/release`. The local Android
database snapshot is restricted to the operator and SYSTEM and is not included
in the release archive.
