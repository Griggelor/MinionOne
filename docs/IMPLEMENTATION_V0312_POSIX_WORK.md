# v0.31.2: POSIX native Workspace Work broker (development increment)

Status: **WIP / NO_GO**. Source branch `feat/v0312-native-posix-work-broker` is stacked
on the v0.31.1 TEST-FIRST branch. This does not enable production use, Windows
native Work operations, unrestricted code execution, or autonomous parent resume.

## What changed

- `NativeWorkspaceBroker` handles real `work/` files on POSIX through trusted
  job-directory file descriptors and no-follow relative file operations.
- `create_work`, `read_file`, `list_files`, `write_work` (expected SHA-256),
  `delete_work`, `rename_work` (no-replace), `mkdir_work` and `rmdir_work`
  are bounded to the approved `work/` tree.
- `input/` is read-only and re-checked against the admission byte snapshot.
- `publish_artifact` reads the actual physical Work bytes, verifies them and
  pins an immutable result snapshot under the existing manager output protocol.
  The legacy publisher still writes `output/` at the end of the worker loop.
- The isolated document-test mode stays SQLite-backed; that is not the
  product-facing Workspace Work path.
- New tool names are admitted to the redacted job telemetry schema, and broker
  authorization denials stay counted even when raised inside a file-handle
  context manager.
- Windows **does not** advertise the new Work tools or use the POSIX broker.

## Evidence

- v0.31.1 target suite: `python scripts/run_workfiles_v031.py --strict`.
  WF-001/002/003 are proven green on initial Ubuntu CI; remaining cases
  are being resolved in this branch. A synthetic pass is still **not** an
  OS sandbox or genuine Unsloth/LM Studio product acceptance.
- Security focused: `python -m unittest tests.test_native_work_boundaries -v`.
- Cross-boundary: `python -m unittest discover -s tests -v` plus the explicit
  nine product-acceptance cases. GitHub CI runs Windows and Ubuntu independently.

## Important open gates

1. Native Windows NTFS handle-relative full CRUD with real token/ACL boundary,
   reparse/ADS/hardlink/race protection (G1b), is still missing.
2. POSIX access still needs a full independently validated untrusted-worker OS
   identity/sandbox plus parallel non-cooperating process mutation checks.
3. Crash-between-filesystem-and-SQLite-receipt replay, stronger kernel CAS and
   idempotent worker operation IDs need explicit treatment. A stopped job is
   never automatically replayed.
4. Output remains flat `output/<name>`, while future `output/artifacts/`
   migration is an explicit contract decision. Nested Work source paths are
   flattened to the basename on publication, with no-replace collisions denied.
5. Currently `pilot_files(zone='output')` holds manager-side immutable output
   snapshots; `work/` data no longer lives in `pilot_files(zone='work')` on
   the new POSIX Workspace path. The durable control-plane database remains.
6. Live Unsloth/Flint host test, parent confirmation and safe resume remain
   unverified. The `NO_GO` release status remains authoritative.

No separate review process is introduced; implementation proceeds from the
explicit red v0.31.1 acceptance contract and actual CI evidence.
