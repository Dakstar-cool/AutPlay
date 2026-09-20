# Measured resource budget report v1

This local-only contract initializes global resource limits and measured ceilings.
It does not activate resource enforcement or certify a deployment's real capacity.
`autplay-admin resource-budget-initialize` is a trusted operator command, not a
browser/device endpoint. The operator must collect and review measurements on the
intended server and explicitly supply the SHA-256 of the reviewed report bytes.

## Report

The report is one UTF-8 JSON object, at most 65,536 bytes. Duplicate/unknown keys,
non-finite values and incomplete path coverage are invalid. It has exactly:

| Field | Meaning |
| --- | --- |
| `version` | Integer 1 |
| `measurement_id` | Canonical UUID identifying the run |
| `server_instance_id`, `identity_epoch` | Exact persisted server identity |
| `environment_sha256` | Digest of the operator-reviewed deployment/hardware/configuration record |
| `workload_sha256` | Digest of the benchmark recipe, fixture and workload configuration |
| `measured_at` | UTC RFC3339 timestamp ending in Z; cannot be later than database time |
| `duration_seconds` | Positive measured steady-state interval, at most 86,400 seconds |
| `sample_count` | Positive number of samples across that interval, at most 1,000,000 |
| `simultaneous_playbacks`, `simultaneous_transfers` | Minimum concurrently active, successful operations sustained across that same interval; not launched or waiting client counts |
| `workload_paths` | Exactly PLAYBACK_CURRENT_NEXT, RANGE_SEEK, DOWNLOAD, UPLOAD, INTERNET_ACQUISITION, A1_ACQUISITION, each once |
| `metrics` | Observed values below |
| `acceptance_maxima` | Operator-reviewed upper bounds for every metric, with the same keys and units |
| `acceptance_minima` | Operator-reviewed positive lower bounds for duration_seconds, sample_count, playback_mib_per_second, transfer_mib_per_second, successful_operations |

The workload recipe records representative path mix, overlap, payloads and codecs.
Playback includes allowed current/next prebuffer and seek/Range. A download-only
experiment cannot establish the shared transfer ceiling. All declared paths must
run in the measured combined workload. The environment record includes relevant
CPU, disk/NAS, network and database/runtime settings. Changing it requires new
review/measurements; matching a supplied hash cannot itself inspect hardware.

Metrics (all values finite and nonnegative):

- `cpu_peak_percent`, `disk_busy_peak_percent`, `network_busy_peak_percent`: peak
  utilization, 0 to 100 percent; the benchmark recipe defines sampling and capacity.
- `disk_read_mib_per_second`, `disk_write_mib_per_second`,
  `network_receive_mib_per_second`, `network_send_mib_per_second`: measured aggregate
  throughput over the steady-state interval, in MiB/s (1 MiB = 1,048,576 bytes).
- `database_p95_ms`, `queue_wait_p95_ms`: p95 database operation/queue-wait latency.
- `queue_depth_max`: maximum observed queued operation count, integer.
- `playback_mib_per_second`, `transfer_mib_per_second`: useful successfully delivered
  playback/transfer bytes per second over the same interval, in MiB/s.
- `successful_operations`, `failed_operations`, `timed_out_operations`: integer
  workload operation outcome counts; failures/timeouts cannot disappear from the run.

All observations must satisfy their supplied maxima and minima. Non-percentage
metrics are bounded at 1,000,000,000. These are encoding bounds, not recommended
performance thresholds. The operator chooses acceptance thresholds for the target.
The command checks report consistency; it cannot prove that measurements are honest
or that a benchmark profile represents future work. Physical target acceptance
remains separate evidence.

## Initialize and replay

Use the CLI help to supply the report path, reviewed report digest, expected
`environment_sha256`, operation UUID, expected global policy revision, desired
playback/transfer limits and measured playback/transfer ceilings. Each must satisfy
`1 <= desired <= ceiling <= sustained simultaneous tested count`.

The transaction locks the exact server identity, shared admission lock, then the
policy row. It validates server epoch and looks up the exact operation receipt
before checking current policy revision. It initializes only when both global
limits, both ceilings and budget evidence are all unconfigured. Defaults/overrides
remain unchanged. Existing configured ceilings cannot be replaced by this command.

The same transaction increments policy revision and writes one audit row whose
primary key is the operation UUID. It stores the request/report digests, sanitized
measurement values, minima/maxima, path coverage and original result. No raw file
path, origin, hostname or arbitrary report text is persisted or printed. A failed
audit insert rolls back the policy. Changed operation reuse is rejected. An exact
retry returns the original result after an uncertain commit, even if later Web
edits changed current policy; it does not reapply the original values. A changed
server identity/epoch cannot replay the old initialization.

