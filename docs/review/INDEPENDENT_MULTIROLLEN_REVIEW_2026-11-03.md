# Unabhängiges Multi-Rollen-Review – MinionOne v0.48

**Stichtag:** 2026-11-03. **Baseline:** main d78457fa76a936653ede4a29000109e58e3b8b60. **Release-Urteil:** PRE-ALPHA / PRODUCT NO-GO.

## Methode und Belegstufen

Sechs getrennte Rollen: Systemarchitekt, Security-Architekt, Reliability/Ops, QA-/Testarchitekt, KI-/Host-Integrationsarchitekt, Produkt-/Entwickler-UX. Bewertet wurden in dieser Reihenfolge: aktuelle Owner-Vorgaben v0.45–v0.48, produktiver Python-/MCP-Code, eigene Tests, vorhandene CI-/Paketnachweise, historische Dokumentation. Die Gründlichkeit der Zustandsmaschine darf nicht mit nachgewiesener Zielhostfunktion verwechselt werden.

Produktionspfad aus dem Code: Flash über minionone_delegate **oder** prepare → seal → start → **derselbe** WorkspaceJobs-Manager → Input-Snapshot/Workerprofile → PilotJobs/AgentLoop → isolierter Dateibroker → managergeprüfte Output-Publikation → ParentReturn/ACK. Kein impliziter Flash-Kontexttransfer, kein separater Review-/Text-Workerpfad.

## Rollenbewertung

### 1. Systemarchitektur

**Stärken:** zwei universelle Auftragswege auf demselben Manager; SQLite/CAS und Receipt-Idempotenz; klar abgegrenzte Broker, Provider, Modell-Lifecycle, Publikation und Rückgabe. **Schwächen:** F01 Eingabevertragsbruch, F04 begrenztes UTF-8-/Flachdateiprotokoll, F07 nur ein aktiver Worker. Bestehendes Design bewahren, keine Sonderarchitektur für Fachrollen hinzufügen.

### 2. Security

**Stärken:** Authentisierung/Loopback an MCP; Least-Privilege-Broker, Input-Siegel und Datei-/Hashprüfung, unveränderliche Veröffentlichung; unsichere Cloud-Zustände werden quarantänisiert. **Schwächen:** F05 kein Pseudonym-Vault/OS-SecretStore und keine hinreichende Erkennung beliebiger PII bei Cloud-Eingaben; F08 volle, inhaltstragende Worker-I/O-Logs mit Zugriffs-/Backup-/Retention-Risiko. Keine im Review nachgewiesene Privilegienausweitung oder Exfiltration.

### 3. Reliability und Betrieb

**Stärken:** persistente CAS-Zustände, Attempt-Fencing, Leases, explizite Recovery und ACK; kein blindes Wiederholen ungewisser Provider-POSTs; Modellwechsel fail-closed. **Schwächen:** F02 keine echte Ende-zu-Ende-Hostabnahme mit Flash/Studio/GPU; F03 NVML-basierte VRAM-Erfassung ist für AMD/ROCm nicht belegt; historische Windows-Testinstabilität in #59/#62. PR #69 verbessert die Testbeobachtung, ersetzt aber keine Live-Abnahme.

### 4. QA und Testarchitektur

**Stärken:** zahlreiche tatsächliche MCP-/HTTP-, SQLite-, Broker-, Restart-, Fault- und Windows-EXE-Tests. **Schwächen:** F06 Produktkatalog mit 182 Fällen: zum Baseline-Review 169 NOT_VERIFIED, 13 NOT_RUN. F01 wurde durch Tests einzelner Phasen nicht erkannt. Golden-/Mock-Tests sind keine Belege für reale Modell-/GPU-/Same-Chat-Integration.

### 5. KI-/Host-Integration

**Stärken:** universelle Workerprofile für LM Studio, Unsloth und kompatible Cloud-APIs, explizit freigegebene Workerinputs, originaler Flash-Chat als Rückgabeziel. **Schwächen:** F09 versionspinnendes Unsloth-UI-Setup, F02 noch fehlender echter Chat-Resume-/Unload-/Reload-Beleg, F03 GPU-Speichersemantik nicht auf Zielhost bestätigt.

### 6. Produkt / Developer Experience

