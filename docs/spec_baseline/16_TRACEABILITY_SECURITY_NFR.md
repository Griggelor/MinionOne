> **HISTORICAL v0.5-or-earlier content:** retained for traceability. The active v0.6 source of truth is README.md plus docs/20–26 and the v0.6 JSON contracts. Conflicting instructions here are superseded.

# 16 – Ergänzende Test-/NFR-Traceability für Baseline 0.3

**Jeder Test hier ist zunächst ein geplanter Abnahmetest.** `tests/test_contract_checks.py` führt *ausschließlich Offline-Contract-Unterfälle* aus; Windows-, Unsloth-, Model-/Hardware-E2E erfordern separate reale Durchführung und dürfen nicht als PASS verbucht werden. Test-IDs T-065–T-080 ergänzen T-001–T-064 aus 07.

| ID | FR / NFR | Negativtest / erwartete Eigenschaft | Evidenz-Gate | Owner |
|---|---|---|---|---|
| T-065 | FR-04/FR-44;NFR-02;NFR-05 | Worker beantragt Shell/PowerShell/Python/exec/run_tests; kein Prozess wird gestartet, DENY Event | G2 Windows E2E | Security |
| T-066 | FR-04/FR-47;NFR-02 | Handle-based Windows Broker gegen Symlink, Junction, Hardlink, ADS, UNC, DOS-Namen, TOCTOU | G1b Windows E2E | Security |
| T-067 | FR-47;NFR-11 | `TASK.md` oder `input/*` nach Accept verändert: Snapshot stabil/Job-Reject | G2 integration | QA |
| T-068 | FR-45/FR-29;NFR-07 | `COMPLETED` vor Integrations-Ack/Delivery/Restore wird abgewiesen | G0 offline + G2 | QA |
| T-069 | FR-29/FR-30;NFR-07;NFR-10 | Timeout nach Server-Send vor Ack: erst Reconciliation, kein blindes Retry | G2 crash/host E2E | Integrationsarchitekt |
| T-070 | FR-43/FR-23;NFR-04 | Unbefugter Caller mit echter Thread-ID darf Parent nicht schreiben | G1a auth integration | Security |
| T-071 | FR-46/FR-48;NFR-08;NFR-15 | Worker-/Profil-/TASK-Injektion erlaubt kein Policy-/Secret-Upgrade | G2 negative integration | Security |
| T-072 | FR-10;NFR-06;NFR-13 | Worker kann Eventwriter nicht ändern/Reasoning nicht fälschlich behaupten | G2 Windows integration | QA |
| T-073 | FR-32;NFR-01 | JIT-Auto-Evict bei `forbid` -> reject vor Load; geschützter Parent bleibt | G1c/G3 Hardware | Resources |
| T-074 | FR-13;NFR-03 | 0 externe Payloads im lokalen MVP, später vollständiger Egress-Test inklusive Tool-Response | G2 + G5 | Privacy |
| T-075 | FR-27/FR-34;NFR-09 | Adapter meldet unsupported/UNKNOWN, kein behauptetes load/estimate/resume | G2 adapter contract | Architektur |
| T-076 | FR-17/FR-30;NFR-10 | Recovery nach Prozessverlust maschinenlesbar, belegbar, ohne verdeckten Erfolg | G2 | QA |
| T-077 | FR-17;NFR-12 | Offline CI unter dokumentiertem Ziel, Timeout ist niemals PASS | G0 CI + Messung | QA |
| T-078 | FR-18;NFR-14 | Patch wird erst nach Reviewer-/User-Ack in Hauptprojekt integriert; Rollback/Evidenz | G3 release integration | Release |
| T-079 | FR-16;NFR-16 | Cloud-Kosten, Public-Release, destruktive Aktion und Testexec ohne Freigabe geblockt | G2/G5 | Security+User |
| T-080 | FR-38/FR-50;NFR-08;NFR-15 | Worker exportiert keine Secrets; keine stille Loglöschung, Admin-Retention | G3/G5 | Operations |

## Formales Evidenzschema pro Testlauf (Vorlage)

- `test_id`, `requirement_ids`, `adr_ids`, `scenario`: was genau geprüft wird.
- `tested_entrypoint`, `environment`, `backend_version`, `backend_commit`, `os_build`, `date_utc`: was wirklich ausgeführt wurde.
- `input_digest`, `policy_version`, `test_code_commit`, `observed_events`, `result`: `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN`.
- `negative_assertion`, `side_effect_probe`, `screenshot/log_artifact_digest`, `limitations`.
- Kein Mock-PASS als Windows-/Production-PASS; für alle Hardware-E2E eine eigene ausführbare oder manuelle Testakte.

## Qualitätsziel-Traceability

Alle NFR-01..NFR-16 sind oben explizit abgedeckt. **Traceability ≠ bestandene Tests.** Für die ersten 50 FRs bleiben `docs/03` und `docs/07` historische Anforderungs- und Testlisten; Sicherheits- und Scope-Interpretation der Baseline 0.3 siehe `docs/13`–`15`.

## v0.4 zusätzliche geplante Abnahmetests (nicht ausgeführt)

| ID | FR / NFR | Negativtest / erwartete Eigenschaft | Evidenz-Gate | Owner |
|---|---|---|---|---|
| T-081 | FR-04/FR-47;NFR-02 | Worker darf `output/` nicht listen/lesen, auch nicht über Receipts oder Fehlerantworten | G1b/G2 Windows | Security |
| T-082 | FR-04/FR-47;NFR-11 | Input-Snapshot unveränderlich; Task und Manifest unter `input/` | G1b/G2 Windows | Security |
| T-083 | FR-04/FR-47;NFR-02 | Work-RW ohne Root-Escape; Link/Junction/ADS/TOCTOU | G1b/G2 Windows | Security |
| T-084 | FR-18/FR-47;NFR-14 | Output write-once: doppelte Publikation verändert keine bestehenden Bytes | G2 Windows | QA |
| T-085 | FR-10/FR-47;NFR-08 | Worker-Event darf keine Manager-Actor/Status-/Ack-Felder fälschen | G2 | QA |
| T-086 | FR-17/FR-30;NFR-10 | Event-Journal und Output-Projektion crash- und idempotenzsicher | G2 Crash-Tests | QA |
| T-087 | FR-10/FR-38;NFR-06 | Logs inkrementell lesbar für Parent; Work-Drafts bleiben nicht autoritativ | G2 | QA |
| T-088 | FR-04/FR-48;NFR-15 | Worker besitzt keine direkten Host-/Unsloth-Filesystemtools | G1b/G2 | Security |
| T-089 | FR-10/FR-47;NFR-08 | Erkennung manipulierten Event-Hash/Sequence mit geschütztem Anchor | G2 | Security |
| T-090 | FR-17/FR-47;NFR-12 | Retry/Event-Spam/Budget-Exhaustion, kein blindes Overwrite | G2 | QA |

**Wichtig:** T-081 bis T-090 sind geplante Realintegrationstests. Offline-Contractprüfungen sind getrennte, schwächere Evidenz.
