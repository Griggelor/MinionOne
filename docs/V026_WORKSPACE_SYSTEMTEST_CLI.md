# v0.26 — synthetischer Workspace-Echtsystemtest (Entwicklungsfunktion)

**Freigabestatus: NO-GO.** Teste ausschließlich mit synthetischem Text in
einem neu angelegten, leeren, eigens freigegebenen Projektordner.
Insbesondere KEINE vertraulichen Projektdateien oder Zugangsdaten einlesen.

## 1. Modell und Projekt vorbereiten

1. LM Studio auf dem **gleichen Windows-Host** starten, lokalen API-Server
   an `127.0.0.1:1234` aktivieren, das exakt angegebene tool-fähige Flint-Modell
   vollständig laden.
2. Einen **leeren Projektordner** anlegen, zum Beispiel
   `C:\\MinionOneSyntheticWorkspace`.
3. Nur für diesen Versuch die explizite Entwicklungsfunktion aktivieren.
   Eine reale Unsloth-Parent-Identität oder GPU-/UMA-Residency ist damit
   weiterhin **nicht nachgewiesen**.

## 2. Server über das ausgelieferte Programm starten

Aus dem ausgepackten GitHub-Systemtest-Artefakt:

```powershell
.\app\MinionOne.exe serve --model "EXAKTE_GELADENE_MODELL_ID" --experimental-workspace-jobs --workspace-project "demo=C:\MinionOneSyntheticWorkspace"
```

Alternativ aus dem Quellcode:

```powershell
python -m minionone.pilot_cli serve --model "EXAKTE_GELADENE_MODELL_ID" --experimental-workspace-jobs --workspace-project "demo=C:\MinionOneSyntheticWorkspace"
```

**Beide Schalter sind zwingend gemeinsam erforderlich.** Ohne
`--experimental-workspace-jobs` werden die Workspace-Ausführungswerkzeuge
nicht angeboten. Ohne `--workspace-project` kann kein Projekt ausgewählt
werden. Der Modellname muss exakt der bereits geladenen LM-Studio-ID
entsprechen. MinionOne startet/lädt keine Modelle automatisch.

Die bestehenden Systemtest- und MCP-Zugangsdaten verwenden weiterhin den
lokalen Benutzerzustand und Windows DPAPI. Das Verzeichnis bleibt
operator-gesteuert; die Worker-Werkzeuge erhalten **keinen** freien
Projektdateisystemzugriff. Der Server bindet nur an 127.0.0.1.

## 3. Synthetischen Workspace-End-to-End-Test ausführen

In einem **zweiten** Terminal:

```powershell
.\app\MinionOne.exe workspace-self-test --project-id demo --project-root "C:\MinionOneSyntheticWorkspace"
```

Aus Python:

```powershell
python -m minionone.pilot_cli workspace-self-test --project-id demo --project-root "C:\MinionOneSyntheticWorkspace"
```

Der Runner führt folgende reale Manager-Schritte aus:

1. Authentifiziert den lokalen MCP-Pilot und bestätigt ausdrücklich den
   opt-in Entwicklungsmodus sowie weiterhin `release_gate=NO_GO`.
2. Reserviert eine echte Job-ID in dem genehmigten Projekt.
3. Legt **ausschließlich feste synthetische Dateien** per Create-Only in
   `input/` an: TASK.md, sample.txt, manifest.json.
4. Versiegelt die Eingaben mit dem kanonischen Manager und ruft
   `minionone_start_workspace_job` mit der ursprünglichen Job-ID auf.
5. Prüft bis maximal 900 Sekunden den echten Worker-Status; der Flint-Worker
   verwendet die bekannten sechs **SQLite-Dokumentwerkzeuge**.
6. Liest die vom Manager veröffentlichte Datei `output/result.txt`, prüft
   dessen exakt erwarteten Text `MINIONONE WORKSPACE OK` samt SHA-256 und
   verifiziert die MCP-Ergebnisliste.
7. Sendet erst nach erfolgreicher Inhalts- und Digest-Prüfung einen
   authentifizierten ACK. Erwartet danach `COMPLETED`.

Der Bericht `workspace-test-*.json` liegt unter dem jeweiligen
Systemtest-Statusordner (`%LOCALAPPDATA%\MinionOne\SystemTest\reports`).
Er enthält nur Status, Testschritte und Prüfsummen, **keine** Token,
API-Anfragen, Modell-Gedankengänge oder privaten Quelldateien.

## Abbruch und Grenzen

Bei Fehlschlag wird **kein ACK** gesendet. Laufzustände, die auf eine
möglicherweise noch laufende Provider-Anfrage hindeuten, werden nicht
automatisch wiederholt. Nicht mit einem anderen Job oder anderer
`operation_id` denselben Auftrag fortsetzen. Zuerst Jobstatus und
redaktionell bereinigte Logs untersuchen.

Das ist ein **vorbereitender realer LM-Studio-Workspace-Test**, noch keine
produktive Flint-/Unsloth-Freigabe. Die Projekt-`work/`-Dateien sind
weiterhin nicht vom Worker direkt beschreibbar, und die Input-Textgrößen sind
streng begrenzt. G1a-Unsloth-Chatidentität, G1b-Worker-Sandbox, echte
Parent-Modell-Residency, automatische Parent-Wiederaufnahme und der
komplette Echtdatenbetrieb bleiben NO-GO.

Die deterministischen CI-Tests ersetzen die reale Probe auf dem
Zielrechner **nicht**. Erst bei einem erfolgreichen Zielhost-Durchlauf die
bereinigte JSON-Evidenz und Versionsangaben für LM Studio, Flint, Unsloth
und Windows zurückmelden.
