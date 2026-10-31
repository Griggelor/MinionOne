# MinionOne v0.31 – verbindlicher Dateisystem-Zielvertrag und Test-First

**Status:** Produktspezifikation, ausdrücklich noch NICHT implementiert und NICHT freigegeben. Stand 31.10.2026. **Nachtrag v0.31.1, ausdrückliche Kundenvorgabe:** `work/` erhält vollständige Dateirechte **einschließlich Ändern, Überschreiben, Umbenennen und Löschen**. `input/` ist lesbar und unveränderlich. `output/` erlaubt das **erstmalige Anlegen** von Ergebnissen und optionales Lesen bereits veröffentlichter Ergebnisse, jedoch **kein Ändern, Überschreiben, Umbenennen oder Löschen** existierender Dateien. Dieser Nachtrag ersetzt die alten Create-only-Beschränkungen für `work/` aus ADR-020 v0.6 und T-113–T-116. Historische Dokumente bleiben nur als historische Baseline.
**Quellen:** docs/spec_baseline/20_NORMATIVE_ARCHITECTURE_V06.md (§§1–6), 23_TEST_PLAN_V06.md (T-112 bis T-123), 07_TESTSTRATEGIE_TRACEABILITY.md (T-007 bis T-009 und T-091 bis T-105), docs/PRODUCT_TARGET_WORKFLOW.md, die nachfolgenden v0.27–v0.30-Anforderungen für MCP und Live-Logs.

## 1. Wiederhergestellter Zielvertrag statt technischer Zwischenlösung

Die ursprüngliche v0.6-Norm beschreibt drei **sichtbare, echte Dateiverzeichnisse** unter dem einen Job-Root. Eingaben werden versiegelt, Arbeitsdateien werden durch den Broker erzeugt, Ausgaben durch den privilegierten Manager veröffentlicht. Ein Dokumenten-BLOB in SQLite ist keine Arbeitsdatei.

