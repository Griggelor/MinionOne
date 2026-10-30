# v0.26 development slice — sealed manager input snapshot

Status: **incremental implementation, not an executable workspace worker**. This
change does **not** increase the package version or change `PRODUCT_READY=False`.

## Implemented

- `SealedWorkspaceInput` reads only the canonical authenticated, admitted
  `workspace_input_blobs` from the manager SQLite database.
- Every request rechecks principal/thread/job ownership, individual content
  SHA-256, manifest digests, filename/casefold constraints, byte quotas,
  `TASK.md` text validity and the canonical aggregate `jobs.input_digest`.
- `minionone_workspace_snapshot(job_id)` is an authenticated MCP JSON-RPC
  metadata tool using this actual manager code path. It returns **only**
  filename/digest/size information, never bytes or manager filesystem paths.
- `read_bytes` is a manager-internal method, **not a worker capability or
  exposed MCP tool**. It deliberately does not read an OS path.
- Disk mutation after sealing does not silently change admitted snapshot
  bytes. This **does not** mean disk has been reverified: the metadata response
  explicitly sets `on_disk_integrity=NOT_RECHECKED`.

## Test coverage

`python -m unittest discover -s tests -p test_workspace_input_snapshot.py -v`

Tests cover exact bytes, changes after seal, per-call ownership checks,
missing jobs, traversal/ADS/network syntax, corrupt SQLite bytes, changed
manifest with updated stored hash and an actual HTTP MCP request.

## Execution gates still outstanding

The new reader supplies **no worker tool authorization**. No provider may call
it directly without trusted execution context and independently checked,
**transactionally current** attempt/epoch/capability. Before any future
`minionone_start_workspace_job`, run `verify_input` against the real pinned
OS input and enforce the parent residency, active attempt, resource and model
eligibility checks. A green snapshot test does not permit a start.

Remaining v0.26 vertical slice: durable workspace lifecycle, job/attempt-scoped
read + CREATE-ONLY write broker, bounded Flint/LM Studio tool dispatcher,
manager-only native artifact publication, recovery, result delivery and explicit
parent ACK. Keep T-163 and the product readiness gate RED until real positive
and negative dispatch are shown on the target Windows system.
