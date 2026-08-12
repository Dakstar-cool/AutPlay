# P03 - Server Runtime, Identity and Job Foundation

Выполни только phase P03. Следуй common protocol и прочитай `HANDOFF_P02.md`.

## Цель

Создать production-shaped, CPU-only FastAPI/worker foundation с безопасной конфигурацией, health/readiness, structured logging, owner/device sessions и PostgreSQL job lease skeleton.

## Inputs

- System Architecture sections on modules, deployment, jobs, security and observability
- Product specification auth/security/background requirements
- PostgreSQL schema and P02 mappings

## Scope

1. Typed configuration with explicit environment/profile precedence and startup validation.
2. FastAPI application factory, API version prefix, stable error envelope and request ID.
3. `/health/live` and component-appropriate `/health/ready`.
4. Structured redacted logs and minimum metrics hooks.
5. Personal-server owner bootstrap designed safely for local setup.
6. Password path with Argon2id if password login is enabled.
7. Access/rotating refresh token device sessions, revoke and object ownership checks.
8. CPU worker process with PostgreSQL job claim, lease, heartbeat, retry/backoff, cancel and recovery skeleton.
9. Application transaction boundary and clock/ID ports.
10. Compose commands for API and CPU worker without GPU runtime.

## Constraints

- No public self-registration unless explicitly approved.
- No real email/OAuth provider.
- Tokens/secrets never stored or logged in plaintext.
- External providers, Vault ingest and sync endpoints remain out of scope.
- API readiness does not depend on GPU or external Internet.
- Job queue remains PostgreSQL based.

## Required tests

- config missing/invalid/secret redaction;
- liveness without DB and readiness behavior with DB failure;
- stable error/request IDs;
- password hash verify and safe parameters;
- refresh rotation, replay/revoke and cross-user authorization;
- concurrent job claim exactly once;
- expired lease recovery, heartbeat, retry limit and cancellation;
- CPU import/start test fails if CUDA dependency enters core path.

## Acceptance

API and CPU worker start from clean locked environment, migration check passes, tests green, and no feature endpoint pretends to work with placeholder data.

Create `HANDOFF_P03.md`, update evidence and stop.
