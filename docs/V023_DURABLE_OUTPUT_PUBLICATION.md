# v0.23 – Manager-only durable output publication (POSIX development seam)

This milestone implements a **durable publication journal**, but does **not** enable a workspace worker, native Windows output access or a real system run.

## Implemented

- The **trusted manager**, not Flint, stages immutable candidate bytes into SQLite (`workspace_publications`) under a previously admitted Job-ID. Unique `(job_id,name)` and `(job_id,operation_id)` enforce idempotency. Aggregate per-job quotas are durable across restarts: 16 files, 4 MiB total, 1 MiB per file.
- A POSIX-only publisher enters the real job's `output/` using the existing descriptor-relative, identity-checked directory walker. It uses a deterministic hidden `.pending-…` name with `O_CREAT|O_EXCL|O_NOFOLLOW` and fsyncs the content before the final filename becomes visible.
- An atomic hardlink creates the final name **without replacing an existing file**. It fsyncs the directory and removes the pending alias; a successful publication updates the SQLite receipt from `PENDING` to `PUBLISHED`. Concurrency is serialized on the opened output directory with POSIX `flock`.
- If the process stops before publication, the final filename does not exist. If it stops after the link, a restart rechecks immutable bytes, hashes, inode and link count, removes the pending alias when valid, and records `PUBLISHED`. An untrusted, partial or inconsistent file blocks publication **fail closed**.
- Unit tests cover crash injection at three transition points; restart; damaged journal payload; preexisting target; symlink; deleted published target; name/byte/count quotas; duplicate operations; concurrent retries.

## Security and product limits

1. **POSIX-only.** Windows input seal and native manager output writer are still disabled. The separate Windows `input/` and `work/` probes are not activated as worker capabilities.
2. **Trusted-manager Python API only.** `WorkspacePublisher.stage()` accepts synthetic candidate bytes from manager code. No MCP exposure, worker-produced artifact verification, real Flint invocation, model profile selection, automatic Chat/Unsloth wakeup, or parent ACK is implemented here.
3. The POSIX output directory is assumed to remain manager-owned; no ACL attestation against same-user malicious processes is included. Crash injection is synthetic; forced kill, disk-full and target-host filesystem certification remain separate gates.
4. A partial pending file is not deleted on mismatch; it requires explicit operator reconciliation. Published files externally deleted or modified cause a hard failure, not silent recreation.
5. This is NOT Windows G1b acceptance, not the target-product end-to-end contract T-163, and not a beta release.

**Product status stays NO-GO.** The next work item is a Windows handle-relative manager publisher with comparable durable journal/fencing behavior, followed by true workspace-model execution and acceptance testing on the target host.
