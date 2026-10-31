# MinionOne v0.30: Methoden-, Architektur-, Code- und Funktionsreview

**Datum:** 30.10.2026. **Arbeitsgrundlage:** PR #47, aktueller Source-Stand, die normative v0.6-Spezifikation (20_NORMATIVE_ARCHITECTURE_V06.md und 23_TEST_PLAN_V06.md), v0.27–v0.30-ADRs sowie die GitHub-Windows/Ubuntu-CI.

## Vorgehen und Evidenzbegriffe

1. Vertikaler Kontrollfluss: authentifiziertes MCP → Flash-Direktdelegation oder Prepare/Seal/Start → kanonischer SQLite-Job → tatsächlicher LM-Studio-HTTP-Adapter → AgentLoop und sechs Broker-Werkzeuge → native Manager-Dateiveröffentlichung → Resultat → geprüfter Parent-ACK.
2. Horizontale Grenzen: Zustandsautomaten und CAS, Operation-Idempotenz, Provider-Timeouts, ungeklärte Side Effects, Win32-FileId/Volume/Reparse, ein- und ausgehende JSON-Nutzlasten, sichere Logs und Credentials.
3. Negative und positive Fälle an jedem Übergang; konkrete Codekorrekturen zusammen mit Regressionstests, keine zusätzlichen Test-CLIs.
4. Die Angaben **CI** (echter Workflow), **SYNTH-E2E** (echtes MCP und LM-Studio-kompatibles HTTP, synthetischer Provider) und **TARGET** (echtes Unsloth/Flint und Hardware) ausdrücklich auseinanderhalten.

## Befunde, Wirkung, Korrekturen

| ID | Risiko | Befund | Maßnahme und Status |
|---|---|---|---|
| R01 | P0 | PR #47 war unter Windows rot: Job noch RUNNING, während Test-/Shutdown-Cleanup Output-Handles schließen wollte; Shutdown wartete nur 200 ms. | Native Worker-Handles vor terminalem Jobstatus schließen; begrenztes Shutdown-Joining; Windows-CI erneut prüfen. |
| R02 | P0 | SQLite-Transkript und JSONL wurden vor RESULT_READY nur in ihrer Bytelänge verglichen. Gleiche Länge bedeutet keine gleichen Inhalte. | Pinned File-Handle liest die vollständigen Bytes; Bytevergleich mit hashverifiziertem DB-Journal; bei Abweichung NEEDS_RECONCILIATION. Neuer Same-Length-Failure-Injection-Test. |
| R03 | P0 | Nach File-fsync, vor DB-Commit kann ein verwaister JSONL-Eintrag entstehen. | Kein stilles Nachspielen oder Überschreiben; Differenzen quarantänisieren, Prozess-Kill-Recovery als offenes G2-Gate führen. |
| R04 | P0 | Ältere Workspace-E2E-Tests ersetzten den gesamten LMStudioAgentProvider durch Python-Fixtures und übersprangen HTTP-Request/Response-Parser und Tool-Call-IDs. | Echte lokale HTTP-Gegenstelle und echte MCP-/SQLite-/Broker-/Publisher-Kette, plus CLI-Kindprozess-E2E. |
| R05 | P1 | Thinking ist nur dann aufzeichenbar, wenn LM Studio es tatsächlich im JSON liefert. Transport nutzt derzeit stream:false. | Test der vollständigen empfangenen reasoning_content-Felder und reasoning_tokens; Streaming bleibt offen. |
| R06 | P1 | Vertrauliche Worker-Rohdaten und redigierte Managerdiagnose haben unterschiedliche Berechtigungs- und Aufbewahrungsbedürfnisse. | Getrennte, direkt lesbare JSONL-Dateien; kein Kopieren von Token-/Promptinhalt ins Prozesslog; ACL-Prüfung am Ziel-Windows-PC offen. |
| R07 | P1 | Timeout oder ungeklärte Providerantwort dürfen nicht automatisch zu einem zweiten Versuch führen. | HTTP-Tests für ungültige Antwort, Modell nicht geladen, Timeout und gesperrten Folgeauftrag. |
| R08 | P1 | Flint ist weiterhin ein SQLite-Dokumentworker ohne native Work-Dateirechte, Shell oder Testausführung. | Grenze im Produktvertrag dokumentiert; nicht durch unkontrolliertes Freischalten umgehen. |
| R09 | P1 | ask_question erreicht WAITING_FOR_PARENT; sicheres Fortsetzen derselben Modellkonversation fehlt. | CI prüft wartenden Zustand und keinen automatischen Neustart; vollständiges Resume bleibt TARGET-Gate. |
| R10 | P2 | In mehreren Testklassen wurden durch Erben einer TestCase-Basis dieselben Fälle mehrfach ausgeführt. | Eigene nicht-testende Fixture-Klasse; echte Tests bleiben erhalten, Duplikate entfallen. |
| R11 | P2 | Unbenutztes Feld LiveJobFiles._volume und Testimport-Reste; unzutreffender ModelStep-Kommentar. | Bereinigt und Dokumentation dem realen I/O-Verhalten angepasst. |
| R12 | P2 | Alte publish_worker_io-Methode klingt wie der Normalpfad, wird aber nur beim Fehlschlag vor Live-Log-Eröffnung benötigt. | Als publish_worker_io_fallback eindeutig benannt. Nicht löschen: Fehlersicherung. |
| R13 | P2 | Historische v0.6-Dokumente beschreiben einen anderen Output-Artefaktpfad und ursprünglich nur redigierte Logs. | ADR-029 erläutert bewusst gesondertes sensibles Live-I/O; Abweichung vor Release entscheiden. |

