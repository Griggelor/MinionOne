# MinionOne – historischer Zielablauf und Testgrenzen (v0.19 WIP)

> **Historische WIP-IST-Aufnahme, nicht aktuelle Produktfreigabe.** Der ursprüngliche Dateivertrag aus ADR-020 v0.6 verlangt tatsächlich existierende input/work/output-Dateien; v0.31 konkretisiert diesen Vertrag mit einem test-first-Gate und bewertet SQLite-Dokumenttexte ausdrücklich als PILOT-Zwischenlösung. Aktuelle Sollbasis: [SPEC_V031_FILESYSTEM_FIRST_TESTFIRST.md](SPEC_V031_FILESYSTEM_FIRST_TESTFIRST.md). Abschnitt „Laufender Implementierungsstand“ unten beschreibt nur den damals dokumentierten v0.19-Stand; spätere v0.20–v0.30-Entwicklungen sind separat nachgewiesen.

Stand 30.10.2026. Dieses Dokument konkretisiert die gewünschte **zweiphasige Job-Bedienlogik**. Gegenüber der normativen v0.6-Architektur ist `PREPARING` eine **neue, vorgeschaltete Reservierung**; keine bereits zugelassene oder versiegelte Jobinstanz. Die bisherige Unveränderlichkeits- und Trust-Grenze **ab Admission** bleibt bestehen. Bis zur Implementierung und Windows-Prüfung ist der Produkt-Release-Gate **NO-GO**.

## Bedienung aus Flash-Next / Unsloth

1. Flash-Next legt einen Auftrag fest und nennt eine **vertrauenswürdig konfigurierte Projektkennung** wie `demo` (der absolute Projektpfad wird vorab durch den Operator in MinionOne hinterlegt; kein frei vom LLM bestimmbarer Hostpfad). Optional fragt Flash zuerst den Modellkatalog ab.
2. `minionone_prepare_workspace(project_id, operation_id)` reserviert dauerhaft eine Job-ID `JOB-<32 hex>`, legt unter dem zugehörigen Projekt `<Projekt>/.minionone/jobs/<Job-ID>/` genau `input/`, `work/`, `output/` an und meldet `PREPARING`. Verfügbare LM-Studio-IDs werden best effort ermittelt. Nicht verifizierte Beschreibungen sind entsprechend markiert. Keine Modellladeaktion.
3. Flash-Next schreibt `input/TASK.md`, Eingabedateien und das Eingabemanifest. Bis zur Versiegelung ist **nur der autorisierte Parent** zum Befüllen berechtigt; MinionOne stellt derzeit **keinen** sicheren Datei-Upload/Broker dafür bereit. Wenn Flash selbst keinen legitimen projektbezogenen Dateizugriff besitzt, muss später ein entsprechender Manager-Import implementiert werden.
4. **POSIX-lokal implementiert; unter Windows weiterhin gesperrt:** `minionone_seal_workspace(job_id, expected_version, operation_id)` verifiziert die tatsächlichen Input-Bytes, `TASK.md`, Manifest, Dateitypen und Quotas, hält einen managervertrauenswürdigen Snapshot fest und überführt dieselbe Job-ID in den kanonischen `ACCEPTED`-Zustand. Modifizierter Input wird beim Start abgewiesen. Keine neue Job-ID.
5. **Weiterhin geplant und rot:** `minionone_start_workspace_job(job_id, expected_version, operation_id, model_id, profile)` bindet ein geladenes, freigegebenes Modell und startet nach Ressourcenprüfung. `model_id` kommt aus dem überprüften verfügbaren Katalog, Fähigkeiten/Budgets aus einer separaten vertrauenswürdigen Profilkonfiguration. Der Worker bekommt nur Job-/Attempt-Capabilities; keine Shell, kein direkter NTFS-Zugriff.
6. Worker führt bounded `list_files`, `read_file`, `create_work`, `publish_artifact`, `report_progress`, `ask_question` aus. Der zukünftige sichere Windows-Dateibroker gewährt nur `input/` READ und `work/` READ + CREATE-ONLY; `output/` bleibt für Worker komplett gesperrt. Manager veröffentlicht zukünftig unveränderliche Worker-Outputs; die Aufnahme-Events werden POSIX-lokal bereits nach `output/job.log.jsonl` projiziert.
7. Flash-Next fragt über MCP den Status/das Log ab; nur verifizierte Artefakte in `RESULT_READY` dürfen dem Benutzer zur Prüfung angeboten werden. `COMPLETED` erfordert einen autorisierten ACK **nach** bestätigter Integration; niemals allein auf Basis eines Workertextes. Same-Chat-Wakeup bleibt bis zu echten Unsloth-Tests unbewiesen, Polling kann ein verifizierter Fallback sein.

