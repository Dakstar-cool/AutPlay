# Disposable PostgreSQL development service

This Compose file runs exactly one PostgreSQL 18 service with pgvector. Its named volume is scoped by the Compose project and is intentionally disposable. The canonical smoke command in the root [`README.md`](../../README.md) verifies versions and removes the container, network, and volume.

`autplay_dev_only` is a fixed local-test credential, not a deployable secret. The service publishes no host port and is not a production manifest.
