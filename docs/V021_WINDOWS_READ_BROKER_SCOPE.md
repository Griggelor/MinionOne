# v0.21 – G1b native Windows input broker (work in progress)

Dies ist **kein** Worker-Dateibroker und keine Freigabe für Echtdaten. Der bisherige Windows-Seal bleibt weiterhin gesperrt.

## Neu

- Rein lesender Baustein `WindowsInputBroker` für `input/` unter einem vom Manager erzeugten JOB-ID-Verzeichnis.
- Projektanker mit `CreateFileW` und FileId-Abgleich, dann `NtCreateFile` pro einzelner Pfadkomponente relativ zum gehaltenen Parent-Handle (keine Benutzerpfade).
- `NtQueryDirectoryFile` listet über das **gehaltene** Verzeichnishandle; die Eingabe wird nicht über einen anschließend erneut aufgelösten Pfad gelesen.
- `GetFileInformationByHandleEx`: Dateityp, Reparse-Merkmal, NTFS-Volume-ID, FileId, Hardlink-Anzahl und Mutationsschutz.
- `ReadFile` mit Byte-Limit und Vergleich von Dateigröße und Änderungsmetadaten vor/nach dem Lesen.
- Tests gegen Path-Traversal, NTFS-ADS, UNC, Geräte-Namen, Symlinks, Hardlinks, Directory-Renames, unzulässige Job-ID und Quotas auf Windows-CI.

## Explizite Grenzen

Der neue Baustein ist **nicht** an den produktiven MCP-Seal- oder Workerpfad angeschlossen. Es gibt keine `work/`-Schreibberechtigungen, keine `output/`-Publikation und keinen `minionone_start_workspace_job`.

Die Windows-CI ist ein **technischer Runner-Nachweis**, keine Beweisführung für die Konfiguration des Zielsystems: NTFS-Version, ACLs, Antivirus-/Filtertreiber, Race-/Kill-/Disk-full-Injection und sichere CREATE-ONLY-Veröffentlichung müssen noch auf dem echten Zielsystem geprüft werden. Insbesondere wird aus dem erfolgreichen Windows-Runner-Test **kein** G1b-PASS und **kein** Zielprodukt-PASS abgeleitet.

Offizielle API-Referenzen: Microsoft `NtCreateFile`, `NtQueryDirectoryFile`, `GetFileInformationByHandleEx`; eine Kompatibilitäts- und Concurrency-Zusage für alle Dateisystemfilter wird dadurch nicht gegeben.
