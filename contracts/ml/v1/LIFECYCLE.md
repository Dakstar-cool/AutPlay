# ML lifecycle guards

`server/src/autplay/domain/ml_lifecycle.py` freezes the in-memory transition
graph for Face/Sona lineage work, Sona capture discovery cursors and exact
target dispatch, Face download/backfill, and recommendation serving decisions.
Terminal states cannot reopen. Only `RETRY_WAIT -> PENDING` retries work; a
`CONSUMED` Sona target does not close its still-active source cursor. A serving
decision moves from transaction-local `PREPARING` to one committed P11 or Sona
truth and cannot change afterward.

Stable source, consent, retention or lineage failures become
`TERMINAL_INELIGIBLE`; corrupt output, integrity/contract failure, unsupported
runtime or batch-one OOM become `TERMINAL_FAILED`; GPU busy/timeout, process
death, database loss and lease expiry retry within budget. Exhausting that
budget yields `RETRY_EXHAUSTED`. Unknown reasons fail closed.

These pure guards have focused tests. Future SQL transition functions and
repository mutations must enforce the same graph under row locks; the Python
module alone is not a persistence fence.
