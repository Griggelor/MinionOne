# v0.22 – native Windows CREATE-ONLY `work/` probe (not a release)

## Architecture boundary

This milestone adds `WindowsWorkCreateProbe`, an isolated native NTFS experiment, **not** an MCP tool and **not** an enabled worker file broker. The native input reader from v0.21 remains separate. Both open a trusted operator-configured project root, verify its file identity, and walk individual `.minionone/jobs/JOB-…/work` directory components through already held native parent handles.

The write experiment uses `NtCreateFile` with `FILE_CREATE` (Windows CREATE_NEW semantics) for exactly one validated flat filename; it does **not** open existing files for writing. It checks the new handle's volume ID, FileId, regular-file type, link count, reparse flag and length, writes bounded bytes using `WriteFile`, then calls `FlushFileBuffers`. On a failure the session is poisoned. No file is ever reported as published; only `name`, `size`, `sha256`, `identity_verified_on_open_handle` and `published: false` are returned.

## Evidence on CI

Windows runner tests validate: successful small/empty/multichunk writes; collision with existing file; existing symlink/hardlink targets; Windows reserved names, ADS, UNC, traversal and cross-job attempts; directory-reparse rejection; directory rename resistance while anchored; type/size/count quotas; invalid job ID; and closed/poisoned session behavior. `tests/test_win32_work_create_probe.py` runs in `.github/workflows/windows-work-create-feasibility.yml` and is **runner feasibility evidence only**, not target host approval.

## NOT implemented – mandatory gates before worker use

1. **No atomic publication.** Failed or force-terminated writes can leave partial visible `work/` files. A crash-safe, manager-owned journal, staging and publication protocol is required, along with partial-file quarantine/cleanup **without untrusted path resolution**.
2. **No durable quota/fencing.** Limits are scoped to one writer instance and the visible directory file count; a crashed/restarted writer or independent process can bypass session byte totals. Worker-wide durable counters and concurrency fencing are still required.
3. **No target-host ACL evidence.** CI runners do not prove volume type, target ACLs, antivirus/filter drivers, rename/file-swap stress, disk full, kill/restart, loss of access or reparse/ACL changes in the deployed environment.
4. **No `output/` manager publisher**, result integrity or Parent-ACK on real folder jobs. Neither `input/` nor `work/` is exposed to Flint, Unsloth or any cloud provider.
5. **No Windows input seal/admission.** The product continues to reject that path fail-closed. The target workflow contract T-163 remains **FAIL_TARGET_CONTRACT**.

### Gate decision

**PRODUCT_READY=false / G1b BLOCKED / NO-GO.** This proof is a component-level increment only. A successful Windows GitHub run must never be interpreted as real worker file permissions or an end-to-end system test.
