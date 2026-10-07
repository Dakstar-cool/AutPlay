# Admin Web server QR deployment - 2026-10-06

The approved update was deployed to the existing private Admin Web container at
2026-10-06 07:07:06 UTC. The connection requests page now shows a QR for the
configured mobile API origin, a selectable/clickable server address, a copy
button and the Admin Web address. The dashboard links to device connection.
Android AP adds Scan server QR to its initial personal server connection screen.
The scan continues the existing signed discovery, trust and admission flow.

## Artifact identity

- Source base: `a17a4569ab462f2c650c757d1da433cdd58c0b03` with the reviewed QR overlay.
- New image: `autplay-admin-server-qr:20261006-a17a456-v1`.
- Image ID: `sha256:f44b6ee0a5426ab1ff3da227057b8f5c8109d73f89a034b624ba25cccdb61937`.
- Base image ID: `sha256:4a31596f33116ca28bf17c3dbb0fc6ae0439351639a99730b14d8a00fe785767`.
- Active container: `autplay-production-tailnet-admin`.
- Stopped rollback container: `autplay-production-tailnet-admin-rollback-qr-20261006`.
- Android package: `app.autplay`, debug 1.0.5, version code 18.
- Installed APK SHA-256: `b993ef44906e4d9b6c5a44baf5e6c6ff1de6a46e9311072f1bd2ad5d96d2081b`.

The image inherits the exact running Admin image and changes twelve allowlisted
files: the Admin HTTP adapter, the API composition argument supplying the mobile
origin, renderer, CSS, two locally served QR scripts, license/source notice,
base/dashboard/connection templates and EN/RU catalogs. Unrelated runtime
settings and workspace changes were excluded. Runtime configuration, non-root
user, groups, mounts, networks, ports and restart policy were preserved.
No migrations or production data mutations were initiated.

## Verification and recovery

- 39 selected server HTTP/browser/API tests and 37 Android tests passed.
- The browser-generated QR matrix was decoded by the actual Android ZXing
  scanner. CSP, clipboard copy, EN/RU and desktop/phone widths passed.
- Ruff, mypy, lintDebug, assembleDebug and changed-file whitespace checks passed.
- A disposable non-root image import/template/asset check and isolated candidate
  HTTP/configuration probes passed before promotion. All twelve candidate file
  hashes matched the manifest.
- Live private HTTPS login and all three QR/CSS assets returned 200. Asset hashes
  matched the reviewed artifacts; anonymous connection page access still redirects
  to login. EN/RU templates render the configured addresses.
- The acquisition agent stayed online; thirteen other running containers retained
  their identities and running state.
- The existing startup service recovered the updated container from a stopped
  state. It remains enabled/active, Docker enabled/active, user linger enabled and
  restart policy unless-stopped. A physical server reboot was not performed.
- A55 installation succeeded without clearing application data. The installed APK
  hash matched the frozen build and AP started and remained running. Physical
  camera scan verification is left to the user; decoder behavior has test evidence.

Host deployment material is retained in
`host-deployment-material/admin-server-qr-20261006`. Full secret-bearing Docker
snapshots remain host-local with mode 0600. Rollback uses the retained deployment
script's rollback command and the prior stopped container. No volumes or prior
images were pruned.

Local manifests, APK, task-only diff, screenshots and sanitized receipts are in
`local-checkpoints/admin-server-qr-20261006-094419/release` and
the adjacent `evidence` directory.
