# MinionOne – Owner-Klarstellung und Optimierung v0.44

## Superseded handoff decision

The owner correction in [OWNER_REQUIREMENTS_V045.md](OWNER_REQUIREMENTS_V045.md) replaces the third text/role path with one universal job system and exactly two handoffs. Original sources and other owner requirements below remain historical traceability.

Stand: 01.11.2026. Verbindliche neue Owner-Entscheidung; Produkt weiterhin PRE-ALPHA / NO_GO.

## Originalquellen und Rangfolge

Abgeglichen mit GitHub `main` bei `f8187d1b59ec9794b8ba6c4f1bc51b5f42d8f649`:

- [Ursprüngliche FR-/Test-Traceability](spec_baseline/07_TESTSTRATEGIE_TRACEABILITY.md)
- [Normative Architektur v0.6](spec_baseline/20_NORMATIVE_ARCHITECTURE_V06.md)
- [v0.6-Testplan](spec_baseline/23_TEST_PLAN_V06.md)
- [Security-/NFR-Traceability](spec_baseline/16_TRACEABILITY_SECURITY_NFR.md)
- [Provider-Roadmap](MODEL_PROVIDER_ROADMAP.md)

Der separate alte `docs/03`-FR-Katalog wird von den historischen Dokumenten
referenziert, liegt im aktuellen öffentlichen Repository aber nicht vor. Hier
werden die tatsächlich vorhandenen Quellen verwendet, keine fehlenden Texte erfunden.
Historische MVP-Einschränkungen auf local-only/parent_unload=forbid sind sichere
Defaults und zeitweise Implementierungsgrenzen; sie streichen nicht die ursprünglichen
Produktanforderungen an drei Backends und freigegebenen Modellwechsel.
Diese Owner-Klarstellung hat Vorrang. Die Work-CRUD-Rechte ab v0.31.1 bleiben gültig.

## Verbindliches Produktziel

| ID | Owner-Anforderung | Ursprüngliche Zuordnung |
|---|---|---|
| OW44-01 | Worker über LM Studio, direktes Unsloth oder externe Cloud-API wählen/ansprechen/laden; Fachrolle unabhängig vom Backend | FR-05, FR-24–28, FR-35–39; T-004, T-025–028, T-034–038, T-061/064 |
| OW44-02 | Kein automatischer Transfer von Flashs Gespräch, Gedächtnis oder rollendem Kontext. Flash wählt explizit übergebene Informationen selbst | FR-02/04/46/48; T-043/045/054/071/088; neu ausdrücklich klargestellt |
| OW44-03 | Automatische Rückgabe in den ursprünglichen Flash-Chat, sowohl bei residentem als auch zuvor entladenem Flash; nötige Wiederherstellung vor erfolgreichem Abschluss | FR-06–08/23/29–34/40–45; T-005/006/019/021–024/029–033/044/050/053/057 und T-139/153–158/164 |
| OW44-04 | Bestehende Direkt- und Ordnerübergabe erhalten; ergänzend einfache Textberatung/Review ohne Worker-Dateiwerkzeuge | bisher FR-05/19/22/39; ergänzender dritter Pfad |

Das Informationsangebot und damit die inhaltliche Isolation sind bewusst variabel:
Flash kann einen knappen Einzelauftrag oder gezielt mehr Material übergeben.
Das ist keine Freigabe, technische Rechte über Modelltext zu erweitern. Profile,
Datei-/Netzrechte, Secrets und Budgets bleiben Manager-/Operator-Entscheidungen.
Eine fehlende automatische Kontextübernahme ist **kein Produktfehler**.

## Drei Übergabewege

