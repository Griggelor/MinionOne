> Fortsetzung: [v0.47](OWNER_REQUIREMENTS_V047.md) implementiert Rückfrage-Resume und begrenzte Cloud-Egress-/Kostengates; die unten genannten offenen Punkte beschreiben den damaligen v0.46-Stand.

# MinionOne v0.46 – realer Modell-Lebenszyklus und native Parent-Rückgabe

Stand 02.11.2026. Ergänzt die [verbindlichen v0.45-Auftragswege](OWNER_REQUIREMENTS_V045.md)
und die ursprünglichen FR-/T-Zuordnungen in [v0.44](OWNER_REQUIREMENTS_V044.md).
Ein Auftragssystem, Direktübergabe oder TASK.md; keine neue Rollen-/Consult-Route.
Kein automatischer Chatkontext für Worker.

## Implementiert

- Optionale automatische Worker-Loads/Unloads über die echten nativen
  LM-Studio-v1- und Unsloth-REST-Endpunkte. Cloud bleibt ein externer Inferenzpfad.
- Explizite kalibrierte RAM-/VRAM-Budgets, echte OS-RAM-/NVML-Messung, Frischeprüfung,
  Reserven, ein Worker und persistierte Modell-Leases. Lastspitze beim Laden und
  zusätzlicher Inferenz-/KV-Bedarf eines geladenen Modells sind ausdrücklich
  getrennt zu kalibrieren; fehlende Zusatzbudgets werden nicht als null angenommen. Keine Vermutung aus Dateigröße.
- Flash bleibt resident, wenn die Budgets passen. Sonst ist ein explizit erlaubter
  Unload möglich, ausschließlich nach verifiziertem Ende des ursprünglichen Turns.
  Keine erzwungene Abbruch- oder Eviction-Option. Bestände werden davor/danach geprüft.
- Nur die durch diesen Job geladene konkrete LM-Studio-Instanz bzw. das verifizierte
  native Worker-Modell wird freigegeben. Vorher geladene Worker bleiben erhalten.
  Flash wird vor erfolgreicher Rückgabe wiederhergestellt/aktiviert. Ursprünglicher
  Kontextumfang, 4-bit-Anforderung und GGUF-Variante werden soweit im nativen Status
  vorhanden versiegelt; widersprechende Operator-Optionen werden abgewiesen.
- Authentifizierter nativer Unsloth-Parentadapter mit festem Original-Thread,
  aktivem serververwaltetem Run und korreliertem Assistant-Anker. Er wählt einen
  eindeutigen Nachfolge-Gesprächszweig; fremde, gelöschte oder mehrdeutige Chats
  werden nicht durch eine neue Unterhaltung ersetzt.
- Fortsetzung über `/api/inference/chat-runs`, persistierte stabile Nachrichten-/
  Run-IDs und belegtes Receipt. Zustellung ist die bestätigte Annahme im Original-
  Thread, keine Behauptung über erfolgreiche Integration. Expliziter Ergebnis-ACK
  bleibt erforderlich. Auch frühe ACKs während der HTTP-Runannahme werden mit
  lesender Receipt-Prüfung korrekt behandelt.
- Verlorene native POST-Antwort: nur beobachtend mit exakt korrelierten Run- und
  Nachrichtenbelegen auflösen. Keine blinde zweite Generierung. Vorbereitete, noch nicht gesendete Nachrichten
  werden nach Neustart aus dem Originalzweig mit Digestprüfung rekonstruiert.
  Ein verifiziert gespeicherter PUT darf zur belegbar noch nicht gestarteten
  ersten Run-Annahme weitergehen; ein unklarer Run-POST wird nie wiederholt. Unklare Modell-
  Operationen bleiben gesperrt und werden nach Neustart nicht wiederholt.
- Operator-Reconciliation prüft unabhängig wiederhergestellte Bestände und,
  bei Ergebnis-Recovery, die ursprünglichen versiegelten Eingaben und verifizierte
  physische Publikationen. Vorhandenes Ergebnis statt erneuter Worker-Inferenz.
- Versionsgeprüfter optionaler Frontend-Recovery-Poll für die automatische Anzeige
  im bereits fokussierten Unsloth-Chat; Original-Frontend neu bauen erforderlich.

