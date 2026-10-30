# Positive & Negative Verification Plan — v0.6

**Evidence declaration:** `unit/contract` and `unit/ref` indicate concrete offline tests; reference tests do **not** prove the target runtime. `PLANNED` means not executed here. `G1/G2/G3` rows must be executed against the **real MCP, file broker, persistent DB, Windows or provider path**. Never convert a planned case into a PASS without a run ID and environment metadata.

| ID | Intent | P/N | Level | Gate | Offline evidence or status |
|---|---|---|---|---|---|
| T-106 | admit identical idempotency job key | P | unit/ref | G0 | `test_delegate_duplicate_returns_same_id` |
| T-107 | reject changed sealed snapshot for reused key | N | unit/ref | G0 | `test_delegate_conflicting_snapshot` |
| T-108 | cross-principal job status must reveal nothing | N | unit/ref + MCP | G0/G2 | `test_foreign_cannot_status` |
| T-109 | cross-principal log retrieval must reveal nothing | N | unit/ref + MCP | G0/G2 | `test_foreign_cannot_logs` |
| T-110 | cross-principal worker tool call must fail | N | unit/ref + MCP | G0/G2 | `test_foreign_cannot_worker_action` |
| T-111 | cross-principal abort attempt rejected | N | unit/ref + MCP | G0/G2 | `test_foreign_cannot_control` |
| T-112 | correct worker creates one work file | P | unit/ref + Windows | G0/G1b | `test_work_new_file_and_read_receipt` |
| T-113 | duplicate file cannot replace original | N | unit/ref + Windows | G0/G1b | `test_work_no_overwrite` |
| T-114 | casefold collision on Windows rejected | N | unit/ref + Windows | G0/G1b | `test_work_casefold_no_overwrite` |
| T-115 | same idempotent create returns receipt once | P | unit/ref | G0 | `test_work_create_idempotent` |
| T-116 | same operation key changed bytes rejected | N | unit/ref | G0 | `test_work_create_idempotency_conflict` |
| T-117 | shell/terminal denied | N | unit/ref + MCP | G0/G2 | `test_forbid_shell` |
| T-118 | worker cannot see output logs | N | unit/ref + Windows | G0/G1b | `test_forbid_output_read` |
| T-119 | worker cannot write input | N | unit/ref + Windows | G0/G1b | `test_forbid_input_write` |
| T-120 | parent directory traversal fails | N | unit/ref + Windows | G0/G1b | `test_forbid_path_traversal` |
| T-121 | publisher copies work snapshot with digest | P | unit/ref + Windows | G0/G2 | `test_publish_snapshot_and_digest` |
| T-122 | publishing nonexistent work denied | N | unit/ref | G0 | `test_publish_unknown_work_rejected` |
| T-123 | output filename already used remains unchanged | N | unit/ref + Windows | G0/G1b | `test_publish_no_overwrite` |
| T-124 | worker progress does not leak raw secret | N | unit/ref + privacy | G0/G3 | `test_report_does_not_log_raw_secret` |
| T-125 | stale attempt cannot commit tool action | N | unit/ref + provider | G0/G2 | `test_fenced_old_attempt_denied` |
| T-126 | resume uses new attempt and state-version CAS | P | unit/ref + provider | G0/G2 | `test_resume_version_and_fencing` |
| T-127 | duplicate resume does not create second attempt | N | unit/ref + fault | G0/G2 | `test_resume_idempotent` |
| T-128 | abort needs quiescence before CANCELLED | P/N | unit/ref + provider | G0/G2 | `test_abort_is_two_phase` |
| T-129 | same command key reused for different action denied | N | unit/ref | G0 | `test_abort_idempotency_conflict` |
| T-130 | budget overflow does not mutate work | N | unit/ref + DB | G0/G2 | `test_over_budget_fails_without_mutation` |
| T-131 | log order is sequence-monotonic | P | unit/ref + DB | G0/G2 | `test_log_monotonic` |
| T-132 | log since-seq incremental paging | P | unit/ref + DB | G0/G2 | `test_log_incremental` |
| T-133 | no worker log modification tool | N | unit/ref + Windows | G0/G2 | `test_log_events_not_worker_editable` |
| T-134 | interrupt cannot declare worker DONE | N | unit/contract | G0 | `test_no_completed_worker_in_interrupted` |
| T-135 | interrupt without attempt rejected | N | unit/contract | G0 | `test_no_attempt_zero_interrupted` |
| T-136 | interrupt with integration ACK rejected | N | unit/contract | G0 | `test_no_integration_ack_interrupted` |
| T-137 | resume cannot smuggle unrelated answer fields | N | unit/contract | G0 | `test_resume_reject_answer_fields` |
| T-138 | resume cannot smuggle unrelated ACK fields | N | unit/contract | G0 | `test_resume_reject_ack_fields` |
| T-139 | RESTORATION_REQUIRED has explicit retry_restore | P | unit/contract | G0 | `test_restore_state_and_retry_restore` |
| T-140 | invalid restoration state rejected | N | unit/contract | G0 | `test_restore_state_requires_failed_restore` |
| T-141 | uncertain outcome supports request_reconciliation | P | unit/contract | G0 | `test_reconciliation_state_and_control` |
| T-142 | no resume before reconciliation | N | unit/contract | G0 | `test_reconciliation_disallows_resume` |
| T-143 | reconciliation flag required | N | unit/contract | G0 | `test_uncertain_state_without_flag_denied` |
| T-144 | real junction/symlink swap concurrent read | N | Windows attack | G1b | `PLANNED` |
| T-145 | hardlink escape to outside root | N | Windows attack | G1b | `PLANNED` |
| T-146 | ADS/device/UNC/8dot3 names rejected | N | Windows attack | G1b | `PLANNED` |
| T-147 | jobroot is symlink or other volume | N | Windows attack | G1b | `PLANNED` |
| T-148 | adjacent Windows user reads output | N | Windows security | G1b | `PLANNED` |
| T-149 | crash after journal commit before projection append | N | durable fault | G2 | `PLANNED` |
| T-150 | torn log tail repaired without duplicate event | N | durable fault | G2 | `PLANNED` |
| T-151 | DB busy/disk full does not falsely mark success | N | durable fault | G2 | `PLANNED` |
| T-152 | concurrent abort/resume only one CAS wins | N | durable concurrency | G2 | `PLANNED` |
| T-153 | uncertain message delivery never double-sends | N | Unsloth E2E | G1a/G3 | `PLANNED` |
| T-154 | foreign Unsloth thread cannot receive message | N | Unsloth security | G1a | `PLANNED` |
| T-155 | authorized Unsloth thread same-chat wakeup | P | Unsloth E2E | G1a | `PLANNED` |
| T-156 | parent chat busy, defer without second run | N | Unsloth E2E | G1a/G3 | `PLANNED` |
| T-157 | provider cancel returns after delayed token stream | N | provider E2E | G2/G3 | `PLANNED` |
| T-158 | parent no-eviction verified on real UMA hardware | P/N | hardware E2E | G1c | `PLANNED` |
| T-159 | HTML/markdown/archives remain inert in preview | N | host integration | G3 | `PLANNED` |
| T-160 | provider response containing injected tool call rejected | N | prompt-injection E2E | G3 | `PLANNED` |
| T-161 | credential canary absent from visible log/export | N | privacy E2E | G3 | `PLANNED` |
| T-162 | MCP unsupported method returns explicit not-supported | N | Unsloth integration | G1a | `PLANNED` |
| T-163 | snapshot changes during admission are rejected | N | Windows concurrency | G1b/G2 | `PLANNED` |
| T-164 | restore failure does not claim completed | N | provider E2E | G3 | `PLANNED` |
| T-165 | version migration safely handles old journal | P/N | durable migration | G2 | `PLANNED` |
| T-166 | cumulative budget remains consumed across resume | N | durable quota | G2 | `PLANNED` |
| T-167 | retention deletes only approved job snapshots | P/N | privacy lifecycle | G5 | `PLANNED` |
| T-168 | global rate/size quota blocks multi-job DoS | N | load test | G3 | `PLANNED` |
| T-169 | untrusted work artifact cannot run git hooks | N | host integration | G3 | `PLANNED` |

