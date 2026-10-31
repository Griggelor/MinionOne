# MinionOne v0.31 – verbindlicher Dateisystem-Zielvertrag und Test-First

**Status:** Produktspezifikation, ausdrücklich noch NICHT implementiert und NICHT freigegeben. Stand 31.10.2026.
**Quellen:** docs/spec_baseline/20_NORMATIVE_ARCHITECTURE_V06.md (§§1–6), 23_TEST_PLAN_V06.md (T-112 bis T-123), 07_TESTSTRATEGIE_TRACEABILITY.md (T-007 bis T-009 und T-091 bis T-105), docs/PRODUCT_TARGET_WORKFLOW.md, die nachfolgenden v0.27–v0.30-Anforderungen für MCP und Live-Logs.

## 1. Wiederhergestellter Zielvertrag statt technischer Zwischenlösung

Die ursprüngliche v0.6-Norm beschreibt drei **sichtbare, echte Dateiverzeichnisse** unter dem einen Job-Root. Eingaben werden versiegelt, Arbeitsdateien werden durch den Broker erzeugt, Ausgaben durch den privilegierten Manager veröffentlicht. Ein Dokumenten-BLOB in SQLite ist keine Arbeitsdatei.

- **input/**: vom Parent/Manager autorisierte Dateien inklusive TASK.md und Manifest; ab Admission unveränderlich; Worker sieht nur verifizierte Inhalte und darf nicht schreiben.
- **work/**: der Worker darf über eng begrenzte Broker-Werkzeuge echte neue Dateien erstellen, lesen und auflisten. Kein Worker bekommt allgemeine OS-Dateihandles oder Hostpfade. ADR-020 §3 verbietet in der v0.6-MVP-Version Überschreiben, Anhängen, Löschen, Umbenennen, chmod und Ausführen.
- **output/**: exklusiv der Manager. Er publiziert einen Hash-verifizierten Snapshot der tatsächlich existierenden Work-Datei und schreibt Logdateien. Der Worker kann weder Output noch Logs lesen oder fälschen.

**Präzisierung zu „bearbeiten“:** Im ursprünglichen Create-only-MVP bedeutet es: Inhalte in realen work/-Dateien erzeugen, lesen und neue Arbeitsstände unter neuen Dateinamen herstellen (z. B. bericht.v2.md). Ein echtes In-place-Edit/Patch/Overwrite bestehender Dateien ist eine neue, gesondert zu genehmigende Funktion. Das soll die v0.31-Tests nicht implizit freischalten.

**Aktueller IST-Verstoß gegen den Ziel-Dateivertrag:** src/minionone/pilot_jobs.py hält input/work/output-Dokumenttexte in pilot_files(zone, name, content, digest); DocumentBroker.create_work erzeugt derzeit keine physische work/-Datei. Der Manager publiziert die ausgewählten Textinhalte später in output/. Das war ein isolierter Test-Backendpfad und darf nicht stillschweigend als Produkt-Architektur gelten.

**Output-Pfadabweichung:** ADR-020 v0.6 beschreibt output/artifacts/, der aktuelle Manager veröffentlicht flach in output/. Für den ersten positiven Test wird vorläufig der vorhandene output/result.txt akzeptiert, damit die eigentliche Work-Datei-Lücke unabhängig nachgewiesen wird. Der endgültige Artefaktpfad muss vor Beta ausdrücklich einheitlich entschieden und versioniert werden.

## 2. SQLite bewusst hinterfragen und Aufgaben sauber trennen

Die v0.6-Norm verlangt einen dauerhaften transaktionalen Manager-Store (Status, Authentifizierung, Version/CAS, Attempt-Leases, Quotas, Fencing, Idempotenz, Outbox und Receipts). Sie fordert **nicht SQLite als zwingende Technologie** und insbesondere **nicht Worker-Dateiinhalte in SQLite**.

| Datentyp | Verbindliche primäre Quelle im Ziel |
|---|---|
| Echte versiegelte Originaleingaben | input/-Dateien + verifizierter Input-Snapshot |
| Echte Arbeitsinhalte und Arbeitsstände | work/-Dateien, niemals bloße virtuelle DB-Dokumente |
| Verifizierte Ergebnis-Bytes | output/-Dateien aus gepinnten echten Work-Bytes |
| Rohes Worker-I/O und tatsächlich geliefertes Thinking | output/worker-io.jsonl, unmittelbar synchronisiert |
| Redigiertes Job- und Manager-Debugging | output/job.log.jsonl und state/logs/*.jsonl |
| Managerstatus, Sicherheit, Idempotenz und Transaktionen | Durable Control-Plane-Store, Technologie separat zu entscheiden |
| Dateimetadaten/Receipts | Hash, Größe, Name, Attempt und Status, keine primäre Work-Dokumentkopie |

**Vorläufige technische Bewertung:** SQLite ist für die atomaren Manager-Transaktionen begründbar und muss nicht aus Prinzip entfernt werden. Die Inhalte der Worker-Workdateien müssen dagegen aus dem SQLite-Dokumentbackend heraus. Ein Totalumbau auf JSON-Dateien als Ersatz für transaktionale Statusupdates wäre erst nach nachgewiesenen CAS-/Locking-/Crash-/Multi-Process-Tests zulässig. Ziel ist keine neue DB-Architektur, solange die originale Dateisystemfunktion fehlt.

## 3. Verbindlicher Endanwender-Workflow (zunächst nur synthetisch)

1. Der Operator registriert einen erlaubten lokalen Projekt-Root. Flash greift über authentifiziertes MCP zu; das Worker-Modell ist in LM Studio nachweisbar geladen.
2. Direktauftrag: Flash ruft minionone_delegate mit begrenzten Textdateien auf. Alternativ: prepare_workspace, autorisierter Dateiimport, seal_workspace, start_workspace_job. In beiden Fällen genau eine stabile Job-ID.
3. Worker liest die versiegelte Eingabedatei mit read_file. Er erstellt per create_work eine **tatsächlich unter work/ sichtbare** Datei, liest sie bei Bedarf wieder mit read_file(work/name).
4. Worker schlägt diese echte Datei mit publish_artifact vor. Der Manager liest die tatsächlichen Work-Bytes über sicheren Broker, prüft Quelle/Digest/Job/Attempt/No-Replace und veröffentlicht ausschließlich in output/. Keine Publikation aufgrund einer bloßen SQLite-Textzeile.
5. Worker-Anfragen, Antworten, verfügbar übermitteltes Thinking, Toolargumente und Toolergebnisse sind während des Laufes in output/worker-io.jsonl lesbar; das redigierte Managerdebug bleibt separat. Crash, Log-/DB-Divergenz oder ungewisse Providerwirkung dürfen keinen falschen Erfolg erzeugen.
6. Flash prüft Ergebnis UND Worker-I/O inhaltlich und zeigt dem Menschen verständlichen Status, Auffälligkeiten und Ergebnis. Nur nach verifizierter Integration darf der exakte Digest per autorisiertem ACK zu COMPLETED führen. Der derzeitige technische Digest-ACK **beweist noch keine echte Kundenintegration**.
7. Rückfrage/Antwort/Weiterarbeit derselben Job-ID, Rückkehr in denselben Unsloth-Chat und Parent-Residency bleiben zusätzliche explizite Zielsystem-Gates, nicht heimlich aus Mock-E2E abzuleiten.

**Kodieren/Testen:** Die normative v0.6 verbietet unkontrollierte Code-/Shell-/Testausführung. Reale Code-Dateien erstellen ist nicht automatisch sichere Codeausführung. Ein Coding-Profil mit Testausführung benötigt später eine getrennte genehmigte Ausführungs- und Sandbox-Spezifikation.

## 4. Die sechs Worker-Werkzeuge – Zielzustand

- list_files(zone): Input und Work zeigen echte erlaubte Dateinamen; Output ist gesperrt.
- read_file(path): über den abgesicherten Broker, ausschließlich Input und Work; echte Bytes statt einer SQLite-Dateisimulation.
- create_work(name, content): sichere handle-relative Erstellung einer vorher nicht vorhandenen echten Work-Datei, fsync, Hash, begrenzte Quotas, dauerhaftes Receipt; kein Overwrite/Delete/Rename.
- publish_artifact(name): echtem Work-Artefakt zurechenbarer, managerseitiger Output-Snapshot; ungültiges/fehlendes Work-Artefakt wird abgewiesen.
- report_progress(percent): nur typisierte redigierte Statusmetadaten.
- ask_question(question): pausiert, kein automatischer zweiter Providerlauf, Resume separat als explizite Funktion testen.

**Idempotenz:** Aktuelle Worker-Toolargumente von create_work enthalten kein operation_id. Ein spätere erfolgreiche Umsetzung braucht einen vertrauenswürdigen Schlüssel aus Job, Attempt und Provider-tool_call_id oder einen bewusst versionierten Toolvertrag. Dies darf im Testergebnis nicht als bereits implementiert gelten.

## 5. Test-First: erst positive rote Produktverträge, dann Implementierung

1. Dieser Sollvertrag und produkt_acceptance/WORKFILES_V031.json werden versioniert.
2. tests für echte Work-Dateibytes werden als **separat ausführbare Zieltests** auf Windows und Ubuntu bereitgestellt. Nur der externe LM-Studio-Modellserver darf ein deterministischer lokaler echter HTTP-Fixture sein; MCP-Server, HTTP-Provideradapter, Broker, SQLite und Dateipfade laufen durch Produktionscode.
3. Der Evidence-Collector speichert die beobachteten roten positiven Ergebnisse und OS/Commit/Case-ID und etikettiert sie explizit **TARGET_CONTRACT_NOT_MET**; ein grüner Collector ist kein grüner Produktkontrakt. Im Strict-Modus erzwingt eine rote Assertion einen Nichtnull-Exitcode.
4. Produktionsimplementation beginnt **erst nach** diesem roten Nachweis. Dann als NativeWorkBroker für POSIX fd-/Windows handle-relative sichere Datei-Create-only-Operationen mit No-Replace, Same-Volume/FileId/Reparse-/Hardlink-/ADS-/UNC-Abwehr, atomarem Receipt und Crash-Replay.
5. Nach Funktionsergänzung dürfen die positiven Fälle auf Windows/Ubuntu grün werden; dann negative Attack-, Concurrent-CAS-, Disk-Full- und Prozesskill-Tests und echter Unsloth/LM-Studio-Zielsystemlauf. Produktfreigabe bleibt NO-GO.

## 6. Testfallmatrix und Trennung der Beweisklassen

| Fall | Baseline | Erwartung | Testumgebung | Status vor Funktion |
|---|---|---|---|---|
| WF-001 | ADR-020 §3 / T-112 | Echte work/result.txt mit exakt übertragenen Bytes | Produktions-MCP + echter HTTP-Testprovider, Win/Linux | ROT erwartet |
| WF-002 | T-112/T-121 | Output-Bytes stammen nachweislich aus realer Work-Datei, Work bleibt erhalten | gleicher End-to-End-Pfad | ROT erwartet |
| WF-003 | ADR-020 §3 | SQLite-Pilot-Tabelle ist nicht alleinige Quelle der Work-Bytes | realer Worker + SQLite-Inspektion | ROT erwartet |
| WF-004 | T-113/T-114 | Kein Overwrite, inkl. Windows-Casefold-Kollision | native Win32/POSIX/HTTP | Geplant |
| WF-005 | T-115/T-116 | Identischer Create-Replay idempotent; veränderte Bytes denied | G2 + native Broker | Geplant |
| WF-006 | T-119/T-120/T-144–148 | Kein Input-Write, Traversal/Junction/Hardlink/ADS/UNC/anderer Benutzer denied | real Windows-Zielhost | Geplant |
| WF-007 | T-118/T-123 | Output/logs für Worker unzugänglich und unveränderlich | native + HTTP | Geplant |
| WF-008 | T-121/T-122 | Kein Publish ohne echte Work-Quelle / gültigen Hash | native + HTTP | Geplant |
| WF-009 | T-125/T-149–152 | Crash nach Work-fsync vor Receipt: kein false-ready / keine Duplikate | Prozess-Kill/Reconciliation | Geplant |
| WF-010 | T-007/FR-09 | Rückfrage → Antwort → Resume derselben Job-ID | Parent-E2E | Geplant |
| WF-011 | T-008/T-100–103 | Exaktes Worker-I/O/Thinking und getrenntes Debug, crash-lesbar | E2E + Kill | teilweise v0.30 |
| WF-012 | T-009/T-104/T-155 | Echte Integration vor ACK, Same-Chat | echter Unsloth-Zielhost | Geplant |
| WF-013 | Neues Kunden-Gate | Geführte Einrichtung ohne manuelles MCP/Bearer-Fachwissen | UX/Windows | Geplant |
| WF-014 | Neues Kunden-Gate | vollständige Kundenaufgabe: Ergebnis, Kontrolle, Recovery | UX/Windows | Geplant |
| WF-015 | ADR-020 §4 | Crash-/DB-Korruptionswiederherstellung, keine Worker-Bytes nur in DB | durable G2 | Geplant |
| WF-016 | ADR-020 §6 | Keine unautorisierte Shell/Test-/Git-Ausführung | adversarial | teilweise v0.30 |

**Begrenzung der ersten Test-First-Lieferung:** Die automatischen roten positiven Zieltests WF-001 bis WF-003 sind jetzt der erste neue Beweis. Die übrigen Fälle werden katalogisiert, aber weder stillschweigend als umgesetzt noch als grün deklariert. Die bisherigen v0.30-HTTP-E2Es bleiben nützlich, beweisen aber nicht, dass eine Work-Datei tatsächlich auf dem Host existiert.

## 7. Verantwortlichkeiten / Freigaberegel

- Produkt/Architektur: einzige Sollversion dieser Dateiarbeitskontrakte und ausdrücklicher Beschluss vor In-place-Edits, Codeausführung oder DB-Systemwechsel.
- Testarchitekt: roter echter Work-Vertrag zuerst; OS-/Run-/Commit-Evidenz, dann negative/fehlerinduzierte G1b/G2-Tests.
- Implementierer: kein „neues SQLite-Arbeitsdokument“ als Ersatz für Work-Dateien; keine direkte Model-OS-Berechtigung.
- Qualität: separate Werte SOURCE/SYNTH-E2E/TARGET; ohne echte Kunden-/Unsloth-/Flint-Validierung kein Produkt-PASS.
- UX: Einrichtung/Ergebnisprüfung/Übernahme/Recovery als reale Anwenderaufgabe messen.

**Release:** weiterhin NO-GO für Echtdaten und für autonome Entwicklungsaufgaben, solange echte Work-Dateien, sichere native Grenzen, Kunden-Integration und Zielhosttests fehlen.
