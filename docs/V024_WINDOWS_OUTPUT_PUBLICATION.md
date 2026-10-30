# v0.24 – native Windows manager-only durable output publication

## Scope and guarantees

`WindowsOutputPublisher` is an internal, **trusted-manager-only** component. It shares the canonical `workspace_publications` SQLite table introduced in v0.23 and accepts only a preapproved, already admitted job; it is not exposed to MCP or any Worker.

- Manager-pinned bytes and SHA-256 are registered by `stage()` in SQLite before publication. Name, operation ID, job ID and aggregate byte/file quotas remain durable.
- For Windows, the publisher anchors the approved project directory, validates the root FileId and traverses `.minionone/jobs/JOB-…/output` with held relative NTFS handles, denying reparse-point components and volume changes.
- It creates `pending-<digest>.part` with `NtCreateFile(FILE_CREATE)`, writes bounded bytes through `WriteFile`, calls `FlushFileBuffers` and verifies the complete content by an opened native handle.
- The manager then uses `NtSetInformationFile(FileRenameInformation)` with `ReplaceIfExists=false` and a held output-directory handle. A different existing final output is never overwritten.
- `BEGIN IMMEDIATE` on the manager SQLite journal serializes concurrent publishers across processes; the database receipt is committed only after the final name becomes visible and verifies correctly.
- A restart after fsync but before rename can verify the pending bytes and finish; a restart after rename but before SQLite commit can verify the final content and complete the journal.
- Incomplete, manipulated or unexpected pending/final states fail closed; no unsafe automatic cleanup or replacement.

## Evidence

`tests/test_win32_output_publication.py` is a native Windows runner-level suite with synthetic admitted jobs. It covers crash-injection at three points, replay, no-clobber, symlink/junction/hardlink rejection, content corruption, durable quotas, cross-thread ownership and concurrency. The dedicated workflow is `.github/workflows/windows-output-publication-feasibility.yml`.

## Mandatory remaining gates

1. The Windows input seal is still **disabled**. Tests seed an `ADMITTED` job directly inside a private synthetic database; that is not an authorized user operation and never bypasses production admission.
2. The Windows result publisher is **not a worker file tool**, and model-provided artifacts are not yet connected to it. Real Flint folder-job execution and same-chat delivery remain absent.
3. Windows runner results are not on-target G1b acceptance. The deployed NTFS version, target ACLs, antivirus/file-system filters, volume replacement, kill/restart, out-of-space and adversarial racing must be tested independently.
4. Shared same-user hostile processes and malicious host operators remain outside the verified threat boundary; `output/` must be protected by trusted manager deployment ACLs.
5. No claim is made that `FlushFileBuffers` plus rename proves persistence after unclean power loss across every filesystem/filter configuration. Crash tests are synthetic transaction-interruption tests.

**PRODUCT_READY=false | G1b BLOCKED | release NO-GO.** A green Windows build is not beta readiness.

## Win32 API correction in runner evidence

Windows returned `WinError 87 (ERROR_INVALID_PARAMETER)` for the native kernel32 `SetFileInformationByHandle(FileRenameInfo)` wrapper with a non-NULL `RootDirectory` and relative name. The manager deliberately uses `NtSetInformationFile(FileRenameInformation)` instead, retaining the parent directory handle and avoiding any lexical/absolute-path rename fallback. This correction must be tested by the native Windows-runner suite and separately on the target host. See [Microsoft's FileRenameInfo contract](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_rename_info).
