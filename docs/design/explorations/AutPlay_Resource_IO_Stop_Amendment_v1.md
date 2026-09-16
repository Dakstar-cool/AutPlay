# Resource I/O stop amendment

Status: approved by the user on 2026-09-16 for local implementation and verification.
The accepted resource-admission deadline/draining contract incorporates this change.
Production rollout remains outside this authorization.

## Conflict

The current resource contract requires an adapter to terminate by its five-second
permit deadline even on a blocked read/write. It also makes an expired permit
stop charging capacity. A Python thread timeout does not stop its filesystem
work. Process termination improves isolation, but a blocked OS/NAS operation
does not have a universal five-second completion guarantee.

Releasing capacity solely because the deadline passed could admit new work while
the old child still consumes resources or writes the staging file. A request to
kill a process is not evidence that it has exited.

Primary references: [Microsoft TerminateProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess),
[Linux kernel wait queues](https://www.kernel.org/doc./htmldocs/kernel-hacking/queue-waitqueue.html),
[Python subprocess timeout behavior](https://docs.python.org/3/library/subprocess.html#timeout-behavior).
The deadline concerns application cancellation, not physical delivery of bytes
already accepted by the kernel/NAS or the network send buffer.

## Accepted behavior

1. Authorization expires within five seconds of the last successful I/O renewal.
   The parent stops request receive/response send at that deadline and requests
   cancellation of the isolated filesystem/provider child. A late renewal cannot
   revive a stopped operation.
2. Capacity remains charged until the child is confirmed exited. Under an OS/NAS
   hang the slot can remain unavailable beyond five seconds. The system favors
   preserving the quota and staging consistency over granting another slot.
3. Before a child may touch Vault bytes, persist its execution registration with
   the exact permit, activation, actual target and a random owner-run identity.
   No application credentials or database connection are passed to the child.
4. A non-closed execution continues charging the activation, blocks reacquisition
   and conflicting attachment reuse, and prevents permit/operation cleanup.
   Lease or heartbeat expiry alone never proves that filesystem work stopped.
5. Normal cleanup confirms process exit before closing the execution in PostgreSQL.
   If that acknowledgement fails, the slot remains charged and closure is retried.
   A process crash/restart marks unmatched registrations orphaned. A trusted local
   supervisor or operator must verify process death before closing an orphan;
   a missing heartbeat, a PID alone, or a client cancellation is insufficient.
6. During upload, the parent retains the existing upload transaction/row lock
   until the child has exited. It then either runs the precommit authority guard
   and commits, or rolls back. A retry cannot race a surviving writer. Uncertain
   COMMIT outcomes use existing chunk receipts for reconciliation.

The minimal additional persistent object is one execution row per process-backed
permit. It records the fence, owner-run, actual target, lifecycle timestamps and
bounded process identity diagnostics. It has no age-based automatic release.
All scheduling, I/O-count, attachment-draining and cleanup predicates must include
unclosed executions before this mode can be enabled.

## Required proof

- Block receive/send and filesystem workers independently; the parent ceases
  network progress by the local deadline and late renewals cannot resume it.
- Delay or refuse process-exit acknowledgement; no new capacity or same-upload
  writer is admitted until confirmed exit is durably recorded.
- Crash the parent between registration, child start, I/O completion and closure;
  unknown registrations remain charged across a new process run.
- Lose PostgreSQL during renewal and cleanup; permission expires, but unresolved
  process executions remain charged until reconciliation.
- Test normal confirmed stop, fresh retry, chunk replay and policy lowering in
  actual PostgreSQL and a real disposable filesystem.

Process-backed enforcement remains disabled until the durable execution accounting
and cancellation paths above have been implemented and verified together.
