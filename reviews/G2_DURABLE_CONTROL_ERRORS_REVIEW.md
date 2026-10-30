# G2-E29-02 critical implementation review

Scope: v0.16.0.dev0 durable ABORT/RESUME_REQUEST rejection events. Reviewed by the
implementing assistant in a separate pass. **Not an independent external audit.**
Independent review and beta release gates remain open.

## Findings challenged and resolved

| Challenge | Resolution and executable evidence |
|---|---|
| Error disappears after server restart | Commit canonical timeline + typed child metadata before throwing; real HTTP and restart test |
| Error says version N while another command already changed it | Snapshot and event share BEGIN IMMEDIATE transaction; current-state reads use a read transaction |
| Failure leaves partial control mutations behind | Savepoint rollback before rejection recording; injected real SQL version change is undone |
| Rejected future-version command becomes valid later | Persist failure outcome by job, control kind and operation digest; replay cannot execute it |
| Conflicting payload poisons a successful receipt | Original successful receipt checked first; mismatch is recorded without replacing the success |
| Resume replay class changes after another accepted intent | Rejected receipt checked before later-state eligibility; original rejection class preserved |
| Event insert succeeds but COMMIT fails | Deferred-FK fault reproduces failure; roll back both rows and return ERROR_JOURNAL_UNAVAILABLE |
| Journal exhaustion blocks valid cancellation | Only rejected new keys consume the 128/job quota; accepted abort still works at capacity |
| Error log stores raw operation ID / exception text | Job/operation-scoped digest + fixed catalog snapshot, tested with canaries and a DB dump |
| Error history leaks to another thread or principal | Ownership check before receipt lookup, journal writes and event/status reads |
| Logging silently launches/restarts worker | No tool or capability added; worker_started=0 remains a SQL constraint; resume remains intent-only |

## Limits requiring follow-up

- This is a **control rejection** journal. A rejected command is not a failed
  worker job and must not be projected as `FAILED` or as a resume execution.
- Unsupported inputs, authentication failures and nonexistent/unowned jobs do
  not append job events. General transport/operator health logging is separate.
- Provider, worker, broker and broad SQLite/storage taxonomy are not yet wired
  into durable manager outcomes. The database cannot guarantee error persistence
  while its own storage is unavailable.
- 128 distinct rejected keys/job, no eviction. The new diagnostic exposes
  exhaustion; there is not yet capacity-repair/retention tooling. Existing
  lifetime job quotas also remain.
- Event diagnostic text is stored as a fixed snapshot. API replay preserves the
  failure code/class; its explanatory text uses the installed catalog. No claim
  of byte-identical exception rendering across future software versions.
- New history metadata is additive. Downgrading by merely installing an old
  binary is unsupported; use the stopped-process pre-upgrade DB backup.
- Unsloth identity is still trusted static pilot configuration. Windows/Linux
  CI tests do not prove real Unsloth auth, file containment or provider control.

## Validation

See `evidence/g2-control-errors/TEST_RUN_SUMMARY.json` and included logs. The new
20-test challenge suite was executed against the unchanged v0.15 source first
and failed. The updated full suite has 213 tests. An existing transport test was
updated to assert the new error event's code/state/version instead of assuming
that a rejected abort creates no event.

Verdict: suitable for a scoped development PR after passing exact-head CI; no
new worker/file privileges and **no beta approval**. Issue #29 remains open.