| Weg | Eingabe | Worker-Werkzeuge | Ergebnis |
|---|---|---|---|
| Direkte Übergabe | Flash benennt Aufgabe und übergebene Dokumenttexte; optionale Zusatzinfos | vorhandener kontrollierter Dateibroker | unveränderliche Artefakte |
| Ordnerstruktur | explizit vorbereitete/versiegelte input-Dateien | kontrollierte work-CRUD und Publikation | unveränderliche Artefakte |
| Textberatung (neu) | prompt plus optional expliziter context | keine Datei-/Shell-/Netz-/MCP-Werkzeuge | begrenzter, managergeprüfter Antworttext |

`context` ist bei Direkt- und Textübergabe optional. Keine Komponente sucht
selbstständig Flash-Chatverläufe, andere Jobs, Projektdateien oder Logs als Worker-Kontext.
Profilanweisungen sind ausdrücklich vom Operator konfigurierte Rollenbeschreibungen.

## Implementierung v0.44 – belegter Umfang

- Neuer tatsächlicher MCP-Pfad `minionone_workers` → `minionone_consult` →
  `minionone_consult_result` → exakter ACK. Eine Modellinferenz, null Worker-Tools;
  keine künstlichen Work-Dateien. Zustände bleiben in der kanonischen jobs-Tabelle.
- Vertrauenswürdige Rollenprofile für `lmstudio`, `unsloth` und `cloud`.
  LM Studio prüft den geladenen Zustand über `/api/v0/models`;
  Unsloth prüft `/api/inference/status`, aktiven `model_identifier`, `loaded` und
  leeres `loading`; zusätzlich muss die Antwort das gewählte Unsloth-Modell bestätigen.
  **Diese Adapter verwenden in v0.44 bereits geladene lokale Modelle.**
- Der native Unsloth-Textrequest setzt eigene Tool-/MCP-/Research-Flags explizit
  aus, Tool-Limit auf null und `tool_choice=none`. Thread-/Session-/RAG-Scope
  bleiben leer; Context-Overflow wird abgewiesen. Ein fehlendes OpenAI-`tools`
  allein wird nicht als Abschaltung des nativen Unsloth-Toolloops interpretiert.
  Quelle: der unten gepinnte `ChatCompletionRequest`-Vertrag. Unbekannte ältere
  Host-Versionen benötigen gesonderte Kompatibilitätsprüfung.
- Cloud-Textadapter für konfigurierte OpenAI-kompatible Chat-Completions-Endpunkte:
  HTTPS, kein Redirect, Secrets nur aus benannter Umgebungsvariable, Operatorfreigabe
  im Profil plus explizites `allow_cloud=true` im Auftrag, begrenzte Eingabe/Ausgabe,
  absolute Deadline. Keine automatische Provider-Umschaltung. Andere Cloud-Protokolle
  benötigen eigene Adapter. Kein Anspruch auf fertige PII-/Egress- oder Geldbudget-Prüfung.
- Vollständiges sensibles Text-Request/Response-I/O im privaten Manager-Journal;
  abrufbar nur über das job-autorisierte `minionone_consult_io`. Keine Header/Keys im I/O.
  Für den Textpfad gibt es absichtlich keinen Projekt-`output/`-Ordner.
- Persistierte Profil-/Input-Bindung, idempotente Wiederholung ohne zweite Inferenz,
  Start-Recovery, gemeinsame Single-Worker-Sperre und Quarantäne bei unklarem POST.
- Rückgabesteuerung mit vor Workerstart erfasster Parent-Bindung, permanenter
  Zustellabsicht und Original-Chat-/Digest-Receipt. Residenter Flash erhält direkt
  die Benachrichtigung; entladener Flash muss erst verifiziert wiederhergestellt
  werden. Beschäftigter Parent wartet; unklare Zustellung wird nicht blind wiederholt.
  Zustellung allein erzeugt niemals einen Integrations-ACK.

## Automatische Rückgabe – Host-Vertrag und offene Zielintegration

`ParentReturnController` ist **keine bloße Polling-Erfüllung**. Mit einem vom
vertrauenswürdigen Host implementierten Driver arbeitet er automatisch im
Hintergrund, auch ohne laufende Statusabfragen durch Flash. Der Driver muss:

