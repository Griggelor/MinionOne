# F02 – Testplan für reale Zielhost-Abnahme

Stand: 2026-11-03, ab MinionOne v0.49. **PRE-ALPHA / NO-GO**, bis auf dem tatsächlichen Zielrechner belegt. Diese Anleitung ist eine Abnahmematrix, **kein** behauptetes Prüfergebnis.

## Voraussetzung

1. Nur künstliche Testtexte verwenden; aktuelle Windows-EXE aus dem überprüften GitHub-Build für den getesteten Commit installieren. Exakten Commit/SHA-256, Windows-Build, Dateisystem, Unsloth-Commit, LM-Studio-Version, Workerprofil/Modell-ID/Quantisierung/Kontextgröße erfassen – ohne Benutzerverzeichnisse, Tokens oder Schlüssel.
2. LM Studio und ausgewähltes Modell laden, MinionOne im synthetischen Workspace-Modus starten (`1_START.cmd`). Bei Ressourcen-/Modellwechsel-Tests zuvor die **echten** Speicherbudgets ermitteln. Auf AMD/ROCm/UMA kann der NVML-basierte VRAM-Checker nicht automatisch als geprüft gelten.
3. `app/MinionOne.exe --state "$env:LOCALAPPDATA\MinionOne\WorkspaceSystemTest" workspace-self-test --handoff both` ausführen. Berichte unter `reports/` prüfen: `passed=true`, zwei separate Jobs, geprüfte reale Work-/Output-Bytes, Digest und ACK, `release_gate=NO_GO`. Bei unklarem Providerzustand keine weitere Generierung starten.
4. Nur bei bestandenem Schritt 3 Unsloth-Hostanbindung und native Original-Chat-Rückgabe gemäß `system_test/NATIVE_RETURN_DE.md` prüfen. Dies ist ein **separates** G1a-Gate und wird durch CLI-Erfolg nicht abgedeckt.

## Nachweise je offenem Gate

| Gate | Reale positive Probe | Reale Negativ-/Recovery-Probe | Beweisklasse |
|---|---|---|---|
| G1a / #2 | authentifizierter Flash-Aufruf, derselbe Chat/Thread erhält automatische Rückgabe nach abgeschlossenen Turn | Fremd-Thread, Replay, verlorene Antwort, Doppelzustellung, busy Chat, Neustart | tatsächliche Unsloth-Chat-Run-IDs und redigierte Zeit-/Statusereignisse, keine Chattexte |
| G1b / #3 | echte Win32-handlegebundene sichere Worker-Dateibearbeitung, managerprüfbare Artefakte | Junction/Symlink/ADS/Hardlink, Rename-Race, ACL, Disk-full, Prozesskill, Cross-Job | Win32-/NTFS-Identitäten, Event-/Dateihashes und unabhängige Prüferfreigabe |
| G1c / #4 | echtes Modell läuft unter reserviertem Flash mit korrektem RAM/VRAM; kontrolliertes Unload/Restore | fehlende Ressourcen, GPU-Budgetbruch, Eviction, Timeout, Neustart; nicht automatisch wiederholen | Modell-/Treiber-/Speicherprofil, Zeit-/Zustandsereignisse |
| G3 / #7 | beide Übergaben mit echten Modellen, tatsächlich publizierte Datei und ACK im Original-Chat | abgebrochener, wiederholter, ungültiger Job und verweigerter ACK | Job-IDs, Input-/Result-Digests, native Statussequenzen, versionsgebundene Testmatrix |

## Abnahmezustände

- `NOT_RUN`: nicht auf dem Zielhost durchgeführt.
- `BLOCKED`: technisch unmöglich oder bekannte Voraussetzung fehlt.
- `FAILED`: tatsächlich gelaufen, Abnahmekriterium nicht erfüllt.
- `PASS_SYNTHETIC_PROVIDER`: zwei Auftragswege auf echtem Worker-Provider, **kein** Chat-/NTFS-/GPU-Gate.
- `PASS_TARGET_E2E`: ausschließlich nach echter Zielhost- und unabhängiger Gate-Evidenz; niemals aus kontrollierten CI-Servern oder einem einzelnen JSON-`passed`.

Die vorhandenen 182 Produktakzeptanzfälle werden nicht automatisch in `PASS_TARGET_E2E` umgeschrieben. Keine Eingabe mit vertraulichen Echtdaten, kein Cloud-Echtdatentest. Ergebnisbelege sicher redigieren; private Datenbank, `worker-io.jsonl`, Modellantworten, Pfade und Schlüssel nicht ins öffentliche Repository hochladen.
