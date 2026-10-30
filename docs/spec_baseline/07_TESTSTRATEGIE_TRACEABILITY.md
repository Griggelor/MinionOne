> **HISTORICAL v0.5-or-earlier content:** retained for traceability. The active v0.6 source of truth is README.md plus docs/20–26 and the v0.6 JSON contracts. Conflicting instructions here are superseded.

> **Baseline-0.3-Ergänzung:** `docs/16_TRACEABILITY_SECURITY_NFR.md` definiert T-065..080 und NFR-Mapping. Die folgenden Testfälle sind **Pläne, keine ausgeführten Tests**. Code-/Test-Ausführung durch Worker im MVP ausdrücklich verboten; nur unabhängige Contract-Tests und später ggf. eigener freizugebender Testdienst.

# 07 – Teststrategie, Traceability, negative Tests, echte Abnahme

## Leitprinzipien

Test-First, Risikopriorisierung, kurze sichere Feedbackschleifen. **Keine Produktionsimplementierung** wird als funktionsfähig bezeichnet, weil Mocks grün sind. Testarten: `unit` (ohne IO), `contract` (Adapter-Fakes/Schema), `integration` (echte Services + synthetische Dateien), `Windows E2E` (Hardware/Rulith/Unsloth), `privacy/security` (adversarial). Jeder Test protokolliert Datum, Commit, Modell-/Provider-Version und tatsächliche Testumgebung.

## Abnahmetestmatrix (Entwurf)

