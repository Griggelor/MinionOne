# MinionOne v0.45 – derselbe Auftrag, zwei Übergabewege

Flash liefert den Arbeitsauftrag. MinionOne verwaltet ihn unabhängig davon, ob
Flash ihn direkt übergibt oder als TASK.md vorbereitet. Es gibt keinen gesonderten
Review-/Rollen-/Consult-Pfad und keinen automatischen Transfer von Flashs Chat.

## Backend konfigurieren

Die lokale oder Cloud-Beispielkonfiguration kopieren und Modell-ID/Endpunkt
anpassen. `profile_id` benennt ein konfiguriertes Backend/Modell. Rollenbeschreibungen
sind nicht erforderlich. Lokale Modelle müssen bereits geladen sein.

Im Windows-Paket:

```powershell
.\1_START.cmd --worker-profiles .\worker_profiles.local.example.json
```

Im Source-Checkout kann die gemeinsame Ausführung explizit aktiviert werden:

```powershell
$env:MINIONONE_MCP_TOKEN = '<lokal sicher erzeugtes Token, mindestens 32 Zeichen>'
python -m minionone.mcp_cli --project demo=C:\MinionOne-Testprojekt --experimental-workspace-jobs --worker-profiles .\examples\worker_profiles.local.json
```

Das Projektverzeichnis muss bereits existieren und vom Operator freigegeben sein.
Die Datenbank ist vor dem Upgrade bei gestopptem Manager zu sichern.

## Auftrag durch Flash

`minionone_workers` liefert konfigurierte Backends, keine garantierte Bereitschaft.
Ein Schnellauftrag braucht nur beispielsweise:

```json
{
  "project_id": "demo",
  "profile_id": "local-lmstudio",
  "task": "Prüfe die folgende Überlegung: ... Publiziere dein Ergebnis als review.txt.",
  "operation_id": "flash-pruefung-20261101-001"
}
```

Aufruf über `minionone_delegate`. `files` ist optional. Der Manager erzeugt
TASK.md, versiegelt und startet denselben Workspace-Job.

Alternativ: `minionone_prepare_workspace`, TASK.md und optionale Eingabedateien
mit Manifest durch einen autorisierten Dateischreiber vorbereiten,
`minionone_seal_workspace`, danach `minionone_start_workspace_job` mit `profile_id`.
MCP allein stellt Flash weiterhin keinen allgemeinen Host-Dateischreiber bereit.

Für beide Wege: `minionone_job_status`, `minionone_workspace_result`, nach Prüfung
`minionone_accept_workspace_result` mit exakter Version und Digest. Sensible echte
Worker-I/O steht unter `minionone_worker_io` und im jobeigenen output/worker-io.jsonl.
Nur jobbezogene Manager-Werkzeuge sind verfügbar; Modelltext wird nie ausgeführt.

Cloud benötigt zusätzlich eine bewusst konfigurierte Operatorfreigabe,
`allow_cloud=true` beim Start und einen Key aus der benannten Umgebungsvariable.
Die Freigabe umfasst den expliziten Auftrag samt Dateien und eigenem Worker-Dialog.
Es gibt weiterhin kein fertiges PII-/Geldbudget-Gate und keinen Test mit echten
Cloudmodellen. Die Beispiel-Cloudadresse ist absichtlich nicht erreichbar.

Automatische Rückgabe in den ursprünglichen Flash-Chat bleibt Pflicht. Der
Rückgabecontroller ist vorhanden und synthetisch geprüft; der native Host-Driver
fehlt. `parent_return_backend=UNAVAILABLE` bedeutet weiterhin keinen automatischen
Wakeup. Tatsächliches Laden/Entladen von Modellen ist ebenfalls noch offen.

Verbindlicher Stand:
https://github.com/Griggelor/MinionOne/blob/main/docs/OWNER_REQUIREMENTS_V045.md