## Evidence requirements

For every executed case retain: exact command, test ID, product revision and tree hash, host OS and filesystem (especially NTFS/ReFS), provider/Unsloth/LM Studio versions, test fixture hash, observed side effects, passed/failed/skipped, runtime, log/journal receipt, reviewer and any unresolved issue. A test that times out is **INCONCLUSIVE or FAIL**, never automatically PASS. Do not put real private data in test fixtures or public reports.

### Test levels

1. **Contract:** schema and semantic rules; immediately runnable, but no OS access proof.
2. **Reference model:** bounded in-memory oracle; no durability or process isolation.
3. **Windows broker:** actual file handles, reparse points, races, hardlinks, neighboring user accounts.
4. **Durable G2:** real DB, commit/outbox/recovery, process kills, disk full.
5. **Production path G3:** real authenticated Unsloth-MCP/LM Studio adapters, approval and same-chat behavior.
6. **Release/security:** external reviewer, supply-chain checks, privacy, installer, exact target hardware.

Prior historical tests `T-001`–`T-105` remain in `docs/07_TESTSTRATEGIE_TRACEABILITY.md`. New tests must also be mapped to product FR/NFR and ADR owners in tracking issues before G2; the v0.6 matrix is a design-to-test risk map, not proof of completed traceability.

**Additional v0.6 execution evidence:** `tests/test_v06_strict_review.py` also verifies cumulative published-byte/artifact quotas, bounded progress events, unknown progress fields and stale `expected_state_version` controls. The corresponding Windows/DB gates remain open.