Der Parent bekommt für seine Fortsetzung seine eigene Historie, niemals ein Worker.
MinionOnes Rückgabejournal speichert davon nur den Nachrichten-Digest und Steuerdaten,
keine zusätzliche Kopie des rollenden Gesprächsverlaufs.
Native Text-/Tool-Historie bleibt als Daten erhalten; vergangene Toolaufrufe werden
nicht erneut ausgeführt. Medien/Attachments, Modellpaare und nicht eindeutig
gebundene Zweige sind aktuell nicht unterstützt. Die HTTP-Kontrollnachrichten
sind auf 2 MiB begrenzt; es gibt keinen stillen History-Abschneide-Fallback.

## Bedienung und Grenzen

[Deutsche Einrichtung, Ablauf und Recovery](../system_test/NATIVE_RETURN_DE.md).
Lokales automatisches Laden ist opt-in; vorhandene v0.45-Profile bleiben geladenen
Modellen vorbehalten, bis kalibrierte Budgets und auto_load ausdrücklich gesetzt
werden. Alte persistierte Profil-Snapshots aktivieren durch neue Defaults keine
Lade-/Entladerechte. Alte Jobs werden nicht automatisch neu gestartet.

Die native Parentintegration adressiert Unsloth Studio, entsprechend dem ursprünglichen
Projektziel. Andere Flash-Oberflächen benötigen einen eigenen Hostadapter; ihre
Inferenz-APIs allein bestätigen keinen Original-UI-Chat. Die Worker-Backends bleiben
unabhängig. Ressourcenmessung ist eine frische Zulässigkeitsprüfung, keine globale
OS-Speicherreservierung gegenüber fremden Anwendungen. Bei diskreten GPUs ist
zunächst NVML-Index 0 unterstützt; Backend-Platzierung und kalibrierte Budgets
müssen dazu passen. Worker-Rückfrage-Resume und vollständige Cloud-PII-/Geldbudget-
Gates bleiben eigene offene Arbeit.

## Primärquellen

Unsloth-Referenz `f9cecc99dd26de9425a0ee0cc75ee936abbecaa6`:
- [native Runs und authentifizierte Zulassung](https://github.com/unslothai/unsloth/blob/f9cecc99dd26de9425a0ee0cc75ee936abbecaa6/studio/backend/routes/chat_generation_runs.py)
- [Run-Idempotenz, Originalnachrichten und eindeutiger aktiver Run](https://github.com/unslothai/unsloth/blob/f9cecc99dd26de9425a0ee0cc75ee936abbecaa6/studio/backend/storage/chat_generation_runs_db.py)
- [native Load-/Unload-/Status-Schemata](https://github.com/unslothai/unsloth/blob/f9cecc99dd26de9425a0ee0cc75ee936abbecaa6/studio/backend/models/inference.py)
- [Chat-History-API](https://github.com/unslothai/unsloth/blob/f9cecc99dd26de9425a0ee0cc75ee936abbecaa6/studio/backend/routes/chat_history.py)
- [Frontend-Recovery-Hook](https://github.com/unslothai/unsloth/blob/f9cecc99dd26de9425a0ee0cc75ee936abbecaa6/studio/frontend/src/features/chat/utils/chat-generation-recovery.ts)

LM Studio:
- https://lmstudio.ai/docs/developer/rest/load
- https://lmstudio.ai/docs/developer/rest/unload
- https://lmstudio.ai/docs/developer/rest/list
- https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict

## Abnahme

HTTP-/MCP-Tests prüfen residenten Flash, tatsächliche API-Swaps nach Turn-Ende,
Worker-Instanzzuordnung, Restorefehler, Recovery ohne Inferenzwiederholung,
verlorene Rückgabeantworten, frühe ACKs und mehrdeutige Zweige mit Testservern.
Die Windows-Paketprüfung umfasst zusätzlich den eingefrorenen Kontroll-Client.
Das ist keine Live-Abnahme einer echten Studio-Installation oder GPU; Produkt-
Release bleibt NO_GO. Native UI-Quellintegration und echtes Zielmodell sind am
installierten Host aufzubauen/zu prüfen, nicht durch synthetische Receipts zu ersetzen.
