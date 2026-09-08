# R1B checkpoint 5 shadow-runtime evidence

This directory records a synthetic, permanently quality-ineligible proof of the Sona-Lite shadow
execution boundary. It is not model-quality evidence and cannot authorize serving or R1C.

The source-only archive was hash-verified before extraction on `user@ii-rtx3060`. The isolated
worker loaded the checkpoint-4 content-addressed ONNX bytes and exact model/tokenizer identities,
bound only to remote loopback, and passed the full Linux GPU test/type/lint gates. A Tailscale SSH
tunnel connected local loopback to that worker. The real server tokenizer reader, canonical HTTP
gateway and `SonaShadowService` then completed one synthetic request without P11 degradation.

The PostgreSQL proof covers one-time owner-bound attachment, rejection of a combined Sona attach
plus P11 mutation, active-retention immutability, and fixed-order post-expiry detach of temporal then
baseline snapshot FKs while exact persisted P11/Sona evidence remains loadable. No production,
personal, Vault, database or secret data was transferred.

See `run-evidence.json` for immutable hashes, gate counts and the exact synthetic result.
