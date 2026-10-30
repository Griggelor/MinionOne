# v0.25 – Native Windows input seal and canonical job admission (development)

## Implemented boundary

A *manager-only* Windows staging path now uses the native Windows input broker from v0.21 to admit the **same manager-reserved JOB-ID** that was returned by `minionone_prepare_workspace`.

1. The manager validates the operator-configured project identity and opens the nested `.minionone/jobs/<JOB-ID>/input` chain with held NTFS directory handles. It rejects reparse-point directories, unexpected volumes, invalid names, alternate data streams, symlinks, hardlinks and invalid file types.
2. Before admission, the manager enumerates `input/`, opens **all input files** by one-component, relative native handles, and retains those handles until the canonical SQLite transaction has committed. Windows `FILE_SHARE_READ` on those handles denies new conflicting writes/deletes while the input snapshot and admission transaction run.
3. The input format matches the established POSIX contract: exactly `TASK.md`, `manifest.json`, and optional flat files; duplicate JSON keys rejected; SHA-256 for all declared file bytes; UTF-8 nonempty task; 16 declared files max, 32 KiB manifest, 64 KiB task, 1 MiB per other file, 4 MiB aggregate.
4. The verified bytes and hashes are inserted into `workspace_input_blobs`, the canonical `jobs` row becomes `ACCEPTED` version 1, the original reservation becomes `ADMITTED`, and a redacted `admission_events` event is recorded under the same Job-ID. Repeating the same `operation_id` returns the same receipt.
5. An **admission-only** manager log projector writes the redacted `ACCEPTED` event to `output/job.log.jsonl` using handle-relative, fsynced pending-file creation followed by native no-replace rename. An existing log must match exactly. No mutable Windows running-job log semantics are implied.
6. The MCP tools `minionone_prepare_workspace`, `minionone_seal_workspace`, `minionone_verify_workspace_input`, and `minionone_workspace_status` retain their existing signatures and principal/thread scoping. The Windows seal is enabled only at the development admission boundary.

## Testing

The dedicated Windows workflow `.github/workflows/windows-workspace-seal-admission.yml` runs tests for canonical admission and the actual MCP prepare/seal/verify path; idempotency, concurrent seals, root substitution, live input rename resistance, post-seal mutation detection, malformed/duplicate/oversized manifests, empty/oversized tasks, hardlink and reparse rejection, journal integrity, and no worker start. Test results are **Windows runner evidence**, not target-host approval.

## Explicitly still blocked

- **No Flint/Unsloth worker launch**, model profile, broker tool dispatcher or output-artifact pickup. Worker file tools remain absent; status `workspace_execution_enabled=false` and `windows_file_broker_verified=false`.
- The pinned Windows seal is not a proof of durable on-disk immutability after handles close. The sealed snapshot is the source of truth and `verify_workspace_input` rejects changes; a future worker start must require verified pinned bytes and no host-path worker access.
- No deployed NTFS/ACL/adversarial target-host evidence, no proof against malicious same-user host processes or filter drivers, and no live interruption/disk-full/AV test. G1b remains **BLOCKED**.
- The output admission log can project the single ACCEPTED event; general worker-state log updates and parent ACK still need a durable, native manager-only solution.
- Parent identity is still the configured local MCP token/thread mapping, not verified Unsloth chat attestation.

**PRODUCT_READY=false / NO-GO.** GitHub runner success confirms only this component. The next development task is to connect the admitted bytes to a constrained, deterministic worker-job lifecycle with true provider execution and target-host security tests.
