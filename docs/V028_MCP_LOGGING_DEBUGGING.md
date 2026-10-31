# v0.28 — Flash-MCP-Anleitung, dauerhafte Diagnose und Audit

**Nur Entwicklung / Systemtest mit synthetischen Daten. Produktfreigabe: NO-GO.**

## 1. Flash lernt die verfügbaren Werkzeuge über MCP

MinionOne liefert bei MCP \`initialize\` ein \`instructions\`-Feld.
Das MCP-\`tools/list\` enthält zu jedem aktivierten Werkzeug die technische
Beschreibung und die verpflichtenden Parameter. Unsloth muss diese Metadaten
an Flash übergeben; ein realer Unsloth-Aufruf bleibt noch zu verifizieren.

**Weg A (bevorzugt für kleine Textaufgaben):**
\`minionone_delegate\` erhält Projekt-ID, Ziel, Kontext,
1–5 Abnahmekriterien und 1–4 kleine UTF-8-Inhalte. MinionOne erzeugt,
versiegelt und startet den **gleichen kanonischen Job** selbst. Ohne
vom Modell angegebene Hostpfade.

**Weg B (gestaffelte Ordnerübergabe):**
\`minionone_prepare_workspace\`, anschließend legt ein vertrauenswürdiger
**externer** Akteur \`TASK.md\`, \`manifest.json\` und weitere Eingabedateien
in \`input/\` ab. Erst danach folgen \`minionone_seal_workspace\` und
\`minionone_start_workspace_job\`. Flash hat keine lokalen
Host-Dateiwerkzeuge; MCP allein legt die Dateien bei diesem Weg nicht ab.

**Beide Wege:** \`minionone_job_status\` → nach \`RESULT_READY\`
\`minionone_workspace_result\` → explizite Bestätigung per
\`minionone_accept_workspace_result\`. Im Zustand
\`WAITING_FOR_PARENT\` keine automatische Fortsetzung versuchen.

Die älteren \`minionone_*_test_job\`-Werkzeuge werden im
Workspace-Produktmodus nicht mehr angeboten. Der separate Pilotmodus
ohne Workspace-Freigabe bleibt für bestehende isolierte Systemtests erhalten.
Es wurden keine neuen Test-CLIs oder getrennten Job-Manager eingebaut.

## 2. Drei dauerhaft gespeicherte Ebenen

Das neue SQLite-\`job_telemetry\`-Journal ist pro Job fortlaufend nummeriert
und wird im Manager **atomar mit den kanonischen Jobzuständen** geschrieben.
Weder beim Jobabschluss noch beim ACK wird es gelöscht. Die vorhandene
\`admission_events\`-Tabelle bleibt maßgebliche Zustandschronik.

- **JOB:** verständliche Annahme und Übergänge zwischen Jobzuständen.
- **DEBUG:** Beginn/Ende von LM-Studio-Modellaufrufen, gemeldete Token,
  verstrichene Millisekunden und abgeschlossene Toolaufrufe, einschließlich
  fester Ergebnis- und Fehlercodes.
- **AUDIT:** Annahme, Zustandsübergänge und Ausgabe der geprüften
  Ergebnisprüfsumme an den authentifizierten Parent.

Jeder Eintrag enthält Zeit, Job-ID-Bezug, laufende Nummer, vordefinierten
Ereignistyp, streng geprüfte Metadaten, vorherigen SHA-256 und eigenen SHA-256.
Eine SQLite-Trigger-Sperre verbietet gewöhnliches \`UPDATE\`/\`DELETE\`.
Beim Lesen verifiziert der Manager **die gesamte** bis zu 2048 Einträge
umfassende Kette, bevor er eine Seite zurückgibt.

Dies ist **Manipulationserkennung bei gewöhnlichen DB-Änderungen**, aber
keine signierte, gegen einen vollständig kompromittierten lokalen
Administrator geschützte externe Beweissicherung: Wer die gesamte DB
inklusive Trigger und Hashes rekonstruieren kann, kann die Kette fälschen.
Der Log ist kein Ersatz für sichere Betriebs-Backups.

**Keine sensiblen Inhaltsdaten im Standardlog:**
keine Prompts, Dateiinhalte, Dateinamen, Hostpfade, Zugangsdaten,
untrusted Provider-Antworten, rohe Exceptions oder Modellgedankengänge.
Nur fest zugelassene Ereignisnamen, Zustände, Zähler und Codes.
Das bisher bewusst unterdrückte HTTP-Roh-Access-Log bleibt aus.

Ein Ausfall der Journal-Transaktion führt zum Abbruch statt zu einem
falsch protokollierten Erfolg. Bei unklarem Providerstatus greift weiter
die vorhandene Quarantäne-/Restart-Logik.

## 3. Flash kann das Diagnosejournal abfragen

MCP-Tool \`minionone_job_trace\`:

\`\`\`json
{"job_id":"JOB-...","after_seq":0,"limit":100}
\`\`\`

Ergebnis: \`trace_id\`, \`events\`, \`integrity\`,
\`next_after_seq\`, \`has_more\`, \`total_events\`.
Für die nächste Seite \`after_seq=next_after_seq\` übergeben.
Maximal 100 Einträge pro Seite, 2048 Einträge pro Job.

Das frühere \`minionone_job_events\` zeigt weiterhin kanonische
Zustandsereignisse inklusive eventuell von Flash gewählter
\`operation_id\`-Werte. Für Diagnoseexporte deshalb **nur**
\`minionone_job_trace\` verwenden.

## 4. Lokaler Diagnoseexport

In einem zweiten Terminal (während der Manager läuft):

\`\`\`powershell
.\app\MinionOne.exe diagnostics --job "JOB-..." --out "C:\MinionOneSyntheticWorkspace\minionone-diagnostic.json"
\`\`\`

- Ruft authentifiziert den redigierten Jobstatus sowie **alle**
  verifizierten \`minionone_job_trace\`-Seiten ab.
- Schreibt ein JSON-Protokoll mit Version, Status, Trace-ID,
  Metadaten und Ereignisfolge, **ohne Ergebnisdateiinhalte**.
- Datei wird ausschließlich **neu** angelegt (\`O_EXCL\`, kein
  Überschreiben). Vorhandene Berichte bleiben erhalten.
- Auf einen beschädigten Hashkettenverlauf oder fehlende Berechtigung
  wird mit Fehler beendet, nicht mit unvollständigem Erfolgsbericht.

Zusätzliche gesicherte Eingaben, LM-Studio-Serverlogs oder
Unsloth-Chatverläufe werden **nicht** automatisch exportiert.

**Aktualisierung seit v0.30:** Das native Joblog wird während der tatsächlichen Worker-Laufzeit direkt und fsynced in output/job.log.jsonl ergänzt. Das vertrauliche exakte Worker-I/O (einschließlich tatsächlich gelieferter Thinking-Felder) steht sofort in output/worker-io.jsonl; beide Dateien sind unabhängig von SQLite direkt lesbar.

Die ursprüngliche v0.28-Beschreibung war: Das vorhandene native \`output/job.log.jsonl\` bleibt vorläufig eine
Projektion des ACCEPTED-Ereignisses (auf Windows nicht das komplette
Laufzeitprotokoll). Die **autoritative neue Job-/Debug-Protokollierung
liegt in \`jobs.sqlite\`** und ist über MCP/CLI zugänglich.

## 5. Systemtest und Grenzen

Der Codepfad wurde mit der echten MCP-HTTP-Implementierung,
kanonischer SQLite-Datenbank, Worker-Broker und nativer
Windows-/POSIX-Eingabe- und Ergebnisveröffentlichung sowie
deterministischen Flint-Antworten getestet.

**Nicht nachgewiesen:** echter Zielsystemlauf mit Flash/Unsloth +
Flint/LM Studio; vollständiger sicherer nativer Worker-\`work/\`-Broker;
automatische Fortsetzung eines Rückfragejobs; produktive Echtdatenfreigabe.

Dateien vor v0.28 behalten ihre alten kanonischen Ereignisse in
\`admission_events\`; das neue Telemetriejournal erfindet rückwirkend
keine Debug-Einträge.
