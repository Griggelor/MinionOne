# G2-E29-03 — local state/storage diagnostics (v0.17)

## Resulting behavior

SQLite failures in MCP tool calls now return distinct fixed diagnostics rather
than `INTERNAL_ERROR`. Startup failures are handled by `mcp_cli.main`: state
directory creation, database initialization and socket construction are inside
one error boundary. On failure it writes JSON `{ok:false,started:false,error}`
to stderr and returns exit code 2. It does not print the success banner or
serve requests. Argument parsing and missing-token validation still use argparse.

An explicit `--db` does not create or require the default home state directory.
Only the default database location needs that directory and its permission setup.

## Classification contract

Classification uses SQLite numeric result codes, OS errno and known exception
types. It never matches exception text, filenames or provider response bodies.

| Signal | Public code | Retryable hint |
|---|---|---|
| SQLITE_BUSY / SQLITE_LOCKED | DB_BUSY | true |
| SQLITE_CORRUPT / SQLITE_NOTADB | DB_CORRUPT | false |
| SQLITE_CANTOPEN | DB_UNAVAILABLE | false |
| SQLITE_FULL / ENOSPC / EDQUOT | STORAGE_FULL | false |
| SQLITE_READONLY / EROFS | STORAGE_READ_ONLY | false |
| SQLITE_IOERR / EIO | STORAGE_IO_ERROR | false |
| SQLITE_PERM / SQLITE_AUTH / EACCES / EPERM / PermissionError | STORAGE_ACCESS_DENIED | false |
| SQLITE_SCHEMA / explicit manager schema rejection | DB_SCHEMA_ERROR | false |
| SQLITE_CONSTRAINT / explicit stored-reference rejection | DB_INTEGRITY_ERROR | false |
| SQLITE_NOMEM | DB_RESOURCE_EXHAUSTED | false |
| Missing/incompatible local state path | STORAGE_UNAVAILABLE | false |
| SQLite exception without a usable or recognized code | DB_ERROR | false |
| Startup address collision | MCP_ADDRESS_IN_USE | false |
| Invalid startup configuration | CONFIGURATION_INVALID | false |

Application `PermissionDenied` is checked before filesystem exception types:
an unauthorized job remains `ACCESS_DENIED`, never a storage error.
Unknown non-storage exceptions remain fixed `INTERNAL_ERROR`.

Python 3.11+ supplies `sqlite_errorcode` for exceptions originating in SQLite.
Extended result codes retain the primary category in their lowest byte. Only
positive signed-32-bit integers are accepted; missing, Boolean, string or
out-of-range codes fall back to DB_ERROR. The numeric attribute is a diagnostic
signal, not an authorization or recovery decision.

Attached `Diagnostic` objects are reconstructed from their catalog key instead
of trusting their message/action/retryable values. Invalid keys become a safe
internal error. Known storage exception types take priority over attached
provider diagnostics. Provider messages and credentials cannot become public
diagnostic text through this path.

## Transactions and journal failure

The existing AdmissionStore remains the job-state owner. This slice adds no
job state, retries, worker calls, schema migration or second persistent store.
Known schema/reference rejections now have typed subclasses of InputRejected,
preserving callers that catch that existing base class while distinguishing
manager-state faults from malformed client input.

If a classified control rejection cannot be journaled, its primary public code
stays `ERROR_JOURNAL_UNAVAILABLE`. Its action additionally includes the fixed
storage-specific next step when SQLite supplied a recognized cause. Only that
safe catalog code is retained by the wrapper; raw storage exceptions are not
copied into journal metadata. Existing rollback/commit rules remain.

No persistence is claimed while the database is unwritable. `retryable=true`
for busy/locked is information for an explicit caller decision, never an
automatic retry loop or a claim that a previous effect is known to be absent.
Corrupt state is not deleted, replaced or automatically repaired. Startup may
already have created ordinary additive schema tables before rejecting an
incompatible historical schema; this is not a database-repair implementation.

## Executed test scope

`tests/test_storage_diagnostics.py` includes real SQLite writer locking through
the actual MCP HTTP listener, corrupt-file runtime/status and startup failures,
cannot-open startup, read-only mode, SQLite page-count exhaustion, default-state
directory failure, typed schema/FK rejection, secret canaries, attached-diagnostic
tampering and journal rollback. Other numeric taxonomy cases use explicitly
injected SQLite/OS exceptions through unit and HTTP boundaries.

The lock fixture reduces SQLite's wait budget to 25ms without replacing the SQL
or failure. Page-count exhaustion proves a real SQLITE_FULL result; it does not
prove physical-disk exhaustion or Windows ACL behavior. Permission/disk errno
cases are injected, so no live target storage/ACL evidence is claimed.

Exact red/green logs, environment and commands are in
`evidence/g2-storage-diagnostics/TEST_RUN_SUMMARY.json`. Issue #29 remains open
for broader provider/worker fault outcomes, durable operator health reporting
and real Unsloth UX. No worker execution or real Resume is enabled here.

## Development workflow

Per the owner's explicit 2026-10-30 instruction: implementation, tests, CI and
merge proceed without a separate review/approval stage. Older review documents
are historical records, not current workflow gates. This does not assert that
an independent audit occurred or change worker capability boundaries.

## Primary interface references

- Python sqlite3 exceptions: https://docs.python.org/3.13/library/sqlite3.html#exceptions
- SQLite primary/extended result codes: https://www.sqlite.org/rescode.html
