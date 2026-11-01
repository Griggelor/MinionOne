# MinionOne – universelle Aufträge von Flash, v0.45

Owner-Korrektur vom 01.11.2026. Diese Festlegung ersetzt die dritte Text-/Rollenroute
in v0.44. Die ursprünglichen FR-/Testzuordnungen und Quellen stehen in
[OWNER_REQUIREMENTS_V044.md](OWNER_REQUIREMENTS_V044.md); die Anforderungen an
Backends, bewusste Informationsübergabe und automatische Rückgabe bleiben bestehen.

## Ein Auftragssystem, zwei Übergabewege

Flash formuliert den Arbeitsauftrag. MinionOne verwaltet und führt ihn universell
aus. Reviewer, Recherche, Analyse oder Fachthemen sind mögliche Auftragsinhalte,
keine eigenen MinionOne-Produkte, Tools oder vorgegebenen Rollen.

| Übergabe | Flash/Operator liefert | Gemeinsame Ausführung |
|---|---|---|
| Schnellauftrag | `minionone_delegate`: `task`, optionale `files`, `project_id`, `operation_id` | Manager erzeugt TASK.md und Manifest, versiegelt und startet dieselbe Job-ID |
| Vorbereitete Dateien | `prepare_workspace`, TASK.md und optionale weitere Dateien plus Manifest, `seal_workspace`, `start_workspace_job` | Derselbe versiegelte Workspace, Worker-Broker und Ergebnis-/ACK-Ablauf |

Beide Wege erlauben `profile_id` aus der vertrauenswürdigen Operator-Konfiguration.
Sie wählen Backend/Modell, nicht den Inhalt des Auftrags. Ohne Profil bleibt der
explizit konfigurierte bisherige LM-Studio-Worker erhalten. Profile müssen keine
Rollenbeschreibung enthalten. Das optionale v0.44-Legacyfeld `instructions` wird
weiter geparst, aber nicht als Arbeitsauftrag oder zusätzliche Systemanweisung
verwendet; der Auftrag kommt ausschließlich von Flash bzw. aus TASK.md.

- Kein separates `minionone_consult` und keine Consult-Ergebnis-/ACK-/Cancel-Tools.
- Direkt mitgegebene Aufgaben benötigen weder Dummy-Dateien noch zusätzliche
  Akzeptanzkriterien. Die bisherige strukturierte goal/context/acceptance_criteria-
  Form bleibt kompatibel; beide Formen gleichzeitig werden abgewiesen.
- **Kein automatischer Transfer von Chatverlauf oder rollendem Kontext.** Nur die
  explizite Aufgabe und deren autorisierte Dateien sind Worker-Eingabe. Die
  Geschichte des laufenden Workers enthält nur seinen eigenen Auftrag/Tooldialog.
- LM Studio, natives Unsloth und konfigurierte kompatible Cloud-Chat-Completions
  verwenden denselben begrenzten AgentLoop. Nur der Manager führt die erlaubten
  jobbezogenen Dateioperationen aus. Native Unsloth-Werkzeuge, MCP, Research,
  Thread-/Session-/RAG-Vererbung bleiben ausdrücklich deaktiviert.
- Modell-/Providerwahl, Snapshot und Cloudfreigabe werden vor Start dauerhaft an
  den versiegelten Input gebunden. Replay, Backendwechsel bei gleichem Vorgang,
  unbekannter POST-Ausgang, Abbruch und Rückgabe bleiben gemeinsam kontrolliert.
- Cloudfreigabe gilt für den ganzen expliziten Auftrag einschließlich seiner
  Dateien und des jobeigenen Tooldialogs. Voraussetzung sind Operatorfreigabe im
  Profil, benannte Secret-Umgebungsvariable, HTTPS und `allow_cloud=true` am Start.
  Eingabebegrenzung wird vor dem Start geprüft. Noch kein PII-/Geldbudget-Gate.
- Lokale Modelle müssen weiterhin geladen sein. Ein Profil ist keine bestätigte
  Bereitschaft und löst keine heimlichen Modellwechsel oder Ausweichprovider aus.

## Rückgabe und verbleibende Arbeit

Der bestehende ParentReturnController ist an dieselben Workspace-Jobs angebunden.
Residente/entladene Rückgabe, Busy, Restorefehler und unbekannte Send-Ergebnisse
werden als synthetischer Hostvertrag getestet. Ein nativer Unsloth-Parent-Driver,
Original-Chat-Bindung und verifizierte Zielhost-Rückgabe fehlen weiterhin.
Ebenso offen: Ressourcenplanung und tatsächlicher Modell-Load/Unload einschließlich
zulässigem Flash-Unload nach Turn-Ende, Worker-Rückfrage-Resume, Cloud-Egress-/PII-
und Geldbudget-Gates und Abnahme mit echten Zielmodellen.

## Upgrade

Vor Upgrade Manager stoppen und private State-Datenbank sichern. Neue unveränderliche
Worker-Snapshots und eine nullable Bindungsspalte ergänzen die vorhandenen Tabellen;
Job-IDs, Artefakte und historische v0.44-TEXT-Datensätze werden nicht gelöscht.
TEXT bleibt ausschließlich als historischer Schematyp erhalten, wird aber nicht
mehr gestartet oder als dritter MCP-Weg angeboten. Neue Jobs sind WORKSPACE-Jobs.
Alte Consult-Clients müssen auf delegate bzw. prepare/seal/start und die gemeinsamen
workspace_result/ACK/Cancel-Werkzeuge umgestellt werden. Ältere Manager-Versionen
nicht parallel gegen den aktualisierten State betreiben.

## Nachweisgrenze

Synthetische HTTP/MCP-Tests prüfen beide Übergaben mit beiden lokalen Backends,
Cloud mit einem kontrollierten HTTPS-Transportersatz, echte native Workspace-Dateien,
Replay, Auswahlbindung und Rückgabevertrag. Windows-CI prüft zusätzlich die gebaute
EXE samt Netzwerk-Kindprozess. Das belegt keinen echten Unsloth-Zielhost und keine
realen Cloudmodelle. Produkt bleibt PRE-ALPHA / NO-GO.

Unsloth-Protokollquelle: [_takes_tool_passthrough und _explicit_studio_tool_loop_requested](https://github.com/unslothai/unsloth/blob/66f1bedef0d66462765f02ac2893c2c46a2696f1/studio/backend/routes/inference.py#L4647).
Der native Request hält Client-Werkzeugvorschläge für MinionOnes Broker offen
(`tool_choice=auto`, Katalog, maximal ein Vorschlag), während Unsloths eigener
Werkzeugloop deaktiviert bleibt. Das Zielmodell/Backend muss diesen Passthrough
unterstützen; die konkrete Installation bleibt zu prüfen.