## Laufender Implementierungsstand

**Jetzt vorhanden (lokal getestet):** HTTP-MCP-Workspace-Reservierung, `PREPARING`, POSIX Input-Seal mit unveränderlichem SQLite-Byte-Snapshot und Übernahme derselben Job-ID in das kanonische `jobs`-Register als `ACCEPTED`, read-only Input-Integritätsprüfung und redigiertes Manager-Admission-Log. Windows-Seal, Windows-Dateibroker, Workerstart, Worker-Artefaktpublikation und Parent-Rückgabe existieren **noch nicht**.

Opt-in für das reine PREPARING-Staging aus Quellcode:

```powershell
$env:MINIONONE_MCP_TOKEN = '<lokal sicher generiertes Token mit mindestens 32 Zeichen>'
python -m minionone.mcp_cli --port 8765 --project 'demo=D:\\Projekte\\MeinProjekt'
```

In Flash-Next mittels authentifizierter MCP-Verbindung `minionone_prepare_workspace` aufrufen. **Das ist kein Windows-Produktrelease und kein Worker-Test.** Nicht mit sensiblen echten Dateien verwenden, bis NTFS-Sicherheitsgrenze verifiziert ist. Der bereits ausgelieferte native v0.18-Systemtest enthält diese WIP-Änderungen nicht.

## Produkt-Abnahme: lokale Tests sind nicht automatisch Produkt-PASS

- `product_acceptance/TARGET_CASES.json`: exakt 169 historisch spezifizierte IDs aus `docs/spec_baseline/07_...`, `16_...` und `23_...`, ergänzt um neue T-170 bis T-182.
- `tests/test_workspace_preparation.py`: 13 neue **lokale** Unit-/MCP-Socket-Tests. Status ist `PASS_LOCAL_ONLY`; sie prüfen insbesondere *keine* Windows-Handle-Confinement-Garantie.
- `product_acceptance/test_target_product_e2e.py`: drei reale lokale MCP-Zielvertragsproben: T-082 (POSIX-Seal) und T-102 (Manager-Admission-Log) nun **lokal grün**, T-163 (echter Workerstart mit Mutationsabwehr) weiterhin **rot**. Grün bedeutet nicht Windows-E2E.
- `scripts/run_product_status.py`: generiert `evidence/product-status/PRODUCT_STATUS.json` und `.md`, inklusive Fehler-/Skips-/Evidenzklassen. `--strict` liefert Exitcode 2, solange das Produktziel nicht belegt ist.
- `.github/workflows/product-acceptance.yml`: soll nach Commit auf Windows und Linux Statusartefakte erstellen; ein grüner **Collector** bedeutet nur, dass Teststatus erhoben wurde, nicht dass das Produkt fertig ist. Es existiert für diesen lokalen Quellstand **noch kein GitHub-CI-Nachweis**.

**Release-Gates:** Kein `PASS_TARGET_E2E`, solange tatsächlicher Windows/Unsloth/Flint-Prozess sowie sicherer Datei-Broker nicht geprüft sind. Kein reales Input-Schreiben durch Worker über NTFS ohne nachweisbaren Win32-handlebasierten Schutz. `PREPARING`-Reservierungen können POSIX-lokal versiegelt werden, sind unter Windows aber weiterhin nicht versiegelbar; auf keinem System sind Ordnerjobs aktuell als Worker ausführbar.

## Weiterentwicklung v0.20 (lokal, kein Windows-Release)

Die Zielschritte 4 (Seal + gleiche kanonische Job-ID) und teilweise 6 (redigiertes Manager-Admission-Log) sind auf POSIX tatsächlich implementiert, per MCP-HTTP getestet und als `PASS_LOCAL_CONTRACT_NOT_TARGET_E2E` eingestuft. Windows-Sealing und alle Worker-Broker-Aktionen bleiben gesperrt. Der ausführbare Start ist weiterhin rot; `minionone_verify_workspace_input` prüft nur unveränderte Bytes und gibt **keine** Ausführungsfreigabe. Siehe `docs/V020_POSIX_SEAL_SCOPE.md`.
