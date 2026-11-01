# MinionOne – aktueller Requirements-Einstiegspunkt (Stand 2026-10-31)

> **Status: Development / PRE-ALPHA / PRODUCT NO-GO.**
> v0.43 vereinheitlicht Windows-Paket/Runtime auf `0.43.0.dev0` und macht den
> aktuellen physischen Workspace-Test direkt über die nummerierten Starter
> zugänglich. [Paket-Anleitung](../system_test/README_DE.md). Produkt NO-GO bleibt.
> v0.42 ergänzt ausschließlich den festen synthetischen AppContainer-Dateipfad mit isoliertem Input, Work und Manager-Output. Vorläufiger CI-Kandidat in [PR #61](https://github.com/Griggelor/MinionOne/pull/61); keine Freigabe für beliebigen Worker-Code, Echtdaten, Zielhost oder allgemeine Dateisystemimporte. [Grenzen](WIN32_SYNTHETIC_FILE_HANDOFF_V042.md).
> Dieses Dokument ist der **Wegweiser für gültige Requirements**, kein Ersatz für die
> vollständigen normativen Anforderungen, Tests oder Zielhost-Nachweise.
> Es stellt explizit klar, welche älteren Texte durch spätere Owner-Entscheidungen
> ersetzt wurden. `main` ist der gemeinsame Entwicklungsstand; ein grüner CI-Run
> bedeutet weder vollständige Requirement-Abdeckung noch Freigabe für Echtdaten.

## 1. Dokumentenrangfolge bei Widersprüchen

1. **Explizite neueste Produktowner-Entscheidungen**, dokumentiert in
   [SPEC_V031_FILESYSTEM_FIRST_TESTFIRST.md](SPEC_V031_FILESYSTEM_FIRST_TESTFIRST.md)
   und ihrem v0.31.1-Nachtrag vom 31.10.2026.
2. **Nicht ersetzte normative Baseline**
   [20_NORMATIVE_ARCHITECTURE_V06.md](spec_baseline/20_NORMATIVE_ARCHITECTURE_V06.md),
   [23_TEST_PLAN_V06.md](spec_baseline/23_TEST_PLAN_V06.md) sowie
   [07_TESTSTRATEGIE_TRACEABILITY.md](spec_baseline/07_TESTSTRATEGIE_TRACEABILITY.md)
   und [16_TRACEABILITY_SECURITY_NFR.md](spec_baseline/16_TRACEABILITY_SECURITY_NFR.md).
3. **Spätere ergänzende ADRs und Implementierungsspezifikationen**, soweit sie
   1 und 2 nicht widersprechen; zum aktuellen IST-Schnitt
   [IMPLEMENTATION_V0312_POSIX_WORK.md](IMPLEMENTATION_V0312_POSIX_WORK.md)
   und [QUALITY_REVIEW_V030_E2E_CI.md](QUALITY_REVIEW_V030_E2E_CI.md).
4. `README.md`, `DEV_STATUS.md`, `CHANGELOG.md` und ältere Pilot-ADRs sind
   **historische Implementierungs- und Statusberichte**, keine Erlaubnis zur
   stillschweigenden Abschwächung des Zielvertrags.

## 2. Wo die Anforderungen und ihre Tests stehen

| Bereich | Primärquelle / Testkatalog | Einordnung |
|---|---|---|
| Gesamtablauf Flash-Next/Unsloth → MCP → MinionOne → Flint/LM Studio → Ergebnis/ACK | [PRODUCT_TARGET_WORKFLOW.md](PRODUCT_TARGET_WORKFLOW.md), v0.6-Norm | Ziel; echte Same-Chat-Integration offen |
| Input-/Work-/Output-Dateirechte | [SPEC_V031_FILESYSTEM_FIRST_TESTFIRST.md](SPEC_V031_FILESYSTEM_FIRST_TESTFIRST.md), `product_acceptance/WORKFILES_V031.json` | **Neueste gültige Änderung**, 22 WF-ID-Anforderungen |
| Job-States, Manager, CAS, Attempts, Fencing, Outbox, Recovery | v0.6-Norm, `ARCHITECTURE_AND_RELEASE_GATES.md`, G2-ADRs | Teilweise implementiert; vollständige Mehrfachversuch-Recovery offen |
| Sichere OS-Isolation, Scope, Input Seal, Windows NTFS | v0.6-Norm, `docs/G1B_WINDOWS_API_FEASIBILITY.md`, v0.21–25-ADRs | Windows G1b Target-Hardware-Gate **offen** |
| Worker-I/O, Thinking, Debug, Fehler | `ADR_029_EXACT_WORKER_IO_OUTPUT.md`, `G2_ACTIONABLE_ERRORS.md`, Issue #29 | Teilweise implementiert; Original-Chat-/Fehler-UX offen |
| Produkt- und Sicherheitsabnahme | `product_acceptance/TARGET_CASES.json`, `product_acceptance/WORKFILES_V031.json`, `spec_baseline/23_TEST_PLAN_V06.md` | 182 T-ID-Einträge (143 TARGET_PRODUCT, 26 REQUIREMENT_DEFINITION, 13 G2_LOCAL_ONLY) plus 22 WF-Einträge; Registrierung ist noch kein PASS |
| Installations- und Anwender-UX | `INSTALL_GITHUB_ARTIFACT_WINDOWS.md`, WF-013/014, G1a/G3-Issues | Echtes Guided-Setup nicht nachgewiesen |

## 3. Verbindliche File-Permissions ab v0.31.1

- **`input/`** – echte versiegelte Originaldateien; Worker **nur lesen**,
  kein Ändern, Löschen, Umbenennen oder Ersetzen.
- **`work/`** – echte sichtbare Arbeitsdateien; Worker darf **innerhalb des
  isolierten Job-Roots** erstellen, lesen, bearbeiten/überschreiben, verschieben/
  umbenennen, löschen und Unterverzeichnisse verwalten. Sichere Broker-/OS-
  Kontrolle ist Pflicht; keine pauschale Shell-/Host-/Netzberechtigung.
- **`output/`** – neue Artefakte höchstens einmalig über autorisierte
  `publish_artifact`-Operation; bereits veröffentlichte Artefakte optional
  lesbar, aber nicht veränderbar/ersetzbar/löschbar/umbenennbar. Interne
  `worker-io.jsonl` und `job.log.jsonl` bleiben Manager-only.
- `work/`-Dateien sind **physische Dateien**, nicht nur
  `pilot_files(zone='work',content=TEXT)` in SQLite. SQLite bleibt vorläufig
  transaktionaler Manager-Control-Plane-Store.
- Die physische Resultatpublikation muss aus einem **verifizierten aktuellen
  Work-Snapshot** erfolgen. Publizierte Bytes bleiben unverändert, auch wenn
  Work später verändert oder gelöscht wird.
- Die endgültige Migration von bisher flachem `output/<name>` zu
  `output/artifacts/<name>` ist **noch offen** und erfordert eine
  eindeutige aktualisierte Pfad-/Migrationsentscheidung.

## 4. Aktuell überholte Aussagen, die NICHT mehr gelten

- "Worker hat genau sechs Tools" – nur noch für den **Legacy-Pilot** richtig;
  der produktive Work-Worker braucht zusätzliche CRUD-Werkzeuge.
- "Work ist create-only / jede Änderung braucht neuen Dateinamen" – ersetzt
  durch volle kontrollierte Work-CRUD-Rechte.
- "Ein leeres `work/` nach erfolgreicher Ausführung ist korrekt" – gilt
  nicht für das neue physische Work-Backend.
- "SQLite-Dokumenttext ist das primäre Work-Dateisystem" – explizit kein
  Produktziel mehr.
- "Grüne synthetische HTTP-CI / Windows-Feasibility = Echtsystemfreigabe" –
  unzutreffend. Nur nach G1a/G1b/G1c/G2/G3 und echtem Target-Host-Test.

## 5. Umsetzung und verbleibende Lücken

- v0.30: echte Jobprotokolle, Process/Worker-Logs und synthetisches Full Bridge
  E2E; **kein** eigenständiger Target-Host-Beleg.
- v0.31.1: 22 Work-Anforderungen und neun ausführbare WF-001–003, WF-017–022.
- v0.31.2: POSIX-Filesystem-Worker-Broker integriert.
- v0.32-Entwicklungsschritt: Windows-NTFS-Broker für Work-CRUD integriert;
  synthetischer echter MCP-/HTTP-End-to-End-Vertrag auf Ubuntu **9/9**
  und Windows **9/9** bestanden (PR #50, 21/21 GitHub-CI-Checks am
  Implementierungs-Commit). **Nicht** gleichbedeutend mit G1b-Zielhost-
  Sicherheitsprüfung oder Freigabe.
- v0.33: Crash-left `pending-*`-Work-Zwischenstände werden auf Windows und POSIX
  erkannt; mutierende Worker-Aufrufe und `RESULT_READY` werden bei unklarem
  Dateizustand verweigert. Die neue Operatorprobe verifiziert echte Work-Bytes
  vor dem ACK. [Windows-Testanleitung](WINDOWS_NATIVE_WORK_SYSTEMTEST_V033.md).
  Synthetische CI **23/23** für PR #51 erfolgreich; *kein* G1b-Zielhost-GO.
- v0.34: [ADR-034 Boxed Tool Runtime](ADR_034_BOXED_TOOL_EXECUTION.md) definiert
  das zweistufige Tool-Berechtigungsmodell. Im laufenden Manager sind sechs
  Legacy- bzw. elf Workspace-Werkzeuge fest jobtypgebunden; AgentLoop und
  `BoxedToolGateway` prüfen vor jedem Broker-Zugriff. Shell-/Code-/Testausführung
  ist **weiterhin nicht implementiert**. Die OS-Prozesssandbox ist separat zu
  bauen und auf dem Zielrechner zu verifizieren. PR #52: 23/23 CI.
- v0.35–v0.36: Native Windows-Prozesskontrolle ist testweise implementiert:
  `CreateProcessW(CREATE_SUSPENDED)` → beschränktes Job Object →
  `ResumeThread`, Timeout und Kill-on-close (PR #53: 22/22 CI).
  Danach echter AppContainer-Kindprozess per `STARTUPINFOEX` mit
  zero capabilities und nachgewiesenem `TokenIsAppContainer=1`
  (PR #54: 24/24 CI). Beide sind **ausschließlich interne Probes**,
  weder Worker-Shell noch eine attestierte Sandbox. Daten-/Netzwerk-
  Denialtests, sichere IPC, getrennte Hostrechte und Target-Host-Gate fehlen.
  [Prozesskontrolle](WIN32_JOB_OBJECT_PROBE_V035.md) /
  [AppContainer-Kindtoken](WIN32_APPCONTAINER_TOKEN_V036.md).
- v0.37: [Adversariale AppContainer-Zugriffsversuche](WIN32_APPCONTAINER_ACCESS_DENIAL_V037.md)
  wurden **auf echtem Windows-CI-Runner mit synthetischen Canaries** erfolgreich
  ausgeführt (PR #55: 22/22 CI). Je ein normaler Hostprozess bewies vorher
  tatsächliche Lesbarkeit von Manager-/Fremdjobdateien und Schreibzugriff
  auf synthetischen Output; im AppContainer wurden diese drei Zugriffe
  explizit verweigert und alle Datei-Hashes blieben unverändert.
  Loopbackverbindung: Windows-Host-Gegenversuch positiv, AppContainer-Timeout,
  aber **keine explizite Netz-Zugriffsverweigerung**, somit
  `network_denial_verified=false`. Kein Host-G1b-PASS, kein Worker-Exec-GO.
- v0.38: [Boxed-IPC- und Recovery-Vertrag](ADR_038_BOXED_IPC_AND_RECOVERY.md)
  implementiert versionierte, streng begrenzte Nachrichten (max. 2 KiB),
  feste `PROBE_PING`-Capabilities, eindeutige Job-/Versuchs-/Operations-
  und Nonce-Zuordnung sowie eine dauerhaft transaktionale
  `PREPARED → IN_FLIGHT → RESPONSE_OBSERVED / NEEDS_RECONCILIATION`-Kontrolle.
  Nach unklarem Absturz ist **kein automatischer Replay** erlaubt.
  PR #56: **25/25 CI auf Windows/Ubuntu** mit Prozessabsturz- und
  parallelen Dispatch-Tests. Ein echter Prozess-IPC-Kanal und die
  Verknüpfung zum AppContainer-Prozess fehlen noch; keine Freigabe von
  `run_tests`, Shell oder Echtdaten.
- v0.39: Tatsächliche native Windows-AppContainer-Pipe-Verbindung (nur festes synthetisches PROBE_PING) mit explicit HANDLE_LIST, Kernel-Tokenprüfung, Job-Object-Zuweisung vor Resume, v0.38-Dispatch-Fence und realem PONG-/Exit-Receipt im dedizierten Windows-CI geprüft ([PR #57](https://github.com/Griggelor/MinionOne/pull/57), [Dokumentation](WIN32_BOXED_IPC_V039.md)). Keine Worker-/Shell-/Testausführung, keine Zielhost-Sandboxfreigabe. Der Probelauf verwendet ein temporäres Journal und ist noch nicht in den produktiven AgentLoop integriert.
- v0.40: Native Windows-AppContainer-Fehlertests auf dem tatsächlichen v0.39-Pipepfad: Crash/Timeout lassen das persistierte In-Flight-Fence unangetastet, defekte und scope-fremde IPC-Frames werden dauerhaft quarantänisiert. Die sechs Fälle und v0.37-Dateidenial-Regressionsproben liefen in dedizierter Windows-CI erfolgreich ([PR #58](https://github.com/Griggelor/MinionOne/pull/58), [Details](WIN32_BOXED_FAULT_GATES_V040.md)). Kein allgemeiner Ausführungssandbox- oder Netzwerk-GO, kein Echtsystem-Freigabenachweis.
- v0.41: Zusätzliche Windows-AppContainer-Negativkontrollen: bestätigter vererbbarer Manager-Event-Handle wird im tatsächlichen Kindprozess nicht zugänglich, fester Breakaway-CreateProcess-Versuch bleibt erfolglos und ein lokal erreichbarer Testlistener liefert differenzierte WSAEACCES-/Timeout-Beobachtung. Die neue CI mit v0.38–v0.40-Regressionen war erfolgreich ([PR #60](https://github.com/Griggelor/MinionOne/pull/60), [Detailnachweis](WIN32_BOXED_BOUNDARIES_V041.md)). Keine allgemeine Netzwerk-Isolationsattestierung, keine Model-Code-Ausführung, keine Freigabe für Zielhost/Echtdaten.
- **Noch nicht erfüllt**: vollständiger Windows-Dateibroker mit nachgewiesener
  NTFS-Isolation; echte OS-Sandbox/ACL und nicht-kooperierende Race-Tests;
  Crash-Replay-/Idempotenz-Verträge für Work-Mutationen; sichere Wiederaufnahme
  derselben Job-ID nach Rückfrage/Timeout; zuverlässige Rückkehr zum echten
  Unsloth-Chat und geprüfte Parent-Integration; Installation/UX und
  echte Flint-/LM Studio-/Unsloth-Zielhost-Validierung.
- **Coding-Profil mit Shell-/Testausführung** bleibt bewusst gesperrt:
  Die Norm unterscheidet das Schreiben von Source-Dateien von der genehmigten
  Ausführung von Code/Tests. Dafür fehlt eine separate Sandbox-Spezifikation.

## 6. Evidence-Regeln

Die Zustände **SOURCE_TEST**, **SYNTH_HTTP_E2E**, **WINDOWS_CI_FEASIBILITY**,
**TARGET_HOST_VERIFIED** und **CUSTOMER_PRODUCT_GO** dürfen niemals
gleichgesetzt werden. Jedes Ergebnis gehört zu Commit/SHA, Betriebssystem,
Prüfklasse und Test-ID. GitHub-Workflows, die nur rote Anforderungen
einsammeln, dürfen grün sein und trotzdem `CONTRACT_NOT_MET` berichten.

## 7. Noch zu klärende Produktentscheidungen

- `output/artifacts/`-Migration, Dateinamen/verschachtelte Artefakte;
- Prozess-/OS-Isolation und zulässige Work-Konkurrenzbedingungen;
- safe Resume-/Idempotenz-Protokoll bei unbekannten Worker-Side-Effects;
- gesonderte Coding-/Testausführung (falls als Anwendungsfall gewünscht);
- endgültige Setup- und Same-Chat-Schnittstelle zum tatsächlichen Unsloth-Host.

Diese Entscheidungen sind **nicht** stillschweigend bereits geklärt, nur weil
ältere Test- oder Architekturtexte teilweise Implementierungsdetails enthalten.
