# AutPlay documentation map

Open the row matching the current task, then the relevant section or ADR. Implementation and review
rules live in [CODING_STANDARDS.md](CODING_STANDARDS.md); agent action boundaries live in
[AGENTS.md](AGENTS.md).

| Task | Entry point |
| --- | --- |
| Setup, build and checks | [README](README.md#developers), [full setup](docs/operations/FULL_PROJECT_SETUP.md); exact tool pins and commands live in configuration and scripts |
| Cross-project priorities and production blockers | [Active plan](docs/operations/PRODUCTION_READINESS_PLAN.md) |
| Architecture, server storage and adapter boundaries | [Architecture](docs/design/AutPlay%20System%20Architecture%20v1.md), affected decisions in [ADRs](docs/adr) |
| Media identity and Vault | [Track identity](docs/design/AutPlay_Track_Identity_v1.md), [ER model](docs/design/AutPlay%20ER%20Model%20v1.md) |
| Android persistence, playback and downloads | [Room design](docs/design/AutPlay_Android_Room_Schema_v1.md), [outbox/Journal](docs/adr/ADR-018-standalone-outbox-and-journal-lineage.md), [Media3 ownership](docs/adr/ADR-021-p08-media3-room-playback-download-ownership.md) |
| Sync and wire compatibility | [Sync protocol](docs/design/AutPlay_Sync_Protocol_v1.md), affected schemas in [contracts](contracts) |
| Acquisition, imports and external providers | [Acquisition contract](docs/design/AutPlay_Discovery_Acquisition_Contract_v1.md), [local acquisition tool](tools/local_music_acquisition/README.md) |
| Privacy, authentication and account/device security | [Privacy runbook](docs/operations/PRIVACY_DELETE_EXPORT.md), affected contracts and threat models in [design docs](docs/design) |
| CI gates and release evidence | [CI/release mechanics](docs/operations/CI_RELEASE.md), [workflow definitions](.github/workflows), [release evidence index](docs/release/README.md) |
| Docker and server installation | [Docker standards](CODING_STANDARDS.md#docker), [Compose operations](deploy/compose/README.md), [install and pair](docs/operations/INSTALL_AND_PAIR.md) |
| Deployment, public edge and signing | [Deployment](docs/operations/DEPLOYMENT.md), [PA3 public edge](docs/operations/PUBLIC_EDGE_PA3.md), [signing custody](docs/operations/ANDROID_SIGNING_CUSTODY.md) |
| Backup and restore | [Backup/restore](docs/operations/BACKUP_RESTORE.md) |

## Historical evidence

`docs/implementation/`, `docs/build-pack/`, old handoffs and dated release reports are historical
material. Open only the exact requirement or proof needed by the current task or active plan.
Historical prompts and retired automation do not initiate new work. Current status belongs to the
active plan; exact run identity belongs to the evidence it cites.
