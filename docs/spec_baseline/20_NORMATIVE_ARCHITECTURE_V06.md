# ADR-020 — Normative System Architecture and Trust Boundaries (v0.6)

**Decision:** APPROVED FOR IMPLEMENTATION PREPARATION; **not** security verified. **Supersedes** incompatible details of historical docs 00–19. **Owners:** product owner, system architect, security reviewer. **Date:** 2026-10-29.

## 1. Mission and non-goals

MinionOne is a local job manager for bounded LLM delegation. Its v0.6 MVP allows reading explicitly supplied inputs, producing new work files and submitting immutable artifacts. It manages job lifecycle, resource budgeting, logs and parent notification without giving the worker broad execution privileges. Not in MVP: arbitrary code/test execution, general-purpose automation, cloud data sharing, automatic project mutation, unverified chat-resume or model eviction.

## 2. Formal trust boundaries

| Principal/component | Trust/authority | Explicit prohibitions |
|---|---|---|
| Human/operator | Controls real job scope and reviewed integration | Must not treat worker assertions as attestations |
| Parent Flash | Authenticated principal scoped to valid jobs and thread; prepares root/input; reviews artifacts | Cannot grant worker broader tools than admin policy |
| MinionOne manager | Privileged: identity, state, durable DB, audit, outbox, policy enforcement | Cannot silently infer success from worker text |
| Tool broker | Narrow file and event boundary, job/attempt authorization, per-operation limits | No arbitrary user-supplied path or process execution |
| Worker LLM | Untrusted generated text and six narrow tool requests | No OS shell/network, no direct output, no privileged MCP |
| Provider backend | Conditionally trusted transport to a model, not a security principal | Must not relay arbitrary tool calls or hold unscoped secrets |
| Unsloth API | External integration: actual supported capabilities to be verified | MCP API-key not proof of chat-thread write ownership |

**Key rule:** the LLM never receives a shell token, generic file API, network tool or manager credential. Gate all tool requests on the server. Security by prompt alone is rejected.

## 3. Filesystem contract and seal

Only three **visible** folders: `input/`, `work/`, `output/`.

- `input/`: parent-prepared, immutable on admission; worker reads only manager-pinned bytes from a verified snapshot. Includes `TASK.md` listed and hashed in the input manifest. A writable manifest is **not** itself a trust anchor.
- `work/`: worker may list/read and **create** new files. No overwrite, append, rename, delete, chmod or file execution. Any newer version receives a distinct filename; collisions reject instead of replacing.
- `output/`: **no worker read/list/write**. Manager commits snapshots of accepted work files under `output/artifacts/` and writes only one `output/job.log.jsonl`; other manager metadata stays outside the job directory.
- Input and work files are **untrusted content** even when sealed. Publishing code does not authorize running, importing, previewing active HTML, or automatically merging it.

**Windows implementation gate:** lexical path guards are prefilters only. Open root/descendants with validated file/volume identity, reparse-point rejection and race-safe handle-relative semantics; enforce no hardlink escapes/ADS/device paths/casefold or 8.3 aliases. Prevent existing host process/file handles and alternate mount/UNC bypasses. If handle-based confinement cannot be proven, use a separate hardened service boundary or disable the operation. ACLs alone are defense-in-depth, not a substitute for correct handles. Validate the threat model with external-user processes and File System Filter corner cases on a real Windows machine.

**Atomic write commit:** check capability/status/attempt + reserve cumulative work, publication and event quota in a transaction; write bounded temporary bytes under secure target directory; flush; no-replace commit into a previously nonexistent canonical filename; record digest+receipt once. On crash/IO failure, reconcile temporary bytes and receipt from authoritative store; never overwrite on retry. Atomicity of cross-DB-and-filesystem effects is achieved by explicit recovery protocol, not a fictitious single OS transaction.

## 4. Non-negotiable invariants

| Invariant | Decision owner | Boundary |
|---|---|---|
| Job principal/thread ownership | Auth service | Every parent control and read request |
| Job registration and immutable input seal | Job manager | Before returning durable `job_id` |
| Worker capability and job/attempt scope | Tool broker + policy store | Every operation, including retries |
| File creation uniqueness and byte quota | File broker | Exclusive commit / CAS reservation |
| Result publication digest | Artifact publisher | After source snapshot verified |
| State version and active attempt | State machine | Persisted CAS transition |
| Inbound worker status is untrusted | State machine | Before any lifecycle update |
| Outbox and delivery receipt | Outbox service | Before `DELIVERED` |
| Integration acknowledgement | Authorized parent/operator | After verified integration, never worker self-report |
| Visible log content/order | Audit service | Journal transaction and replay |
| Resource/parent residency | Resource planner | Before every model load/unload |

`COMPLETED` requires worker `DONE`, verified result digest, acceptable parent restore, delivery and authenticated integration ack. If actual integration semantics cannot be proven, the job remains `RESULT_READY` / `INTEGRATING` instead of fabricating `COMPLETED`.

## 5. Logs and privacy

`output/job.log.jsonl` is a user-readable projection **derived from** privileged journal events. All worker and manager actions should have outcome/receipt/size/digest/error metadata. Do **not** log raw task data, prompts, model reasoning, credentials or arbitrary worker prose by default; safe progress labels are typed/summarized. Every row includes journal sequence, event/actor, attempt, status, operation ID, UTC timestamp and redacted metadata. Redact **before** making the projection; log projection is not the place to store secrets that could later be filtered. A non-authorized host user must not gain access merely because files are in a job folder; real owner/ACL checks remain necessary for Flash reading the output.

If DB commit succeeds but log append fails, the API returns journal-confirmed events, the projector restarts from last verified cursor. On partial/torn log file, manager may atomically replace a rebuilt projection, and must document that manager/admin integrity is out of scope. The worker cannot influence log sequence/actor/false terminal status.

## 6. Fail closed and release modes

Local-only, artifact-only, `parent_unload=forbid`, no cloud by default. A wrong/unknown provider identity, insecure file broker, missing thread binding, inconclusive cancellation, untrusted checkpoint or insufficient resource capacity must disable the corresponding action. Capability expansion requires versioned ADR, authenticated approval and new independent tests.

**No claim** that Unsloth's MXC sandbox applies to arbitrary MinionOne MCP handlers. Its documented OS sandboxes protect Unsloth-controlled code execution; MinionOne must independently verify the integration boundary.