**Stärken:** klare Trennung Flash-Planung/Worker-Ausführung, zwei nachvollziehbare Übergaben, Rückfragen und Ergebnis-ACK. **Schwächen:** F04 höchstens vier kurze Textdokumente und keine sichere Code-/Testausführung im Worker; daher noch keine volle universelle Projektbearbeitung. Änderungen aus dem isolierten Work müssen kontrolliert in das reale Repository überführt werden. F10 offene Lizenz-/Provenienzentscheidung (#8).

## Findings, Schwere und vorgeschlagene Maßnahmen

| ID | Schwere | Konkreter Befund | Maßnahme |
|---|---|---|---|
| F01 | **HIGH, nachgewiesen** | seal akzeptiert nicht ausführbaren bounded-text-Input, start lehnt ab (5 Quellen; 3×3000 Byte; 4000 Zeichen Task) | gemeinsamer Seal-/Start-Vertrag, echte MCP-Regression; Issue #70 |
| F02 | **BLOCKER für Produktfreigabe** | echte Flash-/Unsloth-/GPU-/NTFS-Systemabnahme fehlt | Zielhost mit synthetischen Daten, #2/#3/#4/#7 |
| F03 | **HIGH falls AMD/ROCm-Zielhost** | NVML-only VRAM-Telemetrie im Modell-Lifecycle | HW-Inventar, backendfähige Telemetrie, Ressourcen-/OOM-Tests |
| F04 | MEDIUM/HIGH nach Produktziel | keine großen/verschachtelten/Binär-Inputs oder Code-/Testausführung | gesonderter File-Capability-/Sandbox-Exec-Entwurf |
| F05 | **HIGH für Cloud-Echtdaten** | regelbasierte PII-Filter, kein Vault | Echtdaten bleiben NO-GO; Vault/SecretStore/Privacy-Tests |
| F06 | **HIGH für Release-Nachweise** | Produktabnahmefälle nicht verifiziert | Traceability und echte Live-Gates |
| F07 | MEDIUM | nur ein aktiver Worker | Kapazitätsziel, danach Parallelität/Fencing |
| F08 | MEDIUM | vollständige sensitive Worker-I/O-Retention | Lösch-, Backup- und Zugriffspolicy, #29 |
| F09 | MEDIUM | Unsloth-UI an konkrete Version gekoppelt | Host-/Version-/Adaptermatrix |
| F10 | LOW | öffentliche Lizenz ungeklärt | Eigentümer-/Provenienzentscheidung, #8 |

## F01 – reproduzierter Fehler

v0.48 hatte einen kontradiktorischen Zwei-Phasen-Vertrag: sichere physische Versiegelung erlaubte bis zu 16 Dateien, bis 1 MiB je Quelldatei, bis 4 MiB insgesamt. Der jetzige SQLite-Text-Executor kann dagegen höchstens vier zusätzliche UTF-8-Texte (je max. 3500 Zeichen und 4096 Bytes, zusammen max. 8192 Quellenbytes) übertragen. Ein 4000-Zeichen-Task ist ebenfalls nicht startbar. Drei unabhängige Tests via LocalMCP beobachteten prepare → seal: ACCEPTED → start: INVALID_INPUT, Job verbleibt ACCEPTED. Die Verweigerung ist fail-closed, **kein** Nachweis eines Datenabflusses. Ursache: doppelte Capability-Prüfung mit abweichenden Limits.

Der im Review-PR begonnene Patch verlagert denselben strengen bounded-text-Vertrag **vor** den endgültigen Seal-DB-Commit und validiert ihn bei Start erneut. Das ist kein größerer Worker und keine Lockerung des Brokers. Die Sicherheitsquoten der physischen Datei-Erfassung bleiben separat erhalten. Profile können weiterhin eigene strengere Budgets haben; die generelle Provider-Verfügbarkeit ist von der Input-Abnahme zu unterscheiden.

## Evidenz und Einschränkungen

Baseline-Review: 84 gezielte vorhandene Tests bestanden, drei unabhängige MCP-Negativtests reproduzierten F01. Das beigefügte Baseline-CI-/Windows-Material wurde nicht eigenständig auf dem Zielhost wiederholt. Der vollständige Testlauf konnte lokal nicht abgeschlossen werden; daraus folgt weder PASS noch ein belegter Deadlock. Kein realer GPU-, Flash-Original-Chat-, Unsloth- oder Windows-Zielhosttest in dieser Reviewphase.

Fixphase auf Linux: Sieben neue MCP-Tests bestanden (negative Umschläge, positiver Seal→Start, Wiederholung nach Korrektur). Focused-Regression: 44 Tests, 27 PASS, 17 Windows-spezifische SKIP; ResourceWarnings für SQLite-Verbindungen vorhanden. Ein längerer zusätzlicher Modell-Testlauf erreichte das Zeitlimit und gilt **nicht** als vollständig bestanden. CI auf dem PR steht aus.

**Gesamtentscheidung:** Architektur weiterentwickeln, nicht neu aufbauen; v0.48/v0.49 weiter PRE-ALPHA und PRODUCT NO-GO. Die Nachverfolgung befindet sich im [Maßnahmenplan](REMEDIATION_PLAN_2026-11-03.md) und Issue #70.