| Test-ID | Requirement(s) | Test / erwartetes Ergebnis | Ebene |
|---|---|---|---|
| T-001 | FR-01/FR-02 | Ein vorbereiteter absoluter Jobpfad wird über MCP übernommen; derselbe Root wird verwendet | offline + E2E |
| T-002 | FR-03 | Nicht existierender/fremder Ordner: Ablehnung, kein Ersatzroot | unit/contract |
| T-003 | FR-04;NFR-02 | `../`, Junction, Symlink, UNC, Hardlink: Worker wird technisch isoliert | adversarial Windows E2E |
| T-004 | FR-05 | Identischer Task über Fake-LM-Studio, Fake-Unsloth, Fake-Cloud liefert gleiches Response-Schema | contract |
| T-005 | FR-06/FR-07 | Lokaler Model-Swap nur nach Parent-Turn-Ende; der Manager arbeitet ohne geladenen Parent | Unsloth/Rulith real |
| T-006 | FR-08/FR-23 | Parent mit korrekter Thread-ID wird im selben Chat reaktiviert | Unsloth E2E |
| T-007 | FR-09 | `QUESTION` pausiert; idempotentes `answer` setzt denselben Job fort | unit/integration |
| T-008 | FR-10;NFR-06/FR-13 | Events/Tool-I/O/gelieferte Reasoning-Summaries vollständig und redigiert persistiert | unit/integration |
| T-009 | FR-11/FR-12 | Ergebnisse nur im zugewiesenen `output/`, Status korrekt lesbar | unit/integration |
| T-010 | FR-13;NFR-03 | Personennamen/Adressen/Dateimetadaten als Cloud-Payload -> Disclosure blockiert | adversarial synthetic |
| T-011 | FR-14/FR-15 | Pseudonym-Vault worker-/cloud-unzugänglich; lokale Rückübersetzung validiert | security integration |
| T-012 | FR-16 | Budgetgrenze: zusätzliche API-Aufrufe werden verhindert | unit + fake provider |
| T-013 | FR-17;NFR-07 | Crash/Restart mit bestätigtem State: kein doppelter Schritt, Resume/Intervention möglich | fault injection |
| T-014 | FR-18 | Worker-Patch ins Hauptprojekt ohne Review/Freigabe -> blockiert | integration |
| T-015 | NFR-06/FR-07 | Doppelte/umgeordnete State-Events verändern Job nicht unzulässig | property/fuzz |
| T-016 | FR-07/FR-34 | Lade-Timeout: kein falscher DONE, Restore separat sichtbar | fault injection |
| T-017 | NFR-06;FR-10 | Worker versucht Auditlog/Manifeststatus zu überschreiben: Zugriff verweigert | Windows E2E |
| T-018 | FR-31/FR-33 | Bei Speichermangel `allow_if_needed`: Swap nur wenn zulässig | fake + hardware |
| T-019 | FR-08 | Nach echtem Rulith-Reload ist ursprünglicher Unsloth-Chat weiterhin bedienbar | hardware E2E |
| T-020 | FR-13 | Später Cloud-Tool-Read enthält PII; Egress-Filter blockiert ebenfalls | adversarial |
| T-021 | FR-29/FR-30 | Jobabschluss erzeugt genau eine korrelierte Parent-Nachricht | chat integration |
| T-022 | FR-08/FR-23 | Flash reagiert nach echtem Modellwechsel auf Jobbenachrichtigung | hardware E2E |
| T-023 | FR-29 | Parent-Chat beschäftigt -> kein konkurrierender Chat-Run; späterer Retry | integration |
| T-024 | FR-17/FR-30 | Unsloth-Restart zwischen Ergebnis und Zustellung -> persistente Outbox | fault injection |
| T-025 | FR-24/FR-25/FR-26 | Provider-Discovery + Markdown-Profil erzeugen korrektes Worker-Listing | contract |
| T-026 | FR-27 | Provider down/Modell nicht geladen: `ready=false` bzw. UNKNOWN, nicht einsatzbereit | contract |
| T-027 | FR-25/FR-28 | Profiländerung bleibt versioniert nach Neustart erhalten | unit/integration |
| T-028 | FR-28/FR-38/FR-48 | Worker ändert eigene Sicherheits-/API-Konfiguration: verboten | security |
| T-029 | FR-29 | Zweifacher Abschluss-Event: genau ein Chat-Resume und eine Jobzuordnung | property/integration |
| T-030 | FR-31 | Unsloth `plan_load` + LM-Studio Estimate werden erfasst, Grenzen markiert | adapter contract |
| T-031 | FR-32/FR-40 | `forbid` verhindert jede absichtliche und indirekte Parent-Eviction | hardware/negative |
| T-032 | FR-33/FR-41 | Beide Modelle passen: kein Parent-Unload, Worker nach Möglichkeit reused | hardware |
| T-033 | FR-34/FR-42 | Estimator unsicher/nicht verfügbar: strukturierte Fehlerrückgabe, Parent erhalten | fault injection |
| T-034 | FR-35 | Flint über LM Studio API antwortet auf synthetischen Job | Windows integration |
| T-035 | FR-36 | Unsloth-internes Modell wird über native Funktionen gewählt und angesprochen | Unsloth integration |
| T-036 | FR-37/FR-38 | Cloud-Key aus SecretStore, Cloud-Aufruf nur nach Sicherheitsgate | fake first / optional live |
| T-037 | FR-38;NFR-08 | Keys werden aus Logs/Jobexport/API-Fehlern entfernt, Secret Scan besteht | unit+scan |
| T-038 | FR-39 | Workerprofil bleibt bei Umstellung LM Studio ↔ Unsloth fachlich unverändert | unit/integration |
| T-039 | FR-40 | Backend mit aktivem Auto-Evict bei `forbid` -> sicher verweigern | simulated+live |
| T-040 | FR-41 | Geladenes Worker-Modell wird unverändert verwendet | fake+live |
| T-041 | FR-42 | UMA-Speicher: kein Doppeltzählen von RAM/VRAM, Unsicherheitsflags | unit+hardware |
| T-042 | FR-43;NFR-04 | Manipulierte/fremde `thread_id` kann keine Parent-Nachricht auslösen | security |
| T-043 | FR-44 | Tool-Proxy blockiert Shell/Netzwerkzugriff außerhalb Whitelist | adversarial |
| T-044 | FR-45 | Worker DONE + Parent RESTORE_FAILED -> kein falsch erfolgreiches System-Gesamtergebnis | state machine |
| T-045 | FR-46 | Prompt injection aus TASK/Workeroutput überschreibt keine Admin-Policy | adversarial |
| T-046 | FR-47 | Hash nach Vorbereitung geändert -> Job bricht kontrolliert ab | TOCTOU integration |
| T-047 | FR-49 | Cloud-Echtdaten ohne Approval -> blockiert; Approval widerrufbar | security |
| T-048 | FR-50 | Archivexport liefert Audit+Artifacts ohne Geheimnisse; Löschung nach Policy | integration |
| T-049 | FR-07/FR-31 | OOM beim parallelen Load -> Fehler, Parentzustand verifiziert, keine Kill-Aktion | hardware fault injection |
| T-050 | FR-23/FR-30 | Chat gelöscht/archiviert oder Account gewechselt -> Benachrichtigung sicher ausstehend/gescheitert | chat integration |
| T-051 | FR-10;NFR-13 | Provider liefert keine Reasoning-Tokens -> Audit erhebt keinen falschen Claim | unit |
| T-052 | FR-05/FR-09 | Worker legt synthetischen Test-/Reviewbericht an und stellt Rückfrage nach Toolaktionen | integration |
| T-053 | FR-07;NFR-07 | MinionOne während Swap beendet -> kontrollierter Recovery-Status und Manual Repair | fault+hardware |
| T-054 | FR-02/FR-04 | Flash zeigt Hauptprojekt an, Worker kann nur an übergebenen Jobordner | Windows E2E |
| T-055 | FR-35/FR-36/FR-37 | Providerfehler: HTTP401/429/503/Timeout strukturiert, Retry budgetiert | adapter contract |
| T-056 | FR-24/FR-27 | Katalog unterscheidet configured/installed/loaded/ready/unknown korrekt | unit/contract |
| T-057 | FR-29/FR-45 | Crash zwischen Chatnachricht und Ack -> Retry dedupliziert | fault injection |
| T-058 | FR-31/FR-32 | Schätzung okay, realer Speicherverbrauch zu groß: keine Parent-Policy-Verletzung | real hardware |
| T-059 | NFR-11/FR-14 | Releaseartefakte reproduzierbar; Hash-/Manifest-/Version-Validierung besteht | CI |
| T-060 | NFR-16 | Ohne explizite Freigabe wird keine Public-Funktion/Unsloth-PR ausgeführt | process acceptance |

