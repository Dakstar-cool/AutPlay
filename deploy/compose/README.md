# Disposable PostgreSQL, CPU runtime and optional P12 GPU profile

The base Compose file runs exactly one PostgreSQL 18 service with pgvector and publishes no host port. Its named volume is scoped by the Compose project and is intentionally disposable. `compose.test.yaml` is used only by the canonical check scripts: it assigns a random loopback-only host port so real migration, auth, job, and Vault crash/concurrency tests can connect, then the scripts remove the exact project container, network, and volume and verify that none remain.

`compose.runtime.yaml` adds the `runtime` profile: one-shot Alembic migration followed by separate API, CPU-worker, and direct-stream processes built from the same non-root CPU image whose base is digest-pinned. The worker image pins FFmpeg/FFprobe and Chromaprint/fpcalc, but the isolated stream process imports no worker or media-tool code and receives the Vault volume read-only. API and worker share the same writable Vault volume so staging and final publication remain in one filesystem atomicity domain. Healthchecks and ingest require no GPU or external Internet access. Both published ports default to loopback-only. The root allowlist `.dockerignore` limits the build context to the server lock, package source, and migration inputs so workspace secrets and caches are never uploaded to the builder.

Before parsing the runtime overlay, set `AUTPLAY_RUNTIME_AUTH_SECRET_FILE` and
`AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE` to two different local files outside the
repository, each containing at least 32 random characters. The second key is dedicated to PA2
source-token HMACs and must never equal the access-token signing key. Compose mounts both read-only;
do not place a real credential in YAML, source control, shell history, or logs. Native Linux Compose
implements a `file:` secret as a bind mount and cannot remap its UID/GID. Keep the parent directory
owner-only (`0700`) and make the secrets readable by the non-root container UID (for a
single-operator disposable host, `0444` inside that closed directory); production secret delivery
remains a separate deployment decision.

```text
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime up --build --wait
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime down --volumes
```

To run a loaded release archive instead of rebuilding from the checkout, set the exact image tag
and add `compose.release.yaml`. This overlay removes every server `build` section; `--no-build`
fails closed if the loaded image is unavailable. The PostgreSQL and Vault volumes remain disposable
and project-scoped. The override uses Compose `!reset` and therefore requires Docker Compose 2.24.4
or newer:

```text
docker load --input autplay-server-v0.4.0.docker.tar.gz
AUTPLAY_SERVER_IMAGE=autplay-server:v0.4.0
AUTPLAY_RUNTIME_AUTH_SECRET_FILE=<local secret file outside the repository>
AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE=<different local source-HMAC secret file>
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml -f deploy/compose/compose.release.yaml --profile runtime up --no-build --wait
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml -f deploy/compose/compose.release.yaml --profile runtime down --volumes
```

For the `v0.4.0` trusted-LAN development release, prefer the packaged server installer instead of
assembling this command by hand. It verifies the image archive, platform, revision and exact image
tag, creates persistent secrets and the P-256 identity outside the extracted bundle, and always
applies `compose.release.yaml` last. See
[`docs/operations/INSTALL_AND_PAIR.md`](../../docs/operations/INSTALL_AND_PAIR.md). The installer
does not configure TLS, public exposure, backups or production credentials.

For an explicitly trusted LAN, set `AUTPLAY_RUNTIME_BIND_HOST` to the laptop's concrete LAN IPv4
address before running the same Compose command. Do not use `0.0.0.0`: binding a specific address
keeps VPN and other host interfaces out of scope. Restrict the host firewall rule to the two
published TCP ports and the local subnet. This development profile serves plain HTTP, so it must
not be exposed to an untrusted Wi-Fi network or the public Internet; those topologies require the
production TLS edge and deployment decisions.

`autplay_dev_only` is a fixed disposable development credential, not a deployable secret. These files are not production manifests and must never be pointed at real/user data. Production database roles, TLS/domain topology, secret delivery, backup/restore, and public networking require their owning later phase and explicit deployment approval.

## PA3 public-edge candidate (blocked from WAN)

`compose.public-edge.yaml` is the locally qualified PA3 production-topology candidate. It must be
layered after `compose.admin-local.yaml` and before `compose.release.yaml`. The overlay replaces the
development PostgreSQL credential with two file-backed inputs, disables Admin Web, removes raw
mobile API/stream host ports and publishes only IPv4 TCP 443 through the digest-pinned Caddy edge.
The two database files contain the same generated password in their respective PostgreSQL and
SQLAlchemy formats; keep them outside the repository under an owner-only directory.

Required inputs:

