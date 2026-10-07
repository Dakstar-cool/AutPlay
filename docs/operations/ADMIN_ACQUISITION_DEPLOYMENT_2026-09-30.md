# Private Admin acquisition control deployment

The owner-only `/admin/acquisition` page and operator acquisition agent were activated on the
private target on 2026-09-29 21:54 UTC (2026-09-30 MSK). The Admin Web container alone changed;
the mobile API, CPU workers, PostgreSQL schema, Vault and public edge were not changed.

This is a target hotfix built on the exact previously running Admin image. The base image,
overlay archive, resulting image, agent source, running container and retained rollback container
are identified in [the deployment receipt](ADMIN_ACQUISITION_DEPLOYMENT_2026-09-30.json).
The overlay's individual file hashes are in
[the source manifest](ADMIN_ACQUISITION_OVERLAY_MANIFEST_2026-09-30.json). The source commit in
the receipt identifies the repository baseline; the overlay includes uncommitted feature files,
whose exact bytes are captured by the manifest and target archive. This is not the final
production release qualification in `PRODUCTION_READINESS_PLAN.md`.

The agent is a persistent user service with an operator-owned Docker launcher. The running Admin
container has supplementary GID 1000, shared with the agent and the private setgid spool.
Command and status files are mode `0660`. Enabled source policy on this target is Jamendo, Hitmo,
YouTube, SoundCloud and Bandcamp. Yandex is not enabled because no operator token was configured.

Verification on the exact target included candidate import/template and route checks, a private
HTTPS request redirected to Admin login, preserved port bindings, fresh agent heartbeat, and a
synthetic Admin-to-agent configuration command that was applied and cleaned up. No provider
download or library import was started. One owner-session review with an authorized TXT/track
job remains the next functional acceptance step.

The previous Admin container is retained stopped as the rollback target. Before any rollback,
check that no acquisition queue is active, stop the agent, restore the retained Admin container
under its original name, and verify the private Admin endpoint. No database migration occurred,
so no schema rollback is required. The target also retains the pre-switch container inspection
and the deployment context archive in its private deployment record.