| T-061 | FR-19 | Router wählt bei synthetischem Coding-/Review-Task geeignetes Profil anhand dokumentierter Kriterien | unit; P1 |
| T-062 | FR-20 | Zwei Worker-Sessions laufen getrennt; Ergebnisse konfligieren kontrolliert | integration; P1 |
| T-063 | FR-21 | Event-Monitor zeigt Live-Events und gecachte Artefakte ohne vertrauliche Inhalte | UI; P1 |
| T-064 | FR-22 | Rollenprofile lassen sich persistent speichern und erneut zuordnen | unit; P1 |

## Phasen der Testabdeckung

1. **Spec-/Schema-Gate (ohne GPU):** Markdown-/File-Consistency, JSON-Schema, FR-/Test-ID-Existenz, YAML-Profile, keine Secrets.
2. **Unit-/Property-Gate (ohne GPU):** State Machine, Idempotenz, Policy-Merging, Ressourcen-Entscheider, Outbox/Recovery.
3. **Provider-Contract-Gate (Mocks):** Unsloth, LM Studio, Rulith (zunächst UNIMPLEMENTED), OpenAI (fake), Providerfehler und Rate Limits.
4. **Synthetische Integration (lokal):** echte MCP-Interaktion, echter Datei-/Toolproxy, LM Studio Flint, Unsloth Parent mit `forbid`; keine sensiblen Daten.
5. **Hardware-/UX E2E:** Rulith-Parent, Modellwechsel, Unsloth-Originalthread, langer Kontext und Rückfragen.
6. **Privacy-/Security-Gate:** adversariale Dateiformate und Tool-Ausgaben, Cloud-Egress, Windows-Escape, Secret-Scanning, Audit-Tamper.
7. **Release Candidate/Realtest:** Nutzertest mit nichtkritischen Daten; erst später ausdrücklich freigegebener Echtdatenlauf mit separatem Risiko-Review.

