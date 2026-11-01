# MinionOne v0.33 – Windows-Workspace-Systemtest mit echten Work-Dateien

**Entwicklungsstand: PRE-ALPHA / ausschließlich synthetische Daten / Produktfreigabe NO-GO.**

Dieses Testverfahren ist der **aktuelle** Testpfad für `input/` (read-only),
`work/` (echte physische Dateien, kontrollierte CRUD-Rechte) und `output/`
(unveränderliche, nur einmalig veröffentlichte Artefakte). Ab Paket `0.43.0.dev0` startet `3_SYSTEMTEST.cmd` diesen aktuellen Test direkt;
siehe [Paket-Anleitung](../system_test/README_DE.md). In älteren Paketen
prüfte diese Startdatei ausschließlich den SQLite-Dokument-Pilot. Dessen
Starter heißen jetzt `LEGACY_*` und gelten nicht als Win32-Work-Nachweis.

## 1. Paket und Umgebung

- Windows 11, frisch angelegter **leerer** lokaler Testordner,
  beispielsweise `C:\MinionOneSyntheticWorkspace`. Keine Echtakten,
  Zugangsdaten, Programme oder fremde Projektdateien einbringen.
- Das **zum gewünschten Commit passende** Windows-Systemtest-Artefakt aus
  [Windows System Pilot CI](../.github/workflows/windows-system-pilot.yml)
  verwenden; Artefakt-Commit und zugehörige CI-Abläufe dokumentieren.
- Das aktuelle installierbare Paket trägt `0.43.0.dev0`; `v0.33`
  bezeichnet den ursprünglichen Implementierungsmeilenstein dieser Testanleitung.
  Alte Pakete mit `0.25.0.dev0` nicht mit dem aktuellen Paket verwechseln.
- LM Studio auf demselben Windows-Host, API üblicherweise
  `http://127.0.0.1:1234`, gewünschtes Flint-/Worker-Modell geladen
  (Modell-ID exakt aus LM Studio übernehmen). MinionOne lädt keine Modelle
  selbst. Unsloth-/Flash-Parent später nur optional im zweiten Schritt.

## 2. MinionOne mit echtem Workspace-Backend starten

Aus dem vollständig entpackten Paket im Terminal:

```powershell
.\app\MinionOne.exe serve --model "EXAKTE_GELADENE_FLINT_MODELL_ID" --experimental-workspace-jobs --workspace-project "demo=C:\MinionOneSyntheticWorkspace"
```

Alternativ aus dem Source-Checkout mit installierter Paketabhängigkeit:

```powershell
python -m minionone.pilot_cli serve --model "EXAKTE_GELADENE_FLINT_MODELL_ID" --experimental-workspace-jobs --workspace-project "demo=C:\MinionOneSyntheticWorkspace"
```

Im Status muss `workspace_execution_enabled=true` und
`worker_file_backend=native_win32_work_sqlite_output` stehen.
`release_gate=NO_GO` ist **korrekt und erforderlich**. Die Legacy-
Meldung `sqlite_documents` weist auf den **falschen Testmodus** hin.
Nicht mehrere Pilot-Instanzen mit derselben State-Datenbank starten.

## 3. Den echten Flint-Workspace-Durchlauf ausführen

In einem zweiten Terminal, bei weiterhin laufendem Server:

```powershell
.\app\MinionOne.exe workspace-self-test --project-id demo --project-root "C:\MinionOneSyntheticWorkspace"
```

Dieser Befehl legt eine neue Job-ID an, erzeugt ausschließlich synthetische
`TASK.md`, `sample.txt` und `manifest.json`, versiegelt `input/`,
startet das **wirkliche** konfigurierte Flint-Modell per LM-Studio-HTTP,
verwendet den nativen NTFS-Work-Broker, veröffentlicht ein Artefakt und
sendet den authentifizierten ACK **erst nach den folgenden Prüfungen**:

1. `RESULT_READY` aus dem kanonischen Managerstatus.
2. **Tatsächliche** `work/result.txt` mit Inhalt
   `MINIONONE WORKSPACE OK`, regulärer Dateiidentität, ohne Reparse/Link
   und SHA-256-Übereinstimmung.
3. `output/result.txt` enthält dieselben Bytes und denselben erwarteten
   Hash; keine Überschreibung des bereits veröffentlichten Artefakts.