Audit retention/integrity uses the existing trusted database-writer boundary;
this contract does not claim database-enforced immutable audit storage. Preserve
report, benchmark recipe and environment records with the operator's backup/evidence
process. Missing input files and validation errors return stable sanitized codes.

No actual deployment report or production initialization has been created by the
implementation tests; their measurements are explicitly synthetic fixtures.

## Joint internal byte-work reports v1, v2 and v3

`internal-io-budget-apply` configures or replaces reviewed internal capacity;
`internal-io-limit-set` changes its live limit within that measured ceiling.
Both are local operator commands with operation UUID and expected policy revision,
atomic audit, and exact replay. Neither supplies an audio TRANSFER permit. The
policy starts unconfigured and denies new internal executions until reviewed.

The joint report is a UTF-8 JSON object with exactly these fields, within the same
65,536-byte limit and duplicate/unknown-key restrictions:

| Field | Meaning |
| --- | --- |
| `version` | Integer 1 for the original eight paths; 2 for metadata-inclusive coverage; 3 for retained shared-training coverage |
| `resource_measurement` | Complete report above, measured during this same combined workload |
| `simultaneous_internal_io` | Sustained active internal operations, integer 1..1,000,000 |
| `workload_paths` | Exactly the eight v1 paths, plus METADATA_ENRICHMENT for v2, plus both retained training paths for v3, each once |
| `worst_permitted_mix` | Boolean true: reviewer verified the recipe covers the most demanding permitted concurrent mix |
| `internal_mib_per_second` | Positive useful internal byte throughput over the joint interval |
| `minimum_internal_mib_per_second` | Reviewed positive minimum throughput |
| `successful_internal_operations` | Positive internal completions, no greater than joint successful_operations |
| `minimum_successful_internal_operations` | Reviewed positive minimum completions |

Internal paths are INGEST_ANALYSIS_PUBLICATION, FINALIZED_STAGING_CLEANUP,
PROVIDER_STAGING_CLEANUP, PROVIDER_SCRATCH_RETIREMENT, ORPHAN_RETIREMENT,
ORPHAN_MISSING_CHECK, VAULT_INVENTORY and DEVICE_UPLOAD_CLEANUP. Internal rates and
counts are at most 1,000,000,000; observations must meet their minima. Version 2
adds METADATA_ENRICHMENT: embedded tags/artwork, audio fingerprints, bounded
provider responses and artwork decoding inside retained trees. Existing v1 reports
cannot admit this path. Once v2 evidence is applied, Python and SQL reject a
workload-version downgrade, including while old metadata receipts remain charged.
Version 3 adds TRAINING_DATASET_CHECKPOINT and TRAINING_ROOT_CLEANUP. It covers the
canonical owner-data inventory/checkpoint workload and worst-case bounded output/input
inventory cleanup while retained training execution consumes the same global slot. Existing
v1/v2 reports cannot admit training, and configured v3 evidence cannot be downgraded. Aggregate
resource metrics and outcome counts include both audio and internal operations.

The recipe must cover expensive hashing, decoding/fingerprinting and publication
at the allowed payload/codec limits, concurrent with audio at its configured
**ceilings**, even when its current limits are lower. A scanner-only or idle run
cannot establish this shared ceiling. Because internal slots are interchangeable,
the reviewer must cover the worst permitted simultaneous mix, not assume the
scheduler reserves separate cheap/expensive slots. The boolean is a reviewed
attestation; software cannot infer physical benchmark coverage from JSON.

Apply takes `--report`, `--reviewed-report-sha256`, `--expected-environment-sha256`,
`--operation-id`, `--expected-revision`, `--limit` and `--ceiling`. It requires
`1 <= limit <= ceiling <= simultaneous_internal_io`; joint tested audio counts must
cover the configured audio ceilings. The exact current server identity/epoch,
nonfuture measurement time, environment/report digests, coverage, minima and
maxima are checked and recorded. Changing the identity invalidates new admission
under the old evidence but does not prevent exact exit acknowledgement.

Limit-set takes operation ID, expected revision and limit. Lowering never closes
active receipts or interrupts their authorized work: they remain charged until
exact durable closure, and new work waits. Concurrent count plus PREPARED insertion
is serialized across WORK, finalized cleanup, metadata, provider maintenance and retained
training under the common admission lock, also enforced by SQL triggers. Expiry and process-stop
requests cannot free capacity. Downgrade refuses configured evidence or any active
internal receipt. Deployment must drain legacy workers and integrate every byte
path before activation; no deployment measurement is supplied by these tests.
