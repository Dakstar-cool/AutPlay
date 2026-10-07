# Admin Web light design deployment — 2026-09-30

The approved light, minimal Admin Web interface was deployed at 17:02:04 UTC
(20:02:04 Moscow time). The running private admin container is
`autplay-production-tailnet-admin`.

## Exact deployment identity

- Source base: `a17a4569ab462f2c650c757d1da433cdd58c0b03`, with the reviewed UI overlay.
- Image: `autplay-admin-light:20260930-a17a456-v1`.
- Image ID: `sha256:4a31596f33116ca28bf17c3dbb0fc6ae0439351639a99730b14d8a00fe785767`.
- Running container ID: `46f6c0681729c4731e71d4ac5749b4b1d822767e0d4184f063ca446b241184f3`.
- Previous image ID: `sha256:a8142f684974b944a2e2d67342cd59422dcebdd594d32f53e5d6f3583a6d5811`.
- Stopped rollback container: `autplay-production-tailnet-admin-rollback-light-20260930`.
- Full sanitized receipt: [ADMIN_WEB_LIGHT_DEPLOYMENT_2026-09-30.json](ADMIN_WEB_LIGHT_DEPLOYMENT_2026-09-30.json).

The image contains ten web presentation files: renderer, CSS, EN/RU catalogs,
base/dashboard/login/section/acquisition templates and the new icon macro.
It inherits the exact running acquisition-enabled image. Runtime configuration,
non-root user, supplementary groups, mounts, published ports and networks were
preserved. No migrations or acquisition jobs were initiated. Twelve other running
containers retained their identities and running state.

The receipt distinguishes the original local review baseline from three reviewed
production baseline overrides. Login and section templates differed only in line
endings. The old production dashboard lacked the optional address block present
in the local baseline; the new template guards that block with a defined/nonempty
mobile origin check.

## Verification

- Before deployment: 62 selected renderer, presentation, acquisition, HTTP,
  browser/security and contract tests passed; Ruff and whitespace checks passed.
- Local visual checks: 88 combinations of page, EN/RU locale and viewport
  (1440, 820, 390 and 320 pixels), without page-level horizontal overflow.
- Candidate: all ten file hashes matched the overlay; modules and templates were
  readable by the existing runtime user; all bundled templates compiled.
- Candidate and live container: EN/RU login pages returned 200 and referenced
  `admin-v2.css?v=5`; the served CSS returned 200 with SHA-256
  `916d45b997977a3953ad4602e6fe8bacb8e33012d753e26092f817818c8d2b59`.
- Protected dashboard/acquisition routes retained their anonymous login redirect.
  The acquisition agent reported online. No production login invitation was minted.

## Boot startup

The existing [startup script](../../deploy/operations/ensure-private-admin-web.sh)
is installed and executable, with normalized LF SHA-256
`94d126998a229801d2972156c6c7fa8b2013e2e0814eac4acb12a45d0d51f1e9`.
The existing `autplay-private-admin-web.service` user unit is enabled and active;
its last result was `success`, with `ExecMainStatus=0`. Docker is enabled and
active, user linger is enabled, and the admin container uses `unless-stopped`.
The acquisition admin agent is also enabled and active. Other AutPlay containers
use `unless-stopped` (the unrelated Open WebUI container uses `always`).

Recovery was exercised after promotion: the new admin container was stopped,
the startup service was restarted, and it started the same container and passed
the host readiness probe. Login, CSS and acquisition agent checks then passed
again. A physical machine reboot was not performed.

## Retained recovery material

The host has a private deployment directory containing the image build context,
deployment script, before/after container snapshots, state and receipt. Full
snapshots remain host-local with mode 0600 because they include runtime secrets.
The previous admin container and all persistent volumes remain available for
rollback. No image pruning or volume removal was performed.

## Build permission correction

An isolated candidate failed before promotion because copied directories had mode
0700 and were owned by root. A second build reused that layer despite normalized
source directory modes. Production was unaffected. The
[Dockerfile reference](https://docs.docker.com/reference/dockerfile/#copy) was
consulted for four compatible approaches: normalize context directory modes,
set `COPY --chmod`, set `COPY --chown`, or copy individual files to preserve the
existing directory metadata. Individual file copies with explicit mode 0644 were
selected. A disposable, network-isolated import/template check under the existing
runtime user passed before the successful candidate was started.
