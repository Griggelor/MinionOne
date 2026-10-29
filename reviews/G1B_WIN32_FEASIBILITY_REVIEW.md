# G1b Win32 feasibility — independent-role review (2026-10-29)

**Verdict:** PASS for Windows native API feasibility only.
**Full G1b broker security:** FAIL / NOT IMPLEMENTED. Beta: NO-GO.
**Reviewer:** same assistant in a separate critical role; no independent human
security auditor, no target hardware access.

## Observed external Windows-runner evidence

GitHub Actions run:
https://github.com/Griggelor/MinionOne/actions/runs/40916892418
Tested commit: 0485f78591952d26b2724ef3168479103704da23
Windows hosted runner: build 26100, filesystem NTFS,
filesystem capability flags 0x3E72EFF.
Executed command: python scripts/windows_handle_feasibility.py

**Six tests passed, zero failed, zero skipped, 0.052 seconds.**

- CREATE_NEW refused an existing filename.
- File symlink was opened as a reparse-point object.
- Directory symlink was opened as a reparse-point object.
- Hardlinked names shared the same file/volume identity; link count >= 2.
- File IDs remained stable across separately opened file handles.
- Real runner NTFS and hardlink/reparse feature flags were measured.

## Independent critical analysis

The experiments prove Windows API calls and the *existence* of escape
classes; they **do not** show that MinionOne rejects malicious file
requests. This exact probe uses path-based CreateFileW for controlled
synthetic paths; that is not an acceptable complete worker trust boundary.

Critical missing technical proofs:
- Handle-bound trusted root and per-component relative opens with validated
  volume/file identity and complete reparse handling, not final-component
  attributes only.
- Hardlink escape rejection under concurrent file/hardlink mutation, with
  proper handling of NTFS aliases and differing volume IDs.
- Directory junction/mount-point/hardlink attack cases, ACL isolation from
  adjacent Windows users, rename/swap races, device/UNC/ADS and 8.3 names.
- Atomic CREATE_ONLY publication and no-replace commit under crash/fault
  injection, correct quota and durable recovery, stale attempt fencing.
- Real target Windows 11 host, provider interactions, and external-user tests.

Do not over-rely on NtCreateFile OBJ_DONT_REPARSE in user mode: Microsoft
documentation for that exact relative-root combination is not clearly
normative. Use defense-in-depth plus bounded, **independently evidenced**
adversarial Windows tests; if not demonstrable, deny all worker file tools.

**Release gate:** G1b remains BLOCKED. This code must never be imported
as an authorization helper or described as a secure file broker.
