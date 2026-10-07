# Private Admin Web mobile update and boot activation

On 2026-09-30 UTC, the private Admin Web was updated to image
`autplay-admin-acquisition:20260930-a17a456-v2` (image ID
`sha256:a8142f684974b944a2e2d67342cd59422dcebdd594d32f53e5d6f3583a6d5811`).
This image layers six UI files over the previously deployed acquisition Admin image.
The new running container ID begins `c59bce611602`; the previous acquisition Admin
container is retained stopped as `autplay-production-tailnet-admin-rollback-mobile-20260930`.
No database migration, download job, public edge, or other application container changed.

The page now places queue status first, uses a two-column desktop form grid and a single-column
phone layout, exposes jump links, collapses concurrency controls, labels mobile table rows,
and provides larger mobile action targets. A 390 px browser rendering had no page-level
horizontal overflow. This screenshot used synthetic queue data; an authenticated owner should
review the live page on a phone.

The operator-owned script `deploy/operations/ensure-private-admin-web.sh` is installed at
`/srv/autplay/operator/admin-web/ensure-private-admin-web.sh`. The user systemd unit
`deploy/operations/autplay-private-admin-web.service` is installed under
`~/.config/systemd/user/`, enabled, and active. It waits for Docker, starts the exact private
Admin container if necessary, and checks its local CSS endpoint. The host Docker service is
enabled, the container retains `unless-stopped`, and operator user linger is enabled. A host
reboot was not performed; startup was verified by restarting the unit and checking its status.

After promotion, the private HTTPS acquisition route returned the expected unauthenticated
login redirect, the served CSS digest matched the new image, the acquisition agent remained
active, and the disposable candidate container was removed. The target deployment context,
snapshot, promotion script, and state are stored privately under
`host-deployment-material/admin-web-mobile-20260930`. The earlier deployment receipt is
`ADMIN_ACQUISITION_DEPLOYMENT_2026-09-30.json`.

For operator checks: `systemctl --user status autplay-private-admin-web.service`,
`systemctl --user restart autplay-private-admin-web.service`, and
`journalctl --user -u autplay-private-admin-web.service -n 50 --no-pager`.
