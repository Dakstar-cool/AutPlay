# Release discovery contracts

`v1/` contains the accepted Post-MVP A1A provider-neutral application/Web contract and the additive
A1C automation policy. Contract artifacts do not activate automation: the A1C operator gate remains
false by default and owner policy is separately required.

`v1/automation-command.schema.json` is the strict executable schema for A1C policy, run-now,
select, retry, and ignore mutations. Its owner-derived `operation_id` namespace is shared across
all A1C actions for one owner; divergent reuse fails with `operation_conflict`.

The schemas deliberately omit owner input. Runtime derives owner from the authenticated Web actor
and revalidates it at every application and worker boundary.
