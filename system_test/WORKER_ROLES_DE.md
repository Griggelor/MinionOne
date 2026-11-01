# MinionOne v0.44 – gezielte Worker-Rollen und dritte Textübergabe

PRE-ALPHA / nur synthetische Tests / kein Zielhost-GO.

Vor dem Upgrade MinionOne stoppen und die private State-Datenbank sichern.
v0.44 erweitert die kanonische Job-Typ-Constraint atomar um TEXT und erhält
bestehende Job-IDs/Referenzen. Ältere Programme nicht parallel auf diese Datenbank starten.

Flash bestimmt selbst, welche Informationen ein Worker erhält. Es gibt keine
automatische Weitergabe des Chatverlaufs und keine neue Gedächtnisverwaltung.
Die bestehenden Datei-Direkt- und Ordnerwege bleiben erhalten.

## Konfiguration

`worker_profiles.local.example.json` oder `worker_profiles.cloud.example.json`
kopieren und Modell-IDs/Endpunkte passend eintragen. Beide lokalen Adapter setzen
in diesem Entwicklungsschritt ein **bereits geladenes** Modell voraus. Unsloth
muss das konfigurierte Modell als aktiven `model_identifier` melden.

Die Cloud-Vorlage benutzt absichtlich einen nicht erreichbaren Beispielhost.
Ein echtes Profil nur nach bewusster Datenfreigabe konfigurieren. Es funktioniert
mit dem dokumentierten Chat-Completions-Antwortformat, nicht automatisch mit jeder
beliebigen Cloud-API. Es gibt noch keinen fertigen PII-Filter oder Geldbudgetschutz.
Keine echten vertraulichen Daten für diese Tests verwenden.

Secrets ausschließlich als Umgebungsvariablen setzen; keine Keys in JSON, Auftrag
oder Chat schreiben. Der Name der Variablen steht unter `secret_env`.

Im Windows-Paket zusätzlich zu den bisherigen Startoptionen:

```powershell
.\1_START.cmd --worker-profiles .\worker_profiles.local.example.json
```

Alternativ ein eigenständiger Text-MCP-Server aus dem Source-Checkout:

```powershell
$env:MINIONONE_MCP_TOKEN = '<lokal sicher erzeugtes Token, mindestens 32 Zeichen>'
python -m minionone.mcp_cli --worker-profiles .\examples\worker_profiles.local.json
```

Die Datei ist eine Operator-Konfiguration. Flash kann vorhandene Profile wählen,
aber keine Provider-URLs, Keys oder zusätzlichen Toolrechte erfinden.

## Verwendung durch Flash

1. `minionone_workers` liefert die konfigurierten Rollen. `CONFIGURED_NOT_PROBED`
   bedeutet noch keinen erfolgreichen Provider-/Ladetest.
2. `minionone_consult` mit `profile_id`, `prompt` und eindeutiger `operation_id`
   aufrufen. `context` ist optional und wird nur auf ausdrückliche Übergabe gesendet.
   Es werden keine Dateien benötigt. Für ein freigegebenes Cloud-Profil muss
   zusätzlich `allow_cloud=true` gesetzt sein.
3. Der Worker hat null Datei-/Shell-/MCP-Werkzeuge und liefert mit einer Inferenz
   begrenzten Text. Der Manager führt nie Modellinhalt aus.
4. `minionone_job_status` zeigt den kanonischen Zustand. Bei `RESULT_READY`
   `minionone_consult_result` lesen, bewerten und erst danach
   `minionone_accept_consult_result` mit exakt gelieferter Version/Digest bestätigen.

Die Protokolle sind über `minionone_consult_io` autorisiert lesbar und vertraulich.
Sie gehören nicht automatisch als Kontext in eine weitere Worker-Anfrage.

## Automatische Rückgabe und verbleibende Grenze

Automatische Original-Chat-Rückgabe ist verbindliches Produktziel, sowohl bei
residentem als auch zuvor entladenem Flash. Die neue Zustellsteuerung unterstützt
beide Abläufe mit einem vertrauenswürdigen Host-Driver: Bindung vor Workerstart,
Restore bei Bedarf, Warten bei beschäftigtem Flash, korrelierte Zustellung,
kein doppelter Send nach unbekanntem Absturz und ACK erst nach Zustellung.

Der native Driver für die tatsächlich installierte Unsloth-Version fehlt noch.
Ohne ihn zeigt der Status `parent_return_backend=UNAVAILABLE` und
`auto_wakeup_enabled=false`. Die Statusabfrage oben ist ein Entwicklungstestweg
und erfüllt das automatische Produktziel noch nicht. Das Paket behauptet weder
Original-Chat-Identität noch erfolgreichen Flash-Reload aus einem MCP-Token.

Verbindliche Anforderungen, ursprüngliche FR-/Test-IDs und offener Umfang:
https://github.com/Griggelor/MinionOne/blob/main/docs/OWNER_REQUIREMENTS_V044.md
