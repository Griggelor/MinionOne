# MinionOne v0.46 – automatischer Modellwechsel und Rückkehr zu Flash

Flash liefert weiterhin nur den Auftrag: direkt oder per TASK.md. Beide Wege
verwenden denselben Job. Der neue Modell-Lebenszyklus und die Rückgabe sind
optional konfigurierbar. Ohne diese Konfiguration bleibt der bisherige Testbetrieb.

## Unterstützte Schnittstellen

Worker: LM Studio ab nativer REST-v1-Modellverwaltung (0.4.x), natives Unsloth
Studio oder die bestehenden kompatiblen Cloud-APIs. Cloudworker benötigen keinen
lokalen Modellwechsel. Native Parent-Rückgabe: **Unsloth Studio mit serverseitigen
dauerhaften Chat-Runs**, API-Vertrag `unsloth-studio-f9cecc99`, Referenz-Commit
`f9cecc99dd26de9425a0ee0cc75ee936abbecaa6`. Andere Flash-Oberflächen benötigen einen
passenden Hostadapter; eine Chat-Completions-API allein ist keine UI-Chatfortsetzung.

Der zu verbindende Flash-Chat muss ein einzelner lokaler Text-/Tool-Chat sein,
kein Modellpaar oder Medienchat. Beim Delegieren muss Flash einen echten aktiven,
serververwalteten Chat-Run in diesem Chat haben. Das stellt die ursprüngliche
Thread-/Nachrichtbindung sicher. Direkte fremde MCP-Aufrufe bei ruhendem Flash
werden bei eingeschalteter nativer Bindung abgewiesen.

## Einmal einrichten

1. `parent_host.unsloth.example.json` und `worker_profiles.autoload.example.json`
   kopieren. Originale Thread-ID, aktiven Flash-Modellbezeichner, Worker-Modellkey,
   API-Adresse und Namen der Secret-Umgebungsvariablen eintragen.
2. Die Beispiele enthalten **absichtlich ungültige Null-Speicherwerte**. Für jedes
   automatisch geladene Modell `memory_ram_mb` auf eine am Zielhost gemessene
   positive Obergrenze setzen, einschließlich Kontext/KV-Cache und Ladeoptionen.
   Bei diskretem NVIDIA-GPU-Betrieb zusätzlich `memory_vram_mb` passend setzen.
   Null VRAM ist für CPU/UMA-Konfigurationen mit entsprechendem RAM-Budget gedacht.
   Zusätzlich müssen `inference_ram_mb` und `inference_vram_mb` ausdrücklich
   die noch zusätzlich benötigte Inferenz-/KV-Spitze eines bereits geladenen
   Modells angeben. `null` wird abgewiesen; `0` ist nur bei gemessen vollständig
   vorallokiertem Speicher korrekt. Auch residente Modelle reservieren diese
   zusätzliche Last plus Headroom. Keine Freigabe aus der Dateigröße allein ableiten. NVML-GPU-Index ist zunächst 0;
   Backend-Platzierung und Messung müssen dazu passen. Mehrgeräte-Verteilung ist
   nicht implementiert. Fehlende GPU-Telemetrie wird abgewiesen.
3. `allow_parent_unload` bewusst konfigurieren: `false` lässt Flash im Speicher;
   `true` erlaubt Entladen ausschließlich nach Ende seines aktiven Turns. Wenn
   Worker plus Reserven bereits passen, bleibt Flash auch bei `true` geladen.
4. Unsloth-Key nur in die benannte Umgebungsvariable setzen. Weder Key noch
   Chatverlauf gehören in einen Workerauftrag. Eine Kopie der privaten State-DB
   ist vor dem Upgrade bei gestopptem Manager zu sichern.
5. Im Windows-Paket starten:

```powershell
.\1_START.cmd --worker-profiles .\worker_profiles.autoload.example.json --parent-host .\parent_host.unsloth.example.json
```

Die Nummern-Starter bleiben ein synthetischer Testbereich. Für den gewählten
Projektordner aus einem Source-Checkout beispielsweise:

```powershell
$env:MINIONONE_MCP_TOKEN = '<eigenes starkes Token>'
python -m minionone.mcp_cli --project demo=C:\MinionOne-Testprojekt --experimental-workspace-jobs --worker-profiles .\examples\worker_profiles.autoload.json --parent-host .\examples\parent_host.unsloth.json
```

## Automatische Anzeige in der offenen Unsloth-Oberfläche