## CI vs. Realtest

CI auf GitHub-Runner kann keinen echten Strix-Halo-ROCm-/Rulith-Speicherwechsel nachweisen. Die grüne CI zeigt *nur* CPU/Mock-/Contract-/Scan-Gates; Hardware-Abnahme ist eigenständig und mit Artefakten protokolliert.

## Testdaten

Synthetische Fixture mit Pseudonym-Platzhaltern, Fake-Namen, Fake-API-Keys und variablen langen Kontexten. Adversariale Fixtures für Junctions, absolute Pfade, `../`, binäre Dateiformate, Prompt Injection, falsche Model IDs, API 401/429/503, OOM, Redispatch, Timeouts. Produktionsdaten dürfen **nicht** in GitHub Issues, CI, Testlogs oder öffentliche Releases gelangen.

## Freigabekriterien pro Release

- Keine offenen kritischen P0-Befunde; P0-Tests abgenommen oder mit formaler, dokumentierter Scope-Reduktion deaktiviert.
- Alle Run-IDs/Job-IDs/Thread-IDs korrekt korreliert.
- Kein `forbid`-Unload, kein Escape/Disclosure-Bypass.
- Eindeutige getrennte Bewertung fachliches Ergebnis, Parent-Restore und Benachrichtigung.
- Reproduzierbarer Build/Hash, changelog, Rollback, Konfig-Migrationsplan.
- Nutzertest auf echter Hardware erfolgreich, bevor „releasefähig für diesen Einsatz“ behauptet wird.

## v0.5 – Zusätzliche Lebenszyklus-/Create-only-Abnahmetests (geplant, nicht ausgeführt)

| T-091 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Job-ID sofort nach persistenter Registrierung, auch bei späterem Crash | contract + integration / Windows E2E |
| T-092 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | list_jobs / get_job_status nach Neustart, Owner-Bind und fremde Job-ID verweigern | contract + integration / Windows E2E |
| T-093 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Heartbeat/Lease-Ablauf, Fencing vor INTERRUPTED und vor neuem Attempt | contract + integration / Windows E2E |
| T-094 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Resume aus unverändertem versiegelten Input und Work-Snapshot; attempt_no+1 | contract + integration / Windows E2E |
| T-095 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Resume ohne Checkpoint oder mit aktiver alter Lease verweigern | contract + integration / Windows E2E |
| T-096 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Abort ist zweiphasig: CANCEL_REQUESTED -> nach Quiescence CANCELLED | contract + integration / Windows E2E |
| T-097 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Gleichzeitiges Resume/Abort und wiederholte Commands idempotent und CAS-sicher | contract + integration / Windows E2E |
| T-098 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Retry nach FAILED unterscheidet sich von Resume, unbestimmte Side Effects blockiert | contract + integration / Windows E2E |
| T-099 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Worker create_work ist nur Neuanlage; Casefold-Kollision/Overwrite/Delete verhindert | contract + integration / Windows E2E |
| T-100 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Eine output/job.log.jsonl, manager-exklusiv und inkrementell lesbar | contract + integration / Windows E2E |
| T-101 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | DB-Commit/Dateiappend-Crash, Torn Line und verifizierter Projection-Rebuild | contract + integration / Windows E2E |
| T-102 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Alle Manager-Toolaktionen als redigierte strukturierte Logevents mit fester Sequenz | contract + integration / Windows E2E |
| T-103 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Worker kann output/log nicht lesen/schreiben oder Managerstatus fälschen | contract + integration / Windows E2E |
| T-104 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | RESULT_READY/INTEGRATING/RESTORATION_REQUIRED getrennt von COMPLETED/ACK | contract + integration / Windows E2E |
| T-105 | ADR-18 / FR-07/FR-10/FR-17/FR-29/FR-45, NFR-06/07 | Reconciliation von Provider-Send und Parent-Resume ohne doppelte Nachricht | contract + integration / Windows E2E |
