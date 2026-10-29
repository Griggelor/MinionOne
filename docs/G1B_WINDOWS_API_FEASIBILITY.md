# G1b Windows API feasibility — NOT a sandbox acceptance test

**Scope:** independent, Windows-runner-only experiments using only synthetic
temporary files. This code is not used by any MCP tool or worker file broker.
The entire G1b security gate remains **BLOCKED / NO-GO**.

## Documented APIs tested

- Microsoft CreateFileW: OPEN_EXISTING with FILE_FLAG_OPEN_REPARSE_POINT
  and CREATE_NEW no-replace semantics.
  https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
- Microsoft GetFileInformationByHandleEx: FileIdInfo (volume/file identity),
  FileStandardInfo (number of links), FileAttributeTagInfo (reparse bit).
  https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getfileinformationbyhandleex
- Microsoft GetVolumeInformationW: filesystem name and hardlink/reparse features.
  https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumeinformationw

## Threat-driven cases

1. A regular file has stable volume/file ID over separately opened handles.
2. An allowed-looking hardlink to an external filename has the SAME ID
   and multiple links. A lexical allowed-path check cannot distinguish it.
3. CREATE_NEW refuses a duplicate filename; original bytes stay unchanged.
4. File symlink opened with OPEN_REPARSE_POINT has a reparse attribute.
5. Directory symlink opened with OPEN_REPARSE_POINT has a reparse attribute.
6. Windows build, volume type (must be NTFS in this runner experiment),
   and hardlink/reparse feature flags are recorded in the CI log.

Symlink tests explicitly SKIP if the runner cannot create a link. A skipped
test does not count as proof. These tests are not a directory sandbox.

## Critical architectural limitations

- CreateFileW with a path may follow a symlink in an intermediate directory,
  even when the final component is opened with OPEN_REPARSE_POINT. The
  experiment checks only the final object in a controlled temporary tree.
- No trusted root handle is pinned for lifetime; no secure handle-relative
  walk, intermediate-component identity check or rename-swap protection exists.
- No cross-volume/UNC/ADS/device/8.3/casefold/race/fault-injection testing,
  no ACL isolation against a separate host principal, no hostile filter driver
  testing, no target hardware NTFS proof.
- For the final broker, consider NtCreateFile with RootDirectory and strict
  handle-relative component walk. Microsoft documents relative names with
  RootDirectory, but user-mode OBJ_DONT_REPARSE behavior with this combination
  has API-contract ambiguity; do **not** assume this flag alone is sufficient:
  https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-ntcreatefile

## Required next step before enabling ANY worker file rights

Implement a handle-bound, manager-controlled broker with alternate-user,
reparse/junction/hardlink/race adversarial tests on the **actual target**
Windows 11 filesystem. Keep all worker IO disabled if any critical case is
unverified. Release policy cannot be weakened based on green CI alone.

## Test command and evidence

Windows only:

    py -3.13 scripts/windows_handle_feasibility.py

GitHub Actions workflow:
https://github.com/Griggelor/MinionOne/actions/workflows/windows-handle-feasibility.yml

The exact run, commit hash, Windows build, filesystem type, pass/skip/fail
counts and independent review result must be recorded before treating the
feasibility slice as complete. G1b remains **NO-GO** regardless.
