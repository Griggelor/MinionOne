# v0.27 — Flash → MinionOne → Flint: direkte Delegation

**Entwicklungsfunktion, ausdrücklich NO-GO für Echtdaten.**

Ab v0.27 kann Flash in Unsloth die bereits vorhandene HTTP-MCP-Anbindung
benutzen, ohne selbst `TASK.md` oder `manifest.json` auf dem Host schreiben
zu müssen. Stattdessen übergibt Flash einen fachlichen Auftrag direkt an
`minionone_delegate`. Der Manager prüft und materialisiert ausschließlich
begrenzte UTF-8-Dokumente unter einer **vorher lokal genehmigten**
Projektkennung, versiegelt diese und startet den vorhandenen
Workspace-Worker auf **derselben Job-ID**.

Die Standardfunktion bleibt **ausgeschaltet**. Dies ist kein neuer Testserver
oder separater Worker. Die vorhandene SQLite-Tool-Broker-Implementierung,
nativen Eingabeseals und Manager-Publikation werden verwendet. Flint erhält
auch mit v0.27 **keine direkten Rechte auf Host-`work/`-Dateien**.

## Konfiguration

LM Studio mit dem bereits geladenen Flint-Modell starten. Das Paket
`MinionOne.exe` aus dem aktuellen Windows-Systemtest-Build ausführen:

```powershell
mkdir C:\MinionOneSyntheticWorkspace
.\app\MinionOne.exe serve --model "EXAKTE_FLINT_MODELL_ID" --experimental-workspace-jobs --workspace-project "demo=C:\MinionOneSyntheticWorkspace"
```

Die Unsloth-MCP-Verbindung wird wie zuvor mit
`http://127.0.0.1:8765/mcp` und dem vom lokalen
`MinionOne.exe connection --show-token` erzeugten Authorization-Header
eingerichtet. Beide Backend-Dienste müssen **denselben Loopback** erreichen.

## Erste echte Interaktion mit Flash

Im Unsloth-Flash-Chat folgende Aufgabe eingeben:

> Verwende ausschließlich den angebundenen MinionOne-MCP-Server.
> Rufe zunächst `minionone_status` auf und prüfe, dass
> `workspace_execution_enabled=true` ist. Rufe danach
> `minionone_delegate` mit diesen Argumenten auf:
>
> - project_id: demo
> - goal: Lies die Eingabedatei und erstelle eine kurze Zusammenfassung.
> - context: Dies ist ein synthetischer Test ohne vertrauliche Daten.
> - acceptance_criteria: ["Erstelle summary.txt", "Beziehe dich auf die Eingabedatei"]
> - files: {"sample.txt": "Dieser Text dient nur als Test fuer die Delegation."}
> - operation_id: eine neue eindeutige alphanumerische Kennung mit mindestens 8 Zeichen
>
> Rufe anschließend wiederholt `minionone_job_status` mit der
> zurückgegebenen Job-ID auf. Bei `RESULT_READY` lies
> `minionone_workspace_result`, prüfe die gelieferten Dateien und gib mir
> das Ergebnis. Sende `minionone_accept_workspace_result` nur dann,
> wenn du es akzeptierst; verwende exakt die zurückgegebene version,
> result_digest und eine neue operation_id. Wenn der Worker
> `WAITING_FOR_PARENT`, `FAILED` oder
> `NEEDS_RECONCILIATION` meldet, zeige den Zustand, versuche
> keinen zweiten Workerstart und erfinde kein Ergebnis.

Das Unsloth-Modell muss die **tatsächlichen MCP-Tools** aufrufen; eine bloße
Textbehauptung belegt keine Integration. Es gibt weiterhin keine
automatische Wiederaufnahme eines ruhenden Unsloth-Chats.

## Worker-Auftragsvertrag

`minionone_delegate` verlangt exakt:

`project_id`, `goal`, `context`, `acceptance_criteria`,
`files`, `operation_id`.

Der Manager kontrolliert:
- Konfigurierte lokale Projekt-ID, niemals von Flash gelieferte Hostpfade.
- 1–4 zusätzliche, einfache UTF-8-Eingabedokumente, begrenzte Größe.
- Ein strukturiertes JSON-Auftragsdokument mit Ziel, Kontext und
  **1–5 Abnahmekriterien** als DATA in `TASK.md`.
- Create-only Dateischreiben, kein Überschreiben eines Teiljobs nach
  abgebrochenem Dateistaging.
- SHA-256-Manifest und die vorhandene POSIX-/Win32-Eingabeversiegelung.
- Bereits geladene, konfigurierte Flint-Modell-ID; kein dynamisches
  Modellladen oder Wechsel des Parent-Modells.
- Kanonische Job-Zustände, exklusiver Worker-Lease, Ergebnisveröffentlichung
  und expliziten Parent-ACK.

Der LM-Studio-Aufruf hat jetzt **zwei getrennte Nachrichten**:
`system`: unveränderliche Sicherheits- und Werkzeugregeln des Managers;
`user`: Auftrag und Inventar der versiegelten Eingabedokumente.
Tool-Aufrufe bleiben ausschließlich beim zustandsprüfenden Broker
autorisiert; Prompttexte selbst sind keine Sicherheitsgrenze.

## Wichtige verbleibende Grenzen

- Native schreibbare `work/`-Worker-Artefakte sind noch nicht integriert;
  `create_work` schreibt jobbezogene SQLite-Dokumente. Der Manager
  veröffentlicht die ausgewählten Ergebnisse als echte Dateien.
- `ask_question` stoppt den Worker aktuell mit
  `WAITING_FOR_PARENT`. Das sichere Fortsetzen **nach der
  Parent-Antwort** ist noch nicht implementiert. Keinen zweiten
  Modellversuch automatisch starten.
- Der vollständige Flash-/Unsloth-Zielhost-Test einschließlich
  Elternmodell-Residency, Worker-Modell-Stabilität und dokumentierter
  Rückkehr in denselben Chat steht noch aus.
- Nur synthetische Dokumente verwenden. Die Datei-Staging-Funktion und
  der native Broker sind noch **nicht** sicherheitsabgenommen.
- Ein fehlerhafter oder unvollständiger Staging-Versuch kann sichtbare
  Teildateien hinterlassen. Diese werden **nicht automatisch**
  gelöscht, überschrieben oder erneut versiegelt.
- Aktuelle Paketversion bleibt vorläufig `0.25.0.dev0`,
  Produktfreigabe weiterhin **NO-GO**.

Die neue Testsuite läuft mit
`python -m unittest discover -s tests -v`. Sie benutzt echten MCP
HTTP, echte kanonische DB, die echte Staging- und Publishing-Logik,
jedoch deterministische, nicht echte LM-Studio-Worker-Antworten.
