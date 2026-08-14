# Disposable PostgreSQL development service

The base Compose file runs exactly one PostgreSQL 18 service with pgvector and publishes no host port. Its named volume is scoped by the Compose project and is intentionally disposable. `compose.test.yaml` is used only by the canonical check scripts: it assigns a random loopback-only host port so real migration/invariant tests can connect, then the scripts remove the exact project container, network, and volume and verify that none remain.

`autplay_dev_only` is a fixed local-test credential, not a deployable secret. Neither file is a production manifest, and neither may be pointed at real/user data.