1. `state(principal, thread)` → authentifizierte ursprüngliche Chat-/Modellbindung,
   Residency, Busy-Zustand und reale Restore-/Resume-Fähigkeit liefern;
2. `restore(principal, thread, model, operation_id)` → genau den gespeicherten
   Parent wiederherstellen und den tatsächlichen Zustand bestätigen;
3. `deliver(principal, thread, job, digest, operation_id)` → denselben Chat
   fortsetzen und einen korrelierten ParentReceipt liefern.

Der Driver ist ein vertrauenswürdiges, zeitlich begrenztes Manager-Dependency;
kein vom Worker wählbarer Callback oder Hostpfad. Bei Restorefehler, fremdem Chat,
fehlender Berechtigung oder unbekanntem Send-Ergebnis gibt es keinen falschen Erfolg.
Nach Restore-/Send-Absturz bleibt NEEDS_RECONCILIATION bis zu belegter Klärung.
ACK ist bei aktivierter Rückgabesteuerung erst nach verifizierter Zustellung erlaubt.

**Ein nativer Unsloth-Parent-Driver ist noch nicht ausgeliefert.** Die CLI behauptet
keinen Wakeup aus dem bloßen Bearer-Token; standardmäßig steht `auto_wakeup_enabled=false`.
Der geprüfte offizielle Unsloth-Quellstand ist
`66f1bedef0d66462765f02ac2893c2c46a2696f1`. Seine eigenen MCP-Tools bieten
`studio_status`, `plan_load`, `load_model`, `unload_model`, `chat`.
Die nativen Chat-Run-Routen brauchen jedoch echte Thread-/Message-IDs und den
Parent-Request; ein generischer Inferenzaufruf ist keine Original-Chat-Fortsetzung.
Diese Identität darf nicht aus Workertext oder dem derzeit festen Pilot-Thread geraten werden.

Primärquellen:
- https://github.com/unslothai/unsloth/blob/66f1bedef0d66462765f02ac2893c2c46a2696f1/studio/MCP.md
- https://github.com/unslothai/unsloth/blob/66f1bedef0d66462765f02ac2893c2c46a2696f1/studio/backend/routes/chat_generation_runs.py
- https://github.com/unslothai/unsloth/blob/66f1bedef0d66462765f02ac2893c2c46a2696f1/studio/backend/models/inference.py

Vor nativer Aktivierung: installierte Unsloth-Version pinnen, autorisierte
Original-Thread-Bindung integrieren, residente und entladene Rückgabe tatsächlich
prüfen. Keine neue Unterhaltung als Ersatz erzeugen, keine direkten DB-Schreibzugriffe
auf Unsloth und kein erfundener Callback. Parent-Historie darf nur der Parent-Host
zur Fortsetzung seines eigenen Chats verwenden; sie wird dadurch nicht Worker-Eingabe.

## Noch offene Produktarbeit – Anforderungen bleiben bestehen

- Verifizierter native Unsloth-Driver für Rückgabe und Wiederherstellung;
- Ressourcenplanung, zugelassener Parent-Unload nach Turn-Ende und tatsächliches
  Worker-Load/Unload über die drei Provider-Lebenszyklen;
- Rollen-/Providerwahl auch für die beiden bestehenden Dateipfade;
- Cloud-PII/Egress-Gate, SecretStore und echtes Kostenbudget nach ursprünglicher Norm;
- Fortsetzen von Worker-Rückfragen derselben Job-ID;
- Zielhost-/Modell-/Same-Chat-Abnahme. Synthetische Tests sind kein Ersatz.

Tests v0.44 belegen lokale Quell-/HTTP-Verträge und einen **synthetischen**
Host-Driver für beide Residency-Fälle, Busy, Restorefehler, falschen Chat und
Absturz-Recovery. Ursprüngliche T-ID-Produktgates werden damit nicht automatisch PASS.