```text
AUTPLAY_RUNTIME_POSTGRES_PASSWORD_FILE=<private PostgreSQL password file>
AUTPLAY_RUNTIME_DATABASE_URL_FILE=<private postgresql+psycopg URL file>
AUTPLAY_RUNTIME_AUTH_SECRET_FILE=<private access-token secret file>
AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE=<different source-HMAC secret file>
AUTPLAY_RUNTIME_ADMIN_SOURCE_SECRET_FILE=<different local Admin source secret file>
AUTPLAY_RUNTIME_ADMIN_CSRF_SECRET_FILE=<different local Admin CSRF secret file>
AUTPLAY_RUNTIME_PROFILE_IDENTITY_KEY_FILE=<persistent P-256 identity PEM>
AUTPLAY_RUNTIME_PRIVACY_LEDGER_KEY_FILE=<persistent deletion-ledger encryption key file>
AUTPLAY_RUNTIME_TRAINING_CONSENT_LEDGER_KEY_FILE=<persistent consent-ledger encryption key file>
AUTPLAY_PRIVACY_LEDGER_KEY_ID=<stable deletion-ledger key identifier>
AUTPLAY_TRAINING_CONSENT_LEDGER_KEY_ID=<stable consent-ledger key identifier>
AUTPLAY_ACME_EMAIL=<operator certificate-expiry contact>
```

The two ledger keys and their identifiers belong to persistent production state. Back them up with
the corresponding named volumes and never rotate or replace them as part of an ordinary image
upgrade. On the first start, migrate PostgreSQL and initialize each empty independent ledger before
starting either API. Both initializers are idempotent for the exact existing key and fail closed for
an incompatible ledger:

```text
docker compose <the five -f arguments below> --profile runtime up -d --wait postgres
docker compose <the five -f arguments below> --profile runtime run --rm migrate
docker compose <the five -f arguments below> --profile runtime run --rm --no-deps privacy-ledger-init
docker compose <the five -f arguments below> --profile runtime run --rm --no-deps training-consent-ledger-init
```

The CPU worker also fails closed until the target server has reviewed resource-report v1 and joint
internal-I/O report v3 budgets. Start `mobile-api` and `admin-init` first to persist or verify the
server identity, collect and review the target measurements, and apply them with
`autplay-admin resource-budget-initialize` and `autplay-admin internal-io-budget-apply`. Do not use
synthetic test values in a persistent deployment. The report format and command inputs are defined
in [`docs/design/AutPlay_Resource_Measurement_Report_v1.md`](../../docs/design/AutPlay_Resource_Measurement_Report_v1.md).
Only after both commands succeed should the complete runtime be started.

Render and validate before any host mutation:

```text
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml -f deploy/compose/compose.admin-local.yaml -f deploy/compose/compose.public-edge.yaml -f deploy/compose/compose.release.yaml --profile runtime --profile public-edge config --quiet
docker run --rm --network none --read-only --tmpfs /config --tmpfs /data -e AUTPLAY_ACME_EMAIL=operator@example.test -v ./deploy/compose/Caddyfile.public-edge:/etc/caddy/Caddyfile:ro caddy:2.11.4-alpine@sha256:98eb57d882ccd5213d1688764db10c1ca2c58a1ca3a6717a3411ad798f7a423a caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

This candidate is not authorization to start Caddy or expose TCP 443. PA3 remains blocked until an
encrypted off-host generation is restored in isolation and a stable Android signer/update path is
accepted and proven. WAN Wave stays blocked at the edge. The activation, evidence and rollback
procedure is in [`docs/operations/PUBLIC_EDGE_PA3.md`](../../docs/operations/PUBLIC_EDGE_PA3.md);
Android key custody is defined separately in
[`docs/operations/ANDROID_SIGNING_CUSTODY.md`](../../docs/operations/ANDROID_SIGNING_CUSTODY.md).

## Optional loopback administrative Web

`compose.admin-local.yaml` keeps the server-rendered administrative Web in the existing API process
at `http://127.0.0.1:8787/admin`. It also adds a separate admin-disabled API process for an Android
debug client. Both endpoints use the same database, Vault, signing secret and persistent server
identity, while only the mobile API and stream may bind to a concrete trusted-LAN address. Create
two different random HMAC secret files and one persistent P-256 private-key PEM file outside the
repository, then include the overlay after the normal runtime files:

```text
AUTPLAY_RUNTIME_ADMIN_SOURCE_SECRET_FILE=<local source-HMAC secret file>
AUTPLAY_RUNTIME_ADMIN_CSRF_SECRET_FILE=<different local CSRF-HMAC secret file>
AUTPLAY_RUNTIME_PROFILE_IDENTITY_KEY_FILE=<persistent local P-256 private-key PEM file>
AUTPLAY_MOBILE_BIND_HOST=<concrete trusted-LAN IPv4>
AUTPLAY_MOBILE_API_PORT=18787
AUTPLAY_MOBILE_STREAM_PORT=18788
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml -f deploy/compose/compose.admin-local.yaml --profile runtime up --build --wait
```

The overlay's one-shot `admin-init` process waits for the mobile API and initializes or verifies the
server-instance identity through the signed pairing-discovery boundary. Reuse the same private-key
file for the lifetime of the PostgreSQL data. Replacing it while retaining the database fails
closed because persisted public evidence no longer matches. An intentional configured origin
change keeps the same application identity, updates the capability revision, and requires explicit
confirmation in an already paired client. Restrict the firewall to the selected Wi-Fi interface,
the two mobile ports, and `LocalSubnet`; never use `0.0.0.0` or expose this HTTP debug topology to
the Internet. The overlay derives both signed origins from the exact bind address and published
ports, so they cannot drift independently. Cancel any active Android enrollment invitations before
an intentional origin change and issue new invitations afterward; old snapshots fail closed.