- **input/**: vom Parent/Manager autorisierte Dateien inklusive TASK.md und Manifest; ab Admission unveränderlich; Worker sieht nur verifizierte Inhalte und darf nicht schreiben.
- **work/**: echtes, **vollständig durch den Worker bearbeitbares Arbeitsverzeichnis**. Innerhalb der isolierten Job-Grenze darf der Worker Dateien und Unterverzeichnisse erstellen, lesen, bearbeiten, überschreiben, umbenennen, verschieben und löschen. Dies betrifft **Dateirechte, nicht die Freigabe allgemeiner Shell-/Netz-/Prozessausführung**. Über einen sicheren Broker bzw. eine nachgewiesene OS-Sandbox müssen alle Operationen innerhalb des Work-Roots bleiben, einschließlich Schutz vor Symlinks, Reparse Points, Hardlinks und parallelen Änderungen.
- **output/**: Worker darf **neue** Ergebnisdateien einmalig schreiben/als Veröffentlichung anfordern und bereits veröffentlichte **Artefakte** gegebenenfalls lesen. **Vorhandene** Dateien dürfen nicht verändert, ersetzt, umbenannt oder gelöscht werden. Zur sicheren Umsetzung veröffentlicht zunächst der Manager auf Worker-Anforderung atomar und no-replace aus dem aktuellen Work-Snapshot. Das sind **wirksame Worker-Dateirechte**, nicht notwendigerweise ein frei beschreibbarer OS-Handle auf den Output-Root. Manager-Logs `output/worker-io.jsonl` und `output/job.log.jsonl` sind ausdrücklich NICHT durch Worker les-, veränder- oder erzeugbar; für Artefakte ist langfristig `output/artifacts/` als abgeschotteter Teilbaum vorgesehen. Die aktuelle flache Ausgabe wird bis zur vereinbarten Migration nur als historischer IST-Pfad geführt.

**Verbindliche Präzisierung zu „bearbeiten“:** Der Worker darf `work/bericht.md` erstellen, mehrfach **unter demselben Namen** verändern und speichern, in Unterverzeichnisse verschieben/umbenennen und bei Bedarf auch wieder löschen. Dies ist **nicht** nur die Create-only-Variante mit neuem Dateinamen. In der brokerbasierten Implementierung sind atomare Datei-Replace-Operationen mit Version/Digest-Prüfung und sichere Rename/Delete/Mkdir-Operationen erforderlich. Dateiänderung und Ergebnisveröffentlichung müssen strikt getrennt sein: Ein bereits veröffentlichtes `output/bericht.md` bleibt unverändert, selbst wenn `work/bericht.md` später geändert oder gelöscht wird.

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
3. Worker liest die versiegelte Eingabedatei mit `read_file`, erstellt eine **echte** Datei unter `work/`, verändert ihren Inhalt mehrfach, benennt sie um, erstellt bei Bedarf Unterordner und kann nicht mehr benötigte Work-Dateien löschen. Alle Schritte müssen auf dem echten Dateisystem sichtbar und über den Workerzugang verifizierbar sein.
4. Worker schreibt ein neues Ergebnis nach `output/` über die kontrollierte `publish_artifact`-Operation. Der Manager prüft die **zu diesem Zeitpunkt aktuellen** echten Work-Dateibytes und veröffentlicht sie atomar **nur bei noch nicht vorhandenem Output-Zielnamen**. Nachträgliche Änderungen/Löschungen in `work/` verändern den veröffentlichten Output-Snapshot niemals. Derselbe Output-Dateiname kann durch den Worker nicht überschrieben werden. Keine Publikation aufgrund einer bloßen SQLite-Textzeile.
5. Worker-Anfragen, Antworten, verfügbar übermitteltes Thinking, Toolargumente und Toolergebnisse sind während des Laufes in output/worker-io.jsonl lesbar; das redigierte Managerdebug bleibt separat. Crash, Log-/DB-Divergenz oder ungewisse Providerwirkung dürfen keinen falschen Erfolg erzeugen.
6. Flash prüft Ergebnis UND Worker-I/O inhaltlich und zeigt dem Menschen verständlichen Status, Auffälligkeiten und Ergebnis. Nur nach verifizierter Integration darf der exakte Digest per autorisiertem ACK zu COMPLETED führen. Der derzeitige technische Digest-ACK **beweist noch keine echte Kundenintegration**.
7. Rückfrage/Antwort/Weiterarbeit derselben Job-ID, Rückkehr in denselben Unsloth-Chat und Parent-Residency bleiben zusätzliche explizite Zielsystem-Gates, nicht heimlich aus Mock-E2E abzuleiten.

**Kodieren/Testen:** Die normative v0.6 verbietet unkontrollierte Code-/Shell-/Testausführung. Reale Code-Dateien erstellen ist nicht automatisch sichere Codeausführung. Ein Coding-Profil mit Testausführung benötigt später eine getrennte genehmigte Ausführungs- und Sandbox-Spezifikation.

## 4. Worker-Werkzeuge – Zielzustand (bestehende sechs plus vollständige Work-Dateioperationen)

- list_files(zone): reale Eingaben, vollständigen Work-Inhalt einschließlich Unterordnern und optional bereits veröffentlichte **Output-Artefakte** auflisten. Manager-Logs/SQLite niemals über den Workerdateikanal freigeben.
- read_file(path): reale Bytes aus `input/` und `work/`; optional bereits veröffentlichte `output/`-Artefakte lesen, **keine Manager-Logs** oder andere Hostpfade.
- create_work(name, content): echte Datei in `work/` erstellen, atomar sichern und mit Receipt dokumentieren. Zusätzlich erforderliche Workeroperationen: `write_work(path, content, expected_digest)` zum atomaren **Ersetzen** einer Work-Datei, `delete_work(path)` für echtes Löschen, `rename_work(source, destination)` für Umbenennen/Verschieben innerhalb von `work/`, `mkdir_work(path)` für Unterordner. Bei idempotentem Replay, Quotas und unerwarteten Dateiversionen sichere Fehler statt stiller Nebenwirkungen. Die genauen API-Argumente und Tool-IDs sind Gegenstand der verbindlichen v0.31.1-Implementierung und der roten E2E-Tests.
- publish_artifact(name): **neues** Output-Artefakt aus dem geprüften aktuellen Work-Dateistand; **no-replace** für Output, selbst wenn Work später geändert oder gelöscht wird. Worker dürfen weder bereits veröffentlichte Outputs noch Prozess- und Worker-I/O-Logs ändern.
- report_progress(percent): nur typisierte redigierte Statusmetadaten.
- ask_question(question): pausiert, kein automatischer zweiter Providerlauf, Resume separat als explizite Funktion testen.

**Idempotenz:** Aktuelle Worker-Toolargumente von create_work enthalten kein operation_id. Ein spätere erfolgreiche Umsetzung braucht einen vertrauenswürdigen Schlüssel aus Job, Attempt und Provider-tool_call_id oder einen bewusst versionierten Toolvertrag. Dies darf im Testergebnis nicht als bereits implementiert gelten.

## 5. Test-First: erst positive rote Produktverträge, dann Implementierung

1. Dieser Sollvertrag und produkt_acceptance/WORKFILES_V031.json werden versioniert.
2. tests für echte Work-Dateibytes werden als **separat ausführbare Zieltests** auf Windows und Ubuntu bereitgestellt. Nur der externe LM-Studio-Modellserver darf ein deterministischer lokaler echter HTTP-Fixture sein; MCP-Server, HTTP-Provideradapter, Broker, SQLite und Dateipfade laufen durch Produktionscode.
3. Der Evidence-Collector speichert die beobachteten roten positiven Ergebnisse und OS/Commit/Case-ID und etikettiert sie explizit **TARGET_CONTRACT_NOT_MET**; ein grüner Collector ist kein grüner Produktkontrakt. Im Strict-Modus erzwingt eine rote Assertion einen Nichtnull-Exitcode.
4. Produktionsimplementation beginnt **erst nach** dem roten Nachweis. Dann als nativer Work-Dateizugriff mit vollständigem CRUD einschließlich Edit/Replace, Rename/Move, Mkdir und Delete. Implementierung auf POSIX fd-/Windows handle-relativen sicheren Pfaden bzw. nachgewiesener Work-Sandbox, Same-Volume/FileId/Reparse-/Hardlink-/ADS-/UNC-Abwehr, atomaren Receipts und Crash-Replay. **No-replace gilt für Output**, nicht als Work-weites Edit-Verbot.
5. Nach Funktionsergänzung dürfen die positiven Fälle auf Windows/Ubuntu grün werden; dann negative Attack-, Concurrent-CAS-, Disk-Full- und Prozesskill-Tests und echter Unsloth/LM-Studio-Zielsystemlauf. Produktfreigabe bleibt NO-GO.

## 6. Testfallmatrix und Trennung der Beweisklassen

| Fall | Baseline | Erwartung | Testumgebung | Status vor Funktion |
|---|---|---|---|---|
| WF-001 | ADR-020 §3 / T-112 | Echte work/result.txt mit exakt übertragenen Bytes | Produktions-MCP + echter HTTP-Testprovider, Win/Linux | ROT erwartet |
| WF-002 | T-112/T-121 | Output-Bytes stammen nachweislich aus realer Work-Datei, Work bleibt erhalten | gleicher End-to-End-Pfad | ROT erwartet |
| WF-003 | ADR-020 §3 | SQLite-Pilot-Tabelle ist nicht alleinige Quelle der Work-Bytes | realer Worker + SQLite-Inspektion | ROT erwartet |
| WF-004 | T-113/T-114 (historische Konfliktregel ersetzt) | Work-Overwrite **erlaubt**, jedoch nur innerhalb Work und kontrolliert bei Windows-Casefold-Kollision; Output-Overwrite verboten | native Win32/POSIX/HTTP | Geplant |
| WF-005 | T-115/T-116 (aktualisiert) | Repeat Write/Rename/Delete idempotent; konkurrierende Work-Versionen sauber erkannt, Output bleibt unveränderlich | G2 + native Broker | Geplant |
| WF-006 | T-119/T-120/T-144–148 | Kein Input-Write, Traversal/Junction/Hardlink/ADS/UNC/anderer Benutzer denied | real Windows-Zielhost | Geplant |
| WF-007 | T-118/T-123 (aktualisiert) | Worker darf neue Output-Artefakte publizieren/optional lesen, aber weder bestehende Outputs noch Manager-Logs verändern | native + HTTP | Geplant |
| WF-008 | T-121/T-122 | Kein Publish ohne echte Work-Quelle / gültigen Hash | native + HTTP | Geplant |
| WF-009 | T-125/T-149–152 | Crash nach Work-fsync vor Receipt: kein false-ready / keine Duplikate | Prozess-Kill/Reconciliation | Geplant |
| WF-010 | T-007/FR-09 | Rückfrage → Antwort → Resume derselben Job-ID | Parent-E2E | Geplant |
| WF-011 | T-008/T-100–103 | Exaktes Worker-I/O/Thinking und getrenntes Debug, crash-lesbar | E2E + Kill | teilweise v0.30 |
| WF-012 | T-009/T-104/T-155 | Echte Integration vor ACK, Same-Chat | echter Unsloth-Zielhost | Geplant |
| WF-013 | Neues Kunden-Gate | Geführte Einrichtung ohne manuelles MCP/Bearer-Fachwissen | UX/Windows | Geplant |
| WF-014 | Neues Kunden-Gate | vollständige Kundenaufgabe: Ergebnis, Kontrolle, Recovery | UX/Windows | Geplant |
| WF-015 | ADR-020 §4 | Crash-/DB-Korruptionswiederherstellung, keine Worker-Bytes nur in DB | durable G2 | Geplant |
| WF-016 | ADR-020 §6 | Keine unautorisierte Shell/Test-/Git-Ausführung | adversarial | teilweise v0.30 |

**Erweiterung v0.31.1:** WF-017 bis WF-022 führen zusätzlich positive **rote** Nachweise für echtes Work-Overwrite, Delete, Rename, Unterordner sowie Output-Create-only/Read-only und die Input-Grenze ein. Die anfänglich nur drei roten Tests WF-001 bis WF-003 bleiben die Baseline; die Testumgebung wird auf insgesamt neun positive Zieltests erweitert. Die automatischen Nachweise gelten erst mit GitHub-CI für den geänderten Commit. Die übrigen Fälle werden katalogisiert, aber weder stillschweigend als umgesetzt noch als grün deklariert. Die bisherigen v0.30-HTTP-E2Es bleiben nützlich, beweisen aber nicht, dass eine Work-Datei tatsächlich auf dem Host existiert.

## 7. Verantwortlichkeiten / Freigaberegel

- Produkt/Architektur: Work-Edit/Delete/Rename und Output-Create-only sind ausdrücklich freigegeben; **nur** freie Code-/Shellausführung oder der Ersatz der Control-Plane-Datenbank brauchen gesonderte Produkt- und Sicherheitsentscheidungen.
- Testarchitekt: roter echter Work-Vertrag zuerst; OS-/Run-/Commit-Evidenz, dann negative/fehlerinduzierte G1b/G2-Tests.
- Implementierer: kein „neues SQLite-Arbeitsdokument“ als Ersatz für Work-Dateien; keine direkte Model-OS-Berechtigung.
- Qualität: separate Werte SOURCE/SYNTH-E2E/TARGET; ohne echte Kunden-/Unsloth-/Flint-Validierung kein Produkt-PASS.
- UX: Einrichtung/Ergebnisprüfung/Übernahme/Recovery als reale Anwenderaufgabe messen.

### Ergänzende Test-First-Verträge v0.31.1

| Fall | Erwartung | Ebenen |
|---|---|---|
| WF-017 | Datei in `work/` erstellen, danach denselben Pfad mit geändertem Inhalt schreiben; neue Bytes müssen bei erneutem Read sichtbar und publizierbar sein | echtes MCP → HTTP Worker → nativer Broker |
| WF-018 | Eine Work-Datei erstellen, danach löschen; Datei ist anschließend nicht mehr vorhanden und nicht mehr publizierbar | HTTP E2E, Filesystem |
| WF-019 | Work-Datei umbenennen/verschieben; alter Pfad fehlt, neuer Pfad enthält Originalbytes | HTTP E2E, Windows/POSIX |
| WF-020 | Neue Output-Artefakte erstellen; vorhandene Ergebnisse dürfen weder überschrieben, gelöscht noch umbenannt werden, selbst wenn Work später verändert wird; veröffentlichte Artefakte lesbar | HTTP E2E, Manager-Publisher |
| WF-021 | Input bleibt für Worker lesbar, jeder Schreib-/Delete-/Rename-Versuch wird verworfen | native/HTTP Negativtests |
| WF-022 | Work darf Unterordner erstellen und verwalten, aber keine Pfade außerhalb Work erreichen | native/HTTP + Security-Races |

Die alten Create-only-Regeln aus der historischen Baseline sind **durch die ausdrücklich neue Kundenvorgabe überholt**. Diese Tests dürfen nicht als `PASS` ausgewiesen werden, solange die neue Funktion fehlt.

**Release:** weiterhin NO-GO für Echtdaten und für autonome Entwicklungsaufgaben, solange echte Work-Dateien, sichere native Grenzen, Kunden-Integration und Zielhosttests fehlen.
