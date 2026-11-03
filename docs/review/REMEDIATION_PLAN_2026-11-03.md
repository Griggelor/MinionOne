# Maßnahmenplan nach unabhängigem Multi-Rollen-Review (2026-11-03)

**Baseline:** v0.48, Commit d78457fa. **Gültige Owner-Vorgaben:** v0.45–v0.48. **Status:** PRE-ALPHA, Produkt- und Echtdatenfreigabe weiterhin NO-GO.

Das Vorhaben bleibt: Flash ist Auftraggeber/Planer; MinionOne ist universeller Job-/Sicherheits-/Provider-Manager. Exakt zwei Übergabewege, beide mit demselben Workspace-Job. Kein impliziter Chat-Kontext und kein separater Review-/Fachrollen-Executor.

## Priorisierte Aufgaben und Definition of Done

| Prio | Finding | Arbeitsauftrag | Abnahme | Abhängigkeit |
|---|---|---|---|---|
| P0 | F01, [#70](https://github.com/Griggelor/MinionOne/issues/70) | Einen gemeinsamen bounded-text-Inputvertrag bei Seal und Start verwenden | Drei Repros früh abgelehnt, keine ACCEPTED-Jobanlage; Retry nach Korrektur; positiver MCP Seal→Start; Linux-/Windows-CI grün | Keine |
| P0 | F02, [#2](https://github.com/Griggelor/MinionOne/issues/2), [#3](https://github.com/Griggelor/MinionOne/issues/3), [#4](https://github.com/Griggelor/MinionOne/issues/4), [#7](https://github.com/Griggelor/MinionOne/issues/7) | Zielhost-Abnahme mit synthetischen Daten: beide Übergaben, echte Modell-/GPU-Verwaltung, native Same-Chat-Rückgabe und ACK, Rückfragen, Neustart und Fault-Tests | Maschinenlesbarer Laufbericht mit Git-/Host-/Modellversionen, Prozess-/Dateihashes, Zustandssequenz, Negativfällen und dokumentierter manueller Abnahme | Zielhardware/Unsloth-Installation |
| P1 | F03, #4 | VRAM/RAM/UMA-Inventar und GPU-Telemetrie für tatsächliches Backend (AMD/ROCm und/oder NVIDIA) | Kontrolliertes Modellladen/-entladen, Ressourcenreserven, OOM-/Reconciliation-Tests; Parent nicht unerwartet verdrängt | Zielhardware |
| P1 | F04 | Universellen Dateivertrag für reale Projekte (Bäume, größere Quellen, Binärdaten, Metadaten, Limit-Versionierung) spezifizieren | Freigegebene Capability-ADR, Broker-/Snapshot-/Manifest- und Metamorphic-Testmatrix | F01, Produktanforderung |
| P1 | F04 | Optional isoliertes Code-/Test-Execution-Profil, niemals pauschal Shell freischalten | OS-Sandbox-/Process-/Filesystem-/Netz-Isolations-Gate, End-to-End-Negativtests und Artifact-Import-Workflow | File-ADR + native Sicherheit |
| P1 | F05 | Pseudonym-Vault, SecretStore und sichere Cloud-Datenklassifikation planen | Synthetische Datenschutz- und Exfiltrations-Negativtests, klare Cloud-GO-Bedingungen; bis dahin Echtdaten nicht erlauben | Explizite Datenschutz-/Key-Lifecycle-ADR |
| P1 | F06, #7 | Produktabnahme mit Testevidenz belegen | Jede als verifiziert markierte Target-Case-ID referenziert echten Host- oder reproduzierbaren Automatiktest samt Commit und Datum | Systemtest |
| P2 | F08, [#29](https://github.com/Griggelor/MinionOne/issues/29) | Sensitive Worker-I/O-Protokolle: Retention, Backup, Rechte, Export und Löschung | Policies, Tests für unberechtigte Zugriffe und Löschen, keine Log-Leaks in Fehlerausgaben | Datenschutzpolitik |
| P2 | F09, #2 | Unsloth-UI-Version-/Adapterstrategie | Gepinnte Kompatibilitätsmatrix, wiederholbarer Same-Chat-Test auch nach Upgrade | Zielhost |
| P2 | F07 | Parallele Worker nach echtem Kapazitätsbedarf definieren | Multiworker-Leases, CAS/Fencing und VRAM-Stress-Nachweis | P0/P1 |
| P3 | F10, [#8](https://github.com/Griggelor/MinionOne/issues/8) | Lizenz-/Provenienz-Entscheidung | Eigentümerentscheidung, Lizenzdatei, Drittanbieterinventar | Vor öffentlichem Release |

## Bereits gestartet: F01 – Test-/Codepfad

**Codeänderung im Branch review/v048-remediation-f01:** Neue Funktion bounded_worker_inputs in src/minionone/workspace_input_contract.py. Sie validiert die nach SHA-/Manifest-Check verifizierten Bytes und normalisiert Task und Quellen in die tatsächlich ausführbare bounded-text-Capability. workspace_sealing._commit_captured prüft sie vor dem kanonischen DB-Commit. WorkspaceJobs.start prüft denselben Vertrag erneut, damit ältere Seals und korrumpierte Daten weiter fail-closed bleiben.

**Kein Featureausbau:** Höchstens vier Textquellen, insgesamt höchstens 8192 UTF-8-Bytes, jedes Dokument max. 3500 Zeichen/4096 Bytes, keine Binärdateien, keine Shell und keine automatische Cloud-Freigabe. Dies sind bewusst Pilot-Budgets. Spätere erweiterte Project-Files-/Code-Capabilities werden separat spezifiziert.

**Eigene lokale Evidenz (Linux):**
- 7 neue HTTP/MCP-Regressionstests PASS, einschließlich vier Negativkategorien, gültigem Seal→Start und erfolgreichem Reseal nach Korrektur.
- 44 bestehende Workspace-/Input-/Dispatch-/Windows-Suite-Tests: 27 PASS, 17 erwartete Windows-SKIP.
- Ein breiterer Lauf mit Text-/Provider-Szenarien lief in ein Testzeitlimit und ist **nicht** als Gesamterfolg zu bewerten.
- Einzelne bestehende SQLite-ResourceWarnings beobachtet, nicht als F01-Failure klassifiziert.
- PR-GitHub-CI und native Windows-Abnahme noch ausstehend.

## Reihenfolge für die nächsten Umsetzungen

1. F01-PR prüfen und CI vervollständigen; solange nicht mergen, wenn native Windows-Tests oder andere Checks rot sind.
2. Auf echter Zielhardware systematisch G1a/G1b/G1c/G3 abnehmen, ausschließlich synthetische Dateien und kein Cloud-Echtdatentransfer. Ergebnisse als genaue Evidenzen festhalten.
3. Danach ggf. GPU-Telemetrie und Providerkonformität korrigieren; nicht die sichere Recovery/ParentReturn-Architektur umgehen.
4. Erst danach die größere Datei-/Code-Worker-Capability per eigenem Sicherheits-ADR entwerfen.
5. Privacy-/Vault- und Produktakzeptanz-Gates vor produktivem Einsatz schließen.

## Freigaberegeln

- Ein grüner Unit-/Mock-/CI-Lauf ist **kein** Zielhost-/Produkt-GO.
- Accepted bedeutet: sicher erfasster und für den aktuellen Executor codierbarer Eingabeumschlag, **nicht** Erfolg der Inferenz oder Garantie, dass jedes nachträglich gewählte Workerprofil die erforderlichen Ressourcen hat.
- Keine Sicherheitserweiterung durch bloßes Hochdrehen der Limits, keine freie OS-/Netz-/Codeausführung im bisherigen Worker.
- Keine personenbezogenen Echtdaten im Cloudpfad vor Datenschutz-Gate. Bis dahin PRE-ALPHA / PRODUCT NO-GO.

Quelle: [Multi-Rollen-Review](INDEPENDENT_MULTIROLLEN_REVIEW_2026-11-03.md). Tracking: [F01 #70](https://github.com/Griggelor/MinionOne/issues/70); vorhandene G1/G3-/Security-/Governance-Issues wie oben verlinkt.