## Neu und bereits vorhandene CI-Fälle

**Neues SYNTH-E2E:** tests/test_full_bridge_http_e2e_ci.py. Der Test verwendet zwei echte HTTP-Server. Das Modell wird als deterministische LM-Studio-kompatible HTTP-Gegenstelle gespielt, NICHT als ersetzte Python-Providerklasse. Echte Pfade: MCP-Authentifizierung, Jobreservierung, Validierung, Seal, Start, OpenAI-kompatibles Provider-JSON, Tool-Callbacks, Broker, SQLite, nativer Win32/POSIX-Output, Thinking-Log, MCP-Lesezugriff und Parent-ACK.

| Fall | Positiv/Negativ | Assertion |
|---|---|---|
| E301 | P | Direkte Delegation, gleiches operation_id wiederholt: nur ein Workerstart |
| E302 | P | Getrennte System-/User-Messages, konfiguriertes Modell und echte JSON-Body-Parameter |
| E303 | P | read_file → create_work → publish_artifact → finish; exakte Tool-Ergebnisse |
| E304 | P | Ergebnisdatei und Digest stimmen überein |
| E305 | P/N | ACK vor Lieferung verweigert, danach korrekt, doppelter ACK idempotent |
| E306 | P | reasoning_content und reasoning_tokens tatsächlich im HTTP-Response; vollständige I/O-JSONL-MCP-Gleichheit |
| E307 | P | Laufende redigierte Joblogs und getrennte Prozesslogs |
| E308 | P | Gestaffelter Ordnerweg über prepare, TASK.md, manifest, seal und start |
| E309 | N | Unzulässiger execute_shell-Aufruf wird serverseitig verweigert; kein Ergebnisfile |
| E310 | P/N | ask_question wartet auf Parent, erzeugt keine heimliche Fortsetzung |
| E311 | N | Malforme Providerantwort bleibt im Rohprotokoll, kein sauberer Erfolg |
| E312 | N | Nicht geladenes Modell: keine Generation |
| E313 | N | Timeout/ungewisse Wirkung: NEEDS_RECONCILIATION, nächster Start blockiert |
| E314 | N | Same-Length-Logkorruption: keine RESULT_READY-Freigabe |
| E315 | P | test_cli_subprocess_bridge_e2e.py startet MinionOne als eigenständigen Betriebssystemprozess über echte TCP-Sockets und bestätigt das Ergebnis |
| E316 | P | Roh- und Prozesslogs bleiben nach Ende ohne MinionOne oder DB direkt lesbar |

