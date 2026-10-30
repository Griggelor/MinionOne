# ADR-029 — Vollständiges Worker-I/O-Protokoll im Job-output

**Status: implementiert als NO-GO-Entwicklungsfunktion; Zielhostprüfung offen.**

## Ursprung und ausdrücklich geklärte Anforderung

Die ursprüngliche Projektarchitektur verlangt ein lesbares
\`output/job.log.jsonl\` und einen von Flash abfragbaren Jobverlauf.
Die normative v0.6-Spezifikation unter
\`docs/spec_baseline/20_NORMATIVE_ARCHITECTURE_V06.md\` §5 hatte als
sicherheitsorientierten Default festgelegt, **keine** rohen Prompts,
Modelltexte oder Tool-Inhalte im **redigierten** Auditlog zu speichern.
Der Nutzer verlangt jetzt ausdrücklich **zusätzlich** das exakte
Modell-I/O und die Werkzeug-Ein-/Ausgaben in \`output/\`, lesbar durch
Flash, um auch **inhaltlich** abweichendes oder auffälliges
Worker-Verhalten prüfen zu können.

Daher ersetzen wir den bisherigen Auditvertrag nicht. Wir ergänzen
einen **separaten vertraulichen Worker-Transcript-Kanal**. Er wird
ausschließlich beim vom lokalen Operator explizit freigegebenen
Workspace-Ausführungsmodus aufgenommen; ohne Workspace-Ausführung
wird kein inhaltlicher Log freigegeben. Das ist eine bewusst
weitergehende lokale Datenspeicherung als §5 der v0.6-Baseline.

## Physische Speicherung

Unter dem bereits genehmigten Projektordner und derselben Job-ID:

\`\`\`text
<Projekt>/.minionone/jobs/JOB-.../
    input/
    work/
    output/
        job.log.jsonl     # redigierte Aufnahme-/Managerprojektion
        worker-io.jsonl   # SENSIBLES vollständiges Worker-I/O (neu)
        result.txt        # Beispiel einer geprüften Worker-Veröffentlichung
\`\`\`

\`worker-io.jsonl\` wird **ausschließlich vom Manager** über die
bestehende Windows-NTFS-/POSIX-Publikationsschnittstelle erzeugt,
no-replace und durch SQLite-Receipts abgesichert. Der Worker kann
den Namen weder als Work-Dokument erzeugen noch als Artefakt fälschen.

Das neue, append-only SQLite-Journal \`workspace_worker_io\` führt
zuvor alle einzelnen Ereignisse mit laufender Sequenz und SHA-256-
Hashverkettung. Die Datei in \`output/\` ist die verifizierte
Projektion nach dem letzten Worker-Aufruf. Sie wird **vor** der
\`RESULT_READY\`-Freigabe veröffentlicht. Sie bleibt auch nach
dem Eltern-ACK und nach einem Neustart auf dem Dateisystem stehen.

## Exakt aufgezeichnete Nutzdaten

\`MODEL_REQUEST\` enthält den **tatsächlich versendeten UTF-8-JSON-Body**
an \`/v1/chat/completions\` einschließlich Systemanweisung,
User-Auftrag, Gesprächshistorie, Werkzeugdefinitionen und Parametern.

\`MODEL_RESPONSE\` enthält den **tatsächlich empfangenen UTF-8-JSON-Body**
vom LM-Studio-Provider. Also Inhalt, zurückgegebene Tool-Calls und
deren Argumente, Fehlerhafte Antwortdaten vor der Parserprüfung und
providerseitig zurückgegebene Zusatzfelder, sofern vorhanden.

\`TOOL_RESPONSE\` enthält die **wirklich an das Modell übergebene**
Werkzeugantwort, einschließlich Dokumentinhalten eines erlaubten
\`read_file\`, Prüfsumme, Fehlercode und Status.

Jeder JSONL-Eintrag hat \`seq\`, \`utc\`, \`kind\`, \`body_utf8\`,
\`previous_hash\`, \`entry_hash\`. Die Aufzeichnung ist nicht die vom
Modell erzählte Zusammenfassung, sondern erfolgt im realen
LM-Studio-Adapter und Manager. HTTP-Authorization-Header werden
**nicht** mitgeschrieben.

**Achtung:** Nutzeraufträge können selbst Credentials enthalten,
und manche Provider geben \`reasoning_content\` im Antwortbody
zurück. Der JSON-Body wird im ausdrücklich genehmigten lokalen
Workspace-Ausführungsmodus bewusst nicht redigiert. Darum ist dieses
Log vertraulich. Es darf nicht automatisch nach Cloud/GitHub oder
in fremde Chats hochgeladen werden. Nur lokal berechtigte Operatoren
und der authentifizierte Parent dürfen darauf zugreifen.

## Während der Worker arbeitet: Flash MCP

Flash kann das neue MCP-Tool \`minionone_worker_io\` mit
\`job_id\`, optional \`after_seq\` (Standard 0) und \`limit\` (1–10)
aufrufen. Die Abfrage verifiziert zuerst die vollständige Hashkette
und liefert dann inhaltliche Ein-/Ausgaben sowie \`next_after_seq\`
und \`has_more\`. Damit kann Flash den Dialog schon **während**
der Ausführung prüfen; es braucht nicht erst das fertige Dateiartefakt.
Das Tool erfordert dieselbe authentifizierte Job-/Thread-Berechtigung.

\`minionone_job_trace\` bleibt der **redigierte** Metadatendiagnosepfad
für technische Fehler. Der bestehende lokale Befehl \`diagnostics\`
exportiert nur diesen redigierten Bericht; kein sensibler Transcript
wird ohne eine zusätzliche bewusste Handlung exportiert.

## Grenzen und Fehlerfälle

- Die unveränderten Security-Regeln gelten: Worker kein \`output/\`,
  kein Shell/Netz, keine Hostpfade, keine Manager-Credentials.
- Kein stilles Abschneiden eines Transcripts. Die Entwicklungsversion
  begrenzt JSONL auf 960 KiB, Einzeleinträge auf 256 KiB und auf
  256 Einträge. Bei überschrittenem Budget darf der Auftrag nicht
  \`RESULT_READY\` melden; der Fehler wird im redigierten Status
  und soweit möglich als \`CAPTURE_ERROR\` aufgezeichnet.
- SQLite und NTFS haben keinen gemeinsamen atomaren Commit.
  Die vorhandene managerverwaltete Pending-/No-Replace-Publikation
  erlaubt einen sicheren Replay; die Ausführung kann bei unsicherem
  Zustand in \`NEEDS_RECONCILIATION\` bleiben.
- Bei frühzeitigem Ausfall vor dem ersten Modellaufruf kann die
  Ausgabe nur den Fehler bzw. das Fehlen von externem Modell-I/O
  belegen. Ein fehlender/abgebrochener Providerresponse ist niemals
  als vollständige Antwort darzustellen.
- Keine automatische Inhaltsklassifizierung durch MinionOne:
  **Flash** liest die unverfälschten Daten, erkennt Auffälligkeiten
  und trifft selbst eine Qualitätsentscheidung. Der Manager kann
  sicherheitskritische unzulässige Werkzeuge unabhängig ablehnen.
- Native schreibbare Worker-\`work/\`-Dateien, echte Unsloth-
  Chat-Anbindung und sichere Rückfragefortsetzung bleiben offen.

## Zielsystemprüfung

Ein synthetischer Flash-Auftrag soll den Worker beauftragen, dann
\`minionone_worker_io\` abfragen und nach Laufabschluss
\`output/worker-io.jsonl\` mit dem tatsächlichen LM-Studio-
Kommunikationsverlauf vergleichen. Dabei prüfen:
Originalsystemprompt, Originalauftrag, alle Tool-Calls und
vollständige Werkzeugergebnisse, Modellabschluss, Hashketten,
Dateiänderungsschutz und persistentes Vorhandensein nach ACK.

**Produktfreigabe bleibt NO-GO.**