Die native Run-API erzeugt und persistiert die Fortsetzung im Original-Chat. Die
referenzierte Unsloth-Oberfläche synchronisiert fremde Runs standardmäßig bei
Fokus-/Sichtbarkeitswechseln. Für die automatische Anzeige auch im bereits offenen,
fokussierten Chat installiert MinionOne einen kleinen versionsgeprüften
Recovery-Poll in Unsloths bestehendem Frontend-Hook:

```powershell
python -m minionone.unsloth_ui_setup --studio-root C:\Quellcode\unsloth
```

Anschließend das Unsloth-Frontend mit dessen dokumentiertem Buildverfahren neu
bauen und Studio neu starten. Der Installer legt ein `.minionone-v046.bak` an,
ändert nur den exakt unterstützten Quellstand und ist idempotent. Bei einem
anderen Quellstand verweigert er die Änderung. Die AGPL-Lizenz des Hostfiles
bleibt erhalten. Das ist eine Hostintegration, kein Workerwerkzeug und kein
allgemeiner Browser-/GUI-Automationsprozess.

## Rückfragen ab v0.47

Ein explizites `minionone_answer_question` setzt denselben geprüften Job fort.
[Anleitung und Cloud-Gates](JOB_CONTINUATION_CLOUD_DE.md). Danach wieder den Flash-Turn beenden.

## Ablauf durch Flash

Flash wählt `profile_id` und delegiert wie zuvor. `minionone_job_status` enthält
zusätzlich `model_lifecycle` und `parent_return`.

Bei aktivierter nativer Rückgabe schließt Flash nach dem Start seinen aktuellen
Turn ab und wartet auf die automatische Original-Chat-Benachrichtigung. Nicht
innerhalb desselben Turns bis zum Ergebnis pollen oder vorzeitig bestätigen.
Insbesondere bei `phase=WAITING_PARENT_TURN_END` ist das erforderlich: Bei freigegebenem Swap
wartet der Manager genau auf dessen Ende. Danach läuft der Worker selbstständig.
Ein belastbarer Original-Thread-Run startet nach Ressourcenfreigabe und nötigem
Flash-Restore automatisch. Flash liest das Ergebnis über die bestehenden Tools,
integriert es und bestätigt den exakten Digest. Zustellung allein erzeugt keinen
Integrations-ACK. Auch bekannte Fehler/Abbrüche/Rückfragen werden zurückgemeldet.

Der Parent verwendet für seine Fortsetzung nur seinen eigenen gespeicherten
Gesprächszweig. Worker erhalten weiterhin ausschließlich ihre explizite Aufgabe
und autorisierte Dateien. Unsloths native Worker-Tools/MCP bleiben deaktiviert;
der Parent erhält seine ursprünglichen Tool-/MCP-Einstellungen zurück.

## Fehler und Wiederherstellung

Unklare Lade-/Entlade-/Restore-Ausgänge sperren weitere Worker. Der Manager
wiederholt keine Inferenz oder unsichere Modelloperation. Einen angenommenen
nativen Chat-Run kann er anhand stabiler IDs und verifizierter Nachrichten
lesend bestätigen, auch wenn die POST-Antwort verloren ging.

Nach einem Modellfehler: Generierung am Backend unabhängig stoppen, ursprüngliche
Modellbestände und Flash wiederherstellen, Workspace prüfen, Manager stoppen.
Dann im gleichen Operator-State:

```powershell
.\app\MinionOne.exe --state C:\MinionOneState reconcile-models --job JOB-... --parent-host .\parent_host.unsloth.example.json --confirm-provider-stopped --confirm-work-inspected
```

Der Befehl prüft die Bestände und veröffentlichtes Ergebnis erneut; er lädt kein
Modell und wiederholt keinen Worker. Ein verifiziertes, bereits publiziertes
Ergebnis wird wieder RESULT_READY. Nach dem Neustart des Managers wird dessen
Rückgabe fortgesetzt. Bei fehlenden Belegen bleibt der Job gesperrt. Eine bloß
unbekannte Chat-Zustellung ist kein Grund, dieselbe Inferenz erneut auszuführen.

## Nachweisgrenze

Die automatisierten Tests verwenden echte HTTP/MCP-Transporte, private Journale,
Dateien und die gebaute Windows-EXE gegen kontrollierte API-Testserver. Ein echter
Unsloth-Host, dessen gebautes Frontend, echte Modelle und GPU-Speicherfreigabe sind
hier nicht als live abgenommen ausgewiesen. Produkt bleibt PRE-ALPHA / NO-GO bis
zur Zielhost-Abnahme; die Implementierung der beiden Abläufe ist jetzt vorhanden.