**CI-Workflow:** .github/workflows/full-bridge-e2e.yml führt E301–E316 ausdrücklich auf windows-latest und ubuntu-24.04 aus. Die allgemeine unittest-Discovery entdeckt beide Dateien ebenfalls; ein neuer Benutzer-Testlauncher ist nicht erforderlich.

**Bereits vorhandene Tests:** Python-Unit/Contract-CI, echte MCP-SDKs 1.30/2.3 auf beiden Betriebssystemen, Win32-Handle-/Input-Seal-/Manager-Publisher-Tests, Windows-Frozen-App-Test und separat bewusst strenge Produktakzeptanzmatrix. Der synthetische HTTP-E2E ergänzt diese und ersetzt keine der nativen Sicherheitsprüfungen.

## Nicht als überflüssig gelöscht

Die Zustandsdatenbank, Outbox, Attempt-Fences, native File-Broker und Fehler-Recovery sind nicht redundant zur direkt geschriebenen JSONL-Datei. Ebenso bleibt die historische Pilot-Toolkette für den isolierten Systemtest absichtlich nur außerhalb des Workspace-Modus verfügbar. Die Pre-open-Fehlerprojektion der Workerlogs wird weiterhin benötigt. Das vollständige Entfernen einzelner Module ohne geprüfte Aufrufer wäre eine riskante Änderung ohne Evidenz.

## Offen: weitere echte E2E und Fault-Injection

| ID | Noch NICHT nachgewiesener Testfall | Gate |
|---|---|---|
| E317 | OS-Kill direkt nach JSONL-fsync, vor SQLite-Commit: keine doppelte Modellgeneration | G2 |
| E318 | Kill nach SQLite-Commit, vor Toolantwort: Attempt-Fencing erhalten | G2 |
| E319 | fsync-Fehler/Disk-Full/Read-only: kein falsch positives RESULT_READY | G2 |
| E320 | Unvollständige letzte JSONL-Zeile: letzte gültige Zeile lesbar, Reconciliation | G2 |
| E321 | Native NTFS-Junction-, Reparse-, Hardlink-, ADS- und 8.3-Race-Angriffe | G1b |
| E322 | Unberechtigter zweiter Windows-Account liest vertrauliches I/O-Log | G1b |
| E323 | Gleichzeitige Parent-Clients, parallele Cancel/ACK/Start-CAS-Rennen | G2 |
| E324 | Provider antwortet nach Abbruch/Timeout verspätet: keine alten Tools | G2/G3 |
| E325 | Flash in echtem Unsloth: MCP-Tools, Parent-Thread, Chat-Wakeup/Review | G1a |
| E326 | Flint in echtem LM Studio: Thinking-Format, Tokenlimits, Residency | G1c/G3 |
| E327 | Sicheres Fortsetzen nach Parent-Frage mit neuer Attempt-ID | G2/G3 |
| E328 | Native Work-Dateien, Multi-File-Codeentwicklung und wirkliche Tests | G1b/G3 |

**Abnahme:** Ein PR-Head gilt nur dann für die neue CI-Integration als geprüft, wenn alle GitHub-Workflows desselben HEAD-Commits erfolgreich sind. Ein neuer Commit invalidiert die alten grünen Prüfläufe. Ein TARGET-PASS wird niemals aus synthetischem HTTP, GitHub Runner oder einem verdichteten Modellbericht abgeleitet.

**Produktstatus:** G1a/G1b/G1c/G3 und echte Entwicklungstätigkeit bleiben NO-GO. Eine synthetische grüne CI prüft eine anspruchsvolle Integrationskette, belegt aber nicht eine echte Unsloth-/Flint-Installation, Multi-User-NTFS-Sicherheit oder ein stabil gehaltenes Parent-Modell.
