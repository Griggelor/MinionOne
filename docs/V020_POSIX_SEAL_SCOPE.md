# v0.20 – POSIX Manager-Seal und weiterhin gesperrter Windows-Worker (WIP)

## Konkrete neue Funktionen

`minionone_seal_workspace(job_id, expected_version=0, operation_id)` liest `input/` mit `openat`/`O_NOFOLLOW`-ähnlichen descriptor-relativen POSIX-Operationen, prüft ein flaches `manifest.json` mit SHA-256 für **alle** Inputs einschließlich `TASK.md`, Maximalgrößen und Dateinamen. Nach erfolgreicher Prüfung sichert es die tatsächlichen Bytes in der Manager-SQLite, erstellt **unter derselben reservierten Job-ID** den kanonischen `jobs`-Datensatz im Status `ACCEPTED` mit Version 1 und erzeugt ein redigiertes `output/job.log.jsonl` aus dem Managerjournal. Die drei sichtbaren Verzeichnisse bleiben unverändert. Wiederholungen derselben Operation liefern denselben Beleg, auch nach Neustart. Eine migrierte v0.19-Staging-Datenbank wird unterstützt.

`minionone_verify_workspace_input(job_id)` vergleicht für einen versiegelten Job die aktuellen Input-Bytes mit dem gespeicherten Snapshot und meldet Abweichungen. Dieses Werkzeug **startet keinen Worker**.

### Eingabeformat (eng begrenzter WIP-Vertrag)

```text
<approved-project>/.minionone/jobs/JOB-<32 lowercase hex>/
  input/
    TASK.md              # UTF-8, nicht leer, max. 64 KiB
    sample.txt           # Beispiel: beliebige Bytes <= 1 MiB
    manifest.json        # max. 32 KiB, flaches JSON
  work/                  # Workerzugriff weiterhin GESPERRT
  output/
    job.log.jsonl        # Nur Manager; redigierte kanonische Events
```

`manifest.json`:

```json
{"files":{"TASK.md":"<sha256 of actual TASK.md bytes>","sample.txt":"<sha256 of actual sample.txt bytes>"}}
```

Mindestens `TASK.md`, maximal 16 deklarierte Inputdateien, maximal 4 MiB insgesamt inklusive Manifest, keine Unterordner, Symlinks oder mehrfach verlinkte Dateien. Der Manager pinnt Bytes, nicht nur Dateinamen/Hashes. Dieses enge Entwicklungsformat ist **nicht** die endgültige Dateitypen- oder Größenpolicy.

## Explizite Limitierungen

1. **Windows verweigert die Versiegelung ausdrücklich**, da native NTFS-Handle-Confinement-, Reparse-Point-, Hardlink-, ADS- und ACL-Prüfungen noch nicht nachgewiesen sind. POSIX-Tests sind keine Windows-Sicherheitsnachweise.
2. `minionone_start_workspace_job` existiert nicht. Worker lesen weder `input/` noch `work/`; kein Echtdatenlauf.
3. Der Journalprojektor schreibt nur vorhandene kanonische Admission-Events; Crash-Recovery bei DB-Commit + Log-I/O-Fehler erfolgt durch idempotenten Seal-Retry. Kein vollständig getesteter journalischer Cursor/Replay für laufende Worker.
4. Parent-Thread-Bindung kommt derzeit aus konfigurierten MCP-Credentials, **nicht** aus echter Unsloth-Thread-Attestation.
5. Output-Projektion und Dateien sind weder vertraulichkeitsgeprüft gegen andere Host-Benutzer noch auf realem Windows getestet.
6. Modellprofile, Unsloth-nativer Provider, OpenAI-Provider und automatische Same-Chat-Zustellung fehlen.

## Beweisgrenzen

Echte MCP-HTTP-Contract-Tests T-082 und T-102 sind **lokal** grün. T-163 bleibt **rot** (sicherer Start nicht vorhanden). Sicherheitstests prüfen Posix-Symlinks/Hardlinks, ungültige Manifestdaten, Quota, Mutation, Parallelität, Neustart und Migration. Das Zielprodukt bleibt `NO-GO`, unabhängig davon, wie viele lokale Tests grün sind.