The first account remains an intentional, locally CLI-created `OWNER`; the accepted authentication
contract forbids an implicit default account or permanent browser login. On a clean database, run
the one-time bootstrap from an attached local terminal before requesting browser access, and
protect the token-bearing JSON it prints:

```text
docker compose -p <project> -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml -f deploy/compose/compose.admin-local.yaml --profile runtime exec -T api autplay-admin bootstrap-owner --display-name <name> --device-name <server-machine> --platform OTHER --app-version <version>
```

This command fails closed once any account exists. The created account is `ACTIVE OWNER`, so the
server machine always retains the supported local CLI bootstrap/recovery path without a network
login or a reusable default credential. Browser authority is intentionally separate: issue a
five-minute one-time bearer from an attached local terminal, using the `user_id` retained from the
first owner bootstrap output:

```text
docker compose -p <project> -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml -f deploy/compose/compose.admin-local.yaml --profile runtime exec -it api autplay-admin web-session-invite --user-id <owner UUID>
```

Do not redirect, record or place the printed bearer in shell history. Open `/admin/login` and enter
it only in the masked form. The browser session is an HttpOnly loopback development cookie with a
30-minute idle and 12-hour absolute lifetime; the overlay does not create an implicit or permanent
administrator session. The administrative API publication is always literal `127.0.0.1`; the
separate mobile API contains no administrative Web routes. Cleartext admin Web remains forbidden
outside literal loopback.

### Optional Admin backup control

`compose.backup-control.yaml` adds the owner-only `/admin/recovery` control page without granting
the API process access to Docker, SSH, raw disks or arbitrary filesystem paths. The operator
registers one to 32 opaque target IDs and labels; the browser can select only those IDs, set the
hard per-generation byte cap and choose a 50–99% warning threshold. The API writes bounded
`policy.json`, `request.json` and agent-owned `status.json` documents through a private spool.

Prepare a host directory writable by the non-root API UID and the separately authorized agent,
then add the overlay after `compose.admin-local.yaml` (or the equivalent private-HTTPS Admin
overlay):

```text
AUTPLAY_RUNTIME_BACKUP_CONTROL_ROOT=/srv/autplay/operator/backup-control
AUTPLAY_ADMIN_BACKUP_TARGETS_JSON=[{"id":"workstation-usb-e","label":"External USB E","kind":"external-agent"}]
docker compose <normal -f arguments> -f deploy/compose/compose.backup-control.yaml config --quiet
```

Target registry entries deliberately contain no path. `kind` is either `external-agent` or
`mounted`; the approved agent maps the opaque ID to a local USB/NAS destination. Requesting a
backup from Web creates no subprocess. Run the agent separately with its explicit destination,
target ID and shared remote spool. It claims the policy limit, streams directly to the destination,
updates Admin Web every 256 MiB, emits an assertive alert at the configured threshold and stops
before crossing the hard cap:

```powershell
server\.venv\Scripts\python.exe scripts\admin_target_external_backup.py `
  --destination-root E:\AutPlayBackups `
  --target-id workstation-usb-e `
  --remote-control-root /srv/autplay/operator/backup-control `
  --execute
```

For a one-off run before the Web control spool exists, omit the two control arguments and supply
`--max-backup-bytes` plus optional `--warning-percent` explicitly. The agent refuses `--execute`
without a hard maximum.

P12 adds `ml-gpu` under the opt-in `gpu` profile. It is built from `gpu/Dockerfile`, publishes no
port, has no API/CPU dependency edge, reads Vault and pre-provisioned private model-cache bytes
read-only and writes only PostgreSQL derived state. The normal `runtime` command does not build or start it. On an
NVIDIA host enable both profiles:

```text
AUTPLAY_GPU_DEVICE_SELECTOR=auto
AUTPLAY_GPU_MODEL_ID=<reviewed registry UUID>
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime --profile gpu up --build
```

`AUTPLAY_GPU_DEVICE_SELECTOR` accepts `auto`, `uuid:<GPU UUID>`, `pci:<PCI bus ID>` or `index:<n>`.
Use `scripts/test-p12-gpu.ps1` or the isolated Linux Docker gate
`scripts/test-p12-gpu.sh` to list and exercise the selection before starting the service. P12
contains the pinned ONNX Runtime CUDA process but no approved weights. It therefore exits
fail-closed with a stable configuration/artifact error before claiming work until an eligible
registry model and hash-addressed artifact are supplied. Process restart is bounded to three
failures. This does not change API, stream, playback, ingest or CPU-worker health. The current RTX
3060 12 GB is benchmark hardware, not a Compose or application requirement; `auto` evaluates the
detected GPU capabilities and explicit UUID/PCI/index selectors support multi-GPU and upgraded
servers.