4. `minionone_workspace_result` enthält dieselbe Datei samt Digest.
5. Authentifizierte `minionone_accept_workspace_result` ergibt
   `COMPLETED`.

Im JSON-Bericht müssen insbesondere die Schritte
`real_native_work_file_and_digest_verified`,
`published_file_and_digest_verified` und `exact_parent_ack` als
erfolgreich erscheinen. `release_gate` bleibt `NO_GO`.
Bei einem beschädigten oder fehlenden Work-Ergebnis **kein ACK** senden.

## 4. Zusätzlicher Flash-/Unsloth-Test (separate Beweisklasse)

Wie in [v0.27 Direkte Delegation](V027_FLASH_DIRECT_DELEGATION.md) das
aktuelle lokale HTTP-MCP mit Bearer-Token aus dem **lokalen**
`connection --show-token`-Befehl in Unsloth konfigurieren.
Das Token niemals in Dokumente oder Chat-Logs kopieren.

In demselben Unsloth-Chat einen begrenzten synthetischen Auftrag an
`minionone_delegate` senden, dabei
`project_id=demo`, einen eindeutigen `operation_id` und
`files={"sample.txt": "synthetic minionone test"}` verwenden.
Die Auftragskriterien fordern die Erstellung von `work/result.txt`
und ihre Veröffentlichung über `publish_artifact`.
Mit `minionone_job_status` die zurückgegebene Job-ID verfolgen,
`minionone_workspace_result` abholen, Text und Hash prüfen und
nur bei `RESULT_READY` mit exakt dem zurückgegebenen
`result_digest` und `version` bestätigen.

Erst wenn der **echte Unsloth-Parent selbst** im selben Chat den Inhalt
prüft, übergibt und bestätigt, ist die Integrationsprobe positiv.
Auch das ist noch keine Freigabe für vertrauliche Daten oder allgemeine
autonome Codeausführung. Automatischer Parent-Wakeup und sicherer
mehrstufiger Resume sind noch nicht implementiert.

## 5. Abbruch und Diagnose

`FAILED` / `NEEDS_RECONCILIATION` / `WAITING_FOR_PARENT`
niemals als Erfolg interpretieren oder blind erneut ausführen.
Abgebrochene interne `pending-*`-Work-Dateien blockieren jetzt
Änderungen und Abschluss; sie bleiben **absichtlich erhalten**, bis der
Operator den unklaren Zustand untersucht hat. Sie nicht automatisch löschen
oder als normales Arbeitsdokument veröffentlichen.

Zur Rückmeldung geeignet: anonymisierter JSON-Bericht, genaue Paket-
Commit-SHA, Windows-Build, LM-Studio-/Unsloth-/Flint-Versionen,
Jobstatus und fest definierter Fehlercode. **Nicht versenden:** Bearer-Token,
API-Key, private Datenbank, unbearbeitete `worker-io.jsonl`-Daten,
Modell-Prompts, vertrauliche Projekte oder echte Dateien.

## 6. Was die GitHub-CI bereits nachweist und was nicht

- v0.32: neun echte MCP-/HTTP-Work-Contract-Cases mit **simuliertem**
  LM-Studio-Modell unter Windows **9/9** und Ubuntu **9/9**.
- v0.33: zusätzliche Worker-Transaktions-Quarantäne,
  OS-Verzeichnisbudget, Output-Namensschutz und ein HTTP-E2E-Test,
  der ein abgestürztes Work-Staging direkt vor Abschluss simuliert.
  Die CI des PR #51 hat auf Windows/Ubuntu **23/23 Checks**
  erfolgreich abgeschlossen; das ist keine Zertifizierung einer
  OS-Berechtigungsgrenze auf dem Zielrechner.
- Weiter offen: native Windows-Sicherheitsfreigabe unter adversarialen ACL-,
  Reparse-/Race- und Cross-Process-Bedingungen, echte Unsloth-/Flint-
  Zielhost-Prüfung, automatischer Parent-Wakeup, Work-Operationsjournal
  mit genau-einmaliger Crash-Replay-Verarbeitung und Produkt-GO.

**Allein ein synthetischer CI-PASS erlaubt keine Echtdatenverarbeitung.**
