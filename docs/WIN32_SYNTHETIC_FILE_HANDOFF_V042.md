# v0.42 — Synthetic AppContainer file handoff (NOT a worker capability)

**Status: PRE-ALPHA / PRODUCT NO_GO, no executable worker tools.**

v0.42 extends the fixed Windows MSVC child from v0.39–v0.41 with a
**manager-owned, per-attempt disposable synthetic staging directory**.
There are precisely two fixed native test modes (file_ok and file_bad).
Neither the model, MCP callers nor Flint can provide paths, executables,
file contents, commands or environment variables to the test.

## Narrow real-OS test

1. Manager creates stage/input/sealed.bin with exact synthetic bytes,
   stage/work/, and manager-only stage/output/immutable.bin. It grants the
   temporary AppContainer package SID RX on stage/input and Modify only
   to stage/work. No package grant is made to stage/output.
2. Child starts suspended, Job-assigned (one process, kill on close,
   128 MiB) with TokenIsAppContainer=1 and only two pipe handles inherited.
   The trusted stage is passed as current working directory, not a
   user-provided path or shell argument.
3. Child reads the sealed input, explicitly verifies it cannot open the
   input file or manager immutable output for writing, and creates a
   single fixed work/result.bin file with CREATE_NEW and FlushFileBuffers.
   It sends only the original v0.38 exact PONG over the limited IPC.
4. Manager inspects the output only **after child exit**. The synthetic
   verifier rejects oversized files, nonregular files, reparse points,
   hardlinks, additional work artifacts, wrong data/hash and altered input
   or manager output. Only the exact test bytes are copied into a new
   manager-owned output/result.bin using exclusive create and fsync.
5. The file_bad native mode writes different bytes but sends the same
   valid PONG. A matching PONG is **insufficient for publication**:
   manager rejects the output, records permanent NEEDS_RECONCILIATION in
   the durable attempt journal and confirms there is no new published file.
   Reopening the journal cannot dispatch or prepare the same attempt.

The v0.42 data-plane acceptance remains a **synthetic observation**.
RESPONSE_OBSERVED is not RESULT_READY/ACK, and copying bytes in this
disposable harness is not production output publication.

## Limitations and next gates

This intentionally does not implement a race-free production file importer:
Python path lstat/open/verify would have TOCTOU risk with an adversarial
concurrent process. The test-specific fixed child has exited, and the
one-process Job Object is verified, but that is not proof against every
breakaway technique or hostile process. No user files or target-project
work directories are exposed.

Before any generic worker execution: adopt native handle-pinned
FILE_FLAG_OPEN_REPARSE_POINT/NTFS identity verification for all file
imports, file/memory quotas and full process-tree quiescence,
network egress denial, real persistent manager integration and restart
reconciliation, host-specific G1b admission and owner approval.
Existing v0.41 network timeout remains inconclusive. Issue #59 tracks
an intermittent unrelated Windows Workfiles CI contract test.

**boxed_execution_enabled=false, os_sandbox_attested=false,
run_tests/execute_shell disabled, real data NO_GO, product NO_GO.**
