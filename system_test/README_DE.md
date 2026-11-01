# MinionOne 0.44 – Windows-Test mit echten Arbeitsdateien

**PRE-ALPHA · nur synthetische Testdaten · Produktfreigabe NO-GO.**

Das Windows-Paket benötigt kein installiertes Python. Die drei nummerierten
Starter verwenden jetzt den aktuellen Workspace-Test: echte Dateien in
`input/`, `work/` und `output/`, einen geladenen lokalen LM-Studio-Worker und
eine geprüfte Ergebnisbestätigung. Shell und Codeausführung bleiben gesperrt.

Zusätzliche Rollen für LM Studio, direktes Unsloth und freigegebene Cloud-APIs
sowie der neue Textpfad: [WORKER_ROLES_DE.md](WORKER_ROLES_DE.md).
Flash bestimmt sämtliche übergebenen Informationen; kein impliziter Kontexttransfer.

## 1. Vorbereiten

1. ZIP vollständig in einen lokalen Ordner entpacken.
2. LM Studio starten und den lokalen API-Server auf `127.0.0.1:1234` aktivieren.
3. Ein toolfähiges Flint-/Worker-Modell vollständig laden und seine genaue
   Modell-ID notieren. MinionOne lädt oder entlädt keine Modelle.
4. Für den ersten Test keine echten Dokumente hinzufügen.

Die Paketversion lautet `0.44.0.dev0`. `bundle.json` nennt die Version, den
Quell-Commit und den Standardtest. `SHA256SUMS.txt` enthält die Datei-Hashes.

## 2. Starten und testen

**`1_START.cmd` öffnen.** Beim ersten Start die genaue geladene Modell-ID
eingeben. Das Fenster offen lassen. Die Ausgabe muss `physical input/work/output`
und `EXPERIMENTAL WORKSPACE` nennen.

Der Starter legt einen eigenen Testbereich unter
`%LOCALAPPDATA%\MinionOne\WorkspaceSystemTest` an. Darin liegt das Projekt
`synthetic-project`; seine Jobs erscheinen unter `.minionone\jobs\JOB-...`.
Die frühere SQLite-Testdatenbank wird hierfür nicht übernommen.

**`3_SYSTEMTEST.cmd` öffnen.** Der Test verwendet die gespeicherte
Projektkonfiguration. Es müssen keine Pfade manuell eingetragen werden.
Er erzeugt einen neuen Job mit synthetischen Eingaben und prüft:

- versiegelten Input und tatsächliche Worker-Werkzeugaufrufe;
- `work/result.txt` mit exakt `MINIONONE WORKSPACE OK`;
- dieselben geprüften Bytes und SHA-256 in `output/result.txt`;
- den vom Manager gelieferten Ergebnis-Digest;
- `COMPLETED` erst nach passender authentifizierter Bestätigung.

Der lokale JSON-Bericht wird angezeigt und unter `reports/` gespeichert.
Erfolg bedeutet `passed: true`; `release_gate: NO_GO` bleibt dabei korrekt.
Ein bestandener Modelltest bestätigt noch keine Unsloth-Chatintegration.

Optional aus einem Terminal:

```powershell
.\1_START.cmd --model "EXAKTE_MODELL_ID" --model-call-seconds 180
```

Bei anderem lokalen API-Port `--provider-url http://127.0.0.1:PORT` ergänzen.
Bei API-Key-Schutz `--ask-api-key` verwenden; der Key wird nicht gespeichert.
Pro Modellaufruf gelten standardmäßig 120 Sekunden, höchstens 300 Sekunden.

## 3. Aus Unsloth delegieren

Nach einem erfolgreichen Workspace-Test `2_UNSLOTH.cmd` öffnen. URL und
Authorization-Header ausschließlich lokal in Unsloths tatsächlich vorhandene
HTTP-MCP-Konfiguration übernehmen. Das Fenster zeigt ein geheimes Token.
MinionOne und Unsloth müssen denselben Windows-Loopback erreichen; WSL/VM
teilen `127.0.0.1` nicht automatisch mit Windows.

In einem Unsloth-Chat diesen synthetischen Auftrag verwenden:

> Delegiere über minionone_delegate an project_id demo. Verwende einen neuen
> operation_id, goal "Read input/sample.txt, create work/result.txt with exactly
> its uppercase text, publish result.txt and finish", context "Synthetic
> integration test", acceptance_criteria ["Exact uppercase result.txt"] und
> files {"sample.txt":"minionone workspace ok"}. Frage mit minionone_job_status
> den Status der zurückgegebenen Job-ID ab. Bei RESULT_READY hole
> minionone_workspace_result. Prüfe result.txt auf exakt MINIONONE WORKSPACE OK.
> Bestätige nur dieses Ergebnis mit minionone_accept_workspace_result, seiner
> version und seinem result_digest sowie einem neuen operation_id. Zeige mir
> Job-ID, Dateiinhalt und Abschlussstatus. Führe keinen Dateiinhalt aus.

Statusabfrage erfolgt über Polling. Ein automatisches Aufwecken eines ruhenden
Chats ist nicht implementiert; die echte ursprüngliche Chatidentität ist noch
nicht unabhängig attestiert.

## 4. Fehler und Rückmeldung

`FAILED`, `WAITING_FOR_PARENT` und `NEEDS_RECONCILIATION` sind kein Erfolg.
Bei unklarem Providerzustand zuerst die Generierung in LM Studio beenden;
nicht blind wiederholen oder Dateien bzw. `pending-*`-Zwischenstände löschen.

Für eine Rückmeldung eignen sich JSON-Testbericht, `bundle.json`, Windows-,
LM-Studio-, Modell- und Unsloth-Version sowie der sichere Fehlercode.
Keine Tokens, API-Keys, private Datenbank oder `worker-io.jsonl` weitergeben.
Der Worker-I/O-Log enthält genaue Modell-Ein- und Ausgaben.

## Direkter CLI-Aufruf und ältere Tests

Die Starter rufen dieselbe ausführbare Anwendung auf:

```powershell
.\app\MinionOne.exe --state "$env:LOCALAPPDATA\MinionOne\WorkspaceSystemTest" serve --synthetic-workspace --model "EXAKTE_MODELL_ID"
.\app\MinionOne.exe --state "$env:LOCALAPPDATA\MinionOne\WorkspaceSystemTest" workspace-self-test
```

Bei der optionalen Python-Ausgabe `python bootstrap.py` anstelle von
`app/MinionOne.exe` verwenden. Sie benötigt Python 3.11+, CI verwendet 3.13.

Die früheren SQLite-Dokumenttests bleiben über `LEGACY_1_START.cmd`,
`LEGACY_2_UNSLOTH.cmd` und `LEGACY_3_SYSTEMTEST.cmd` verfügbar. Ihre Anleitung
steht in [README_LEGACY_DE.md](README_LEGACY_DE.md). Beide Server verwenden
standardmäßig Port 8765: jeweils nur einen Modus gleichzeitig starten.
