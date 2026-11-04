# AMD Ryzen AI Max+ 395 / Windows 11: shared-UMA Modell-Lifecycle (Entwicklungsstand)

**Status:** Implementierter, konservativer Ressourcenpfad. **PRE-ALPHA / PRODUCT NO-GO**. Kein realer Windows-11-/Radeon-8060S-/LM-Studio-/Unsloth-Abnahmelauf und kein nachgewiesener automatischer Modellwechsel auf dem 395+. Der bisherige NVIDIA-NVML-Pfad bleibt bestehen.

## Funktion im Code

Bisher wurde bei jedem positiven `memory_vram_mb` eine diskrete NVIDIA-NVML-Abfrage erzwungen. Der neue **explizite** vertrauenswürdig konfigurierte Wert `memory_accounting: "shared_uma"` ist bei Worker-Profilen und der originalen Flash-Parent-Konfiguration zulässig. Der Default `separate` erhält alte Profile und Snapshots.

Für `shared_uma` gilt:
- **Ein** überprüfter verfügbarer OS-Physical-RAM-Pool; kein NVML-Aufruf, keine zusätzliche virtuelle „VRAM-frei“-Kapazität, keine doppelte Budgetierung.
- `memory_ram_mb` = konservativ gemessener inkrementeller **gesamter** CPU+GPU-Speicherbedarf für Modellladen auf dem gewählten Backend. `inference_ram_mb` = zusätzlicher Gesamtbedarf eines bereits geladenen Modells (Kontext/KV/temporäre Arbeitsdaten). Beide Werte sind **nicht** nur die CPU-Anteile.
- `memory_vram_mb=0`, `inference_vram_mb=0` verpflichtend für automatisch geladene Modelle. Fehlende Inferenzbudgets werden abgelehnt; getrennte positive VRAM-Budgets sind im UMA-Modus unzulässig.
- Bei jedem Laden, Wiederverwenden und Wiederherstellen von Flash gilt die gemeinsame Prüfung; mindestens **4096 MiB OS-RAM-Reserve**, auch wenn die allgemeine Headroom-Konfiguration niedriger ist. Stale/ungültige Messungen führen zur Verweigerung.
- Kein automatischer Fallback zu NVIDIA, keine Änderung von Force-/Unload-/Fencing-/Same-Chat-/Cloud-Sicherheitsregeln. Flash wird nur unter den zuvor bereits festgelegten Bedingungen entladen.

**Wichtige Hardwaregrenze:** Windows 11 kann mit AMD Variable Graphics Memory (VGM) einen Teil des physischen UMA-Speichers für die Radeon-GPU reservieren. Dieser reservierte Anteil ist dann nicht zwingend in `GlobalMemoryStatusEx.ullAvailPhys` enthalten. Die aktuelle Prüfung kann dadurch ausreichend großen GPU-Speicher **konservativ als nicht ausreichend bewerten**. Sie addiert den VGM-Anteil absichtlich nicht ungeprüft hinzu. Dieser Code ist deshalb **kein vollständiger GPU-Speicher-/Residenzbeweis für große 395+-Modelle**. Eine getrennt attestierte Windows-DXGI-/Backend-Budgetmessung ist ein Folgepunkt und darf nur mit tatsächlicher Zielhost-Evidenz eingeführt werden.

## Beispiel-Konfiguration (Platzhalter, kein betriebsfertiges Budget)

Im vertrauenswürdigen `worker_profiles.json`-Eintrag:
```json
{
  "profile_id": "amd-395-worker",
  "provider": "lmstudio",
  "model_id": "EXAKTE_MODELL_ID",
  "base_url": "http://127.0.0.1:1234",
  "auto_load": false,
  "memory_accounting": "shared_uma",
  "memory_ram_mb": 0,
  "memory_vram_mb": 0,
  "inference_ram_mb": 0,
  "inference_vram_mb": 0
}
```
Diese bewusst konservative Vorlage startet **keinen** automatischen Modellwechsel. Erst nach unabhängiger Kalibrierung der realen Worst-Case-Budgets und Rückprüfung von RAM, VGM-Einstellung, Backend und KV-Kontext `auto_load=true` und die beobachteten MiB-Werte einsetzen. Der Flash-Parent erhält in `parent_host.unsloth.json` ebenfalls `memory_accounting: "shared_uma"` und seine **eigenen** kalibrierten `memory_ram_mb`- und `inference_ram_mb`-Werte. `allow_parent_unload=false` ist für die erste Live-Probe zu belassen. Keine Fantasiewerte oder gemeinsame Übertragung des Flash-Chatverlaufs an den Worker.

## Abnahme auf dem Zielrechner

1. Windows-Build/AMD-Treiber/Adrenalin/VGM-Aufteilung, Gesamt-RAM, genaue LM-Studio-/Unsloth-/Modell-/Quantisierungs-/Kontextversionen dokumentieren. Keine lokalen Pfade/Tokens oder echten Chatdaten veröffentlichen.
2. Read-only `MinionOne.exe resource-probe --mode os-shared` prüfen. Die angezeigte `ram_available_mb` ist nur die OS-Sicht, keine zugesicherte GPU-Kapazität.
3. Beide universellen Übergaben via `workspace-self-test --handoff both` mit synthetischen Daten und manuell bereits geladenem Worker durchführen.
4. Modell-Lifecycle nur nach kalibrierten Profilen aktivieren. Positive und negative Belege: Elternmodell bleibt resident bei genügend RAM; bei Speichermangel kein ungewolltes Entladen; wenn ausdrücklich erlaubt, erst nach Flash-Turn-Ende entladen; Worker/Flash-Restore mit denselben Backends; dedizierter VGM-Carve-out-Fall; fehlende Kapazität; Verweigerung bei fehlender/staler Telemetrie; Prozessabbruch und Neustart-Reconciliation.
5. Erst nach echter Unsloth-original-chat- und Zielhost-Abnahme G1c/G3 bewerten. Vorher **BLOCKED / NO-GO**.

## Nachweise im Entwicklungs-PR

`tests/test_amd_uma_lifecycle_v050.py` prüft Profilzulassung, Legacy-Default, Einzelpool-Headroom, fehlendes NVML, separates NVIDIA-Verhalten, Parent-Budget, konservative Negativfälle und einen synthetischen MCP-Native-Swap mit Ergebnis-/Rückgabekette. Diese Tests simulieren den Modellprovider: **kein** Windows-/AMD-395+-Hardware-Gate.

Quellen zum Windows-/UMA-Verhalten:
- AMD: https://developer.amd.com/playbooks/user-guide/
- AMD: https://developer.amd.com/playbooks/lmstudio-rocm-llms/
- Microsoft DXGI QueryVideoMemoryInfo: https://learn.microsoft.com/en-us/windows/desktop/api/dxgi1_4/nf-dxgi1_4-idxgiadapter3-queryvideomemoryinfo

Verfolgt in [G1c #4](https://github.com/Griggelor/MinionOne/issues/4) und [Review #72](https://github.com/Griggelor/MinionOne/issues/72).
