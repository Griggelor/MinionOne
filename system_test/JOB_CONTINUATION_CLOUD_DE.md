# MinionOne v0.48 – Rückfragen, Cloud-Freigaben und Recovery

Aufträge kommen weiterhin von Flash: direkt oder als TASK.md im vorbereiteten
Ordner. Einrichtung von Modellwechsel und Original-Chat-Rückgabe steht in
[NATIVE_RETURN_DE.md](NATIVE_RETURN_DE.md). Der dort versionsgeprüfte Frontend-Patch
bleibt derselbe. Python-Hilfsprogramme setzen eine Installation des mitgelieferten
Wheels im verwendeten Python-/Unsloth-Environment voraus.

## Worker-Rückfragen

Flash beendet nach dem Delegieren seinen Turn. Ein Worker kann `ask_question`
verwenden; der Manager pausiert ihn, erhält Work-Dateien und Worker-Historie und
holt Flash wie bisher im Original-Chat zurück. `minionone_job_status` enthält:

```json
{
  "state": "WAITING_FOR_PARENT",
  "workspace_job": {
    "question": "Welche Fassung soll veröffentlicht werden?",
    "question_digest": "…64 Hexzeichen…",
    "answer_available": true,
    "attempt_epoch": 1
  }
}
```

Flash entscheidet anhand seines eigenen Kontexts oder fragt den Anwender. Nur die
gewählte Antwort wird übergeben:

```json
{
  "job_id": "JOB-…",
  "expected_version": 5,
  "question_digest": "…exakt aus dem aktuellen Status…",
  "answer": "Veröffentliche die überarbeitete Fassung.",
  "operation_id": "antwort-001"
}
```

Tool: `minionone_answer_question`. Version und Digest sind keine Beispielwerte,
sondern müssen aus dem aktuellen Status übernommen werden. Bei identischer
Wiederholung dieselbe operation_id verwenden. Nach Annahme wieder den Flash-Turn
beenden und auf die nächste native Rückgabe warten. Keinen neuen Job anlegen.

Die Antwort erweitert weder Datei-/Netzrechte noch Budgets. Checkpoint, Eingaben,
Work-Dateien einschließlich Verzeichnissen und bisherige Logs müssen unverändert
sein. Bei erschöpftem Budget ist `answer_available=false`. Ein alter Job ohne
gültigen Checkpoint oder ein unklarer Inferenzabbruch wird nicht neu gestartet.

## Cloud-Profil einrichten

`worker_profiles.cloud.example.json` enthält weiterhin absichtlich eine ungültige
Provideradresse/Modellkennung und jetzt eine **unkalibrierte** Kostenpolicy.
Vor Aktivierung alle Werte prüfen. Secrets ausschließlich über die benannte
Umgebungsvariable bereitstellen; kein Schlüssel gehört in einen Auftrag.

| Feld in cloud_policy | Bedeutung |
|---|---|
| contract | `text_tokens_v1`: ausschließlich die deklarierte Text-Token-Abrechnung |
| data_class | `public`, `synthetic` oder vorher lokal `pseudonymized`; bewusste Datendeklaration |
| input_token_ceiling | Am ausgewählten Provider verifizierte billbare Eingangsobergrenze einschließlich Tools/Formatierung |
| input_price_microusd_per_million | Konservative Preisobergrenze je Million Eingabetokens in Mikro-US-Dollar |
| output_price_microusd_per_million | Entsprechende Ausgabeobergrenze; Reasoning muss enthalten sein |
| max_job_cost_microusd | Maximaler reservierter Betrag für einen Job über alle Versuche |
| max_total_cost_microusd | Gesamtobergrenze dieses lokalen Principals über alle Jobs und Neustarts |
| sensitive_terms | Zusätzliche private Namen/Begriffe, die niemals versandt werden dürfen |
| public_terms | Exakte, vom Operator geprüfte öffentliche Begriffe zur Vermeidung von Fehlalarmen |

1.000.000 Mikro-US-Dollar entsprechen 1 USD. Es gibt keine voreingestellten
aktuellen Modellpreise. Eingangsobergrenze und Preise müssen konservativ zum
tatsächlichen API-Vertrag passen. Der gesamte Eingangshöchstwert plus maximaler
Ausgabe wird für **jede** Anfrage berechnet und auf ganze Mikro-US-Dollar aufgerundet:

```text
Reservierung = ceil((input_token_ceiling × Eingabepreis
                 + maximale_Ausgabetokens × Ausgabepreis) / 1.000.000)
```

Das ist bewusst eine Obergrenze, keine genaue Rechnung. Gemeldete Tokenzahlen
reduzieren sie nicht. Der vollständige Request muss außerdem in das geprüfte
Eingangsbudget passen; derzeit werden seine serialisierten Bytes plus 8192
Einheiten Formatierungsreserve gegen input_token_ceiling geprüft. Andere
Tokenisierung/Abrechnung darf nicht ungeprüft als kompatibel angenommen werden.

Flash wählt das geprüfte Profil und setzt pro Auftrag `allow_cloud=true`. Fehlt
die Policy, wird nichts gesendet. Eine konfigurierte Datenklasse prüft sich nicht
selbst: Der Operator und Flash müssen die tatsächlich gewählten Daten prüfen.
Der lokale Filter ist konservativ und kann Fehlalarme liefern; er ersetzt keine
vollständige PII-Erkennung. Bei vertraulichen Daten zuerst lokal pseudonymisieren
oder einen lokalen Worker verwenden. Freigaben dürfen keine Schlüssel enthalten.

## Status und Fehler

`workspace_job.cloud_budget` nennt `reserved_upper_bound_microusd`,
`principal_reserved_upper_bound_microusd` und `requests_reserved`.
Ein neuer Job oder Manager-Neustart setzt diese Summen nicht zurück.
Erhöhungen einer Kostenpolicy erfordern eine bewusste Operator-Konfiguration;
bereits angefallene Reservierungen bleiben erhalten.

- `CLOUD_POLICY_REQUIRED`: Profil vervollständigen; kein Request gesendet.
- `CLOUD_EGRESS_BLOCKED`: Aufgabe, Dateinamen, spätere Datei-/Tooldaten und Antwort
  lokal prüfen. Die Fehlermeldung verrät keine erkannten Namen oder Schlüssel.
- `CLOUD_INPUT_LIMIT`: explizites Material reduzieren oder das Eingangsbudget am
  Provider neu verifizieren.
- `CLOUD_BUDGET_EXHAUSTED`: weitere Aufrufe sind vor dem Senden blockiert.
- Netzwerk-/Provider-Timeout: Reservierung bleibt belastet; unbekannte Generierung
  bleibt gesperrt und wird nicht automatisch wiederholt.

## Unklare Cloud-Generierung abschließen

Ab v0.48 gibt es einen **lokalen Operatorbefehl**, kein Flash-/Worker-Tool.
Ein normaler Neustart hebt die Sperre weiterhin nicht auf.

1. Beim Provider unabhängig feststellen, dass die Generierung beendet oder
   abgebrochen ist. Ein Timeout, ein geschlossener HTTP-Client oder das Beenden
   von MinionOne beweist dies nicht. Ohne diese Feststellung bleibt der Job gesperrt.
2. MinionOne für genau dieses State-Verzeichnis beenden. Ein laufender Manager
   hält die Prozesssperre; der Befehl verweigert dann jede Auflösung.
3. `work/`, veröffentlichte Dateien und Protokolle des betroffenen Jobs prüfen.
   Die Prüfung ist lesend; Dateien nicht für den Befehl verändern oder löschen.
4. In PowerShell den Job mit seinem echten Job-ID-/Versionswert abschließen:

```powershell
.\app\MinionOne.exe --state "$env:LOCALAPPDATA\MinionOne\WorkspaceSystemTest" resolve-cloud --job "JOB-…" --expected-version 7 --confirm-provider-stopped --confirm-work-inspected
```

Bei einer nativen Flash-Anbindung zusätzlich `--parent-host "C:\Pfad\parent_host.json"`
mit der ursprünglichen Thread-Konfiguration angeben. Das State-Verzeichnis muss
zur gestarteten Instanz gehören; beim eigenen Setup dessen tatsächlichen Pfad
verwenden. Die Versionsprüfung ist optional, aber empfohlen. Den Wert vorher aus
`minionone_job_status` übernehmen. Beide Bestätigungsflags sind verpflichtend.
Für Python: `python -m minionone.pilot_cli` statt `.\app\MinionOne.exe` verwenden.

Die JSON-Quittung meldet `FAILED_NO_RETRY`, die beibehaltene Kostenreservierung
und Prüfsummen. Der Job wird `FAILED` mit `CLOUD_OPERATOR_RESOLVED`. Er wird
weder erneut ausgeführt noch als erfolgreiches Ergebnis übernommen. Alle
Reservierungen bleiben auch bei unbekanntem Provider-Ausgang belastet.
Dieselbe Befehlswiederholung einschließlich `--expected-version` liefert dieselbe
dauerhafte Quittung. Eine veränderte Anfrage wird abgelehnt.

Der Befehl prüft die versiegelte Cloud-Profilbindung, Eingaben, echten Work-Baum,
das vollständige physische Worker-I/O gegen das private Journal und bereits
veröffentlichte Dateien. Beschädigte/fehlende Protokolle, Dateilinks, ausstehende
Publikationen und zugehörige Modell-Leases bleiben gesperrt und benötigen eine
eigene Untersuchung. Der Cloud-Befehl stellt keine lokalen Modelle wieder her.

5. MinionOne anschließend mit derselben Konfiguration neu starten. Bei bestehender
   nativer Bindung wird der Fehler automatisch an den ursprünglichen Flash-Chat
   zurückgegeben; dessen bisherige Zustellungsprüfungen gelten weiter. Weitere
   Aufträge sind wieder möglich, sofern alle übrigen Sperren und Budgets es erlauben.
   Eine erneute fachliche Bearbeitung benötigt einen neuen ausdrücklichen Auftrag.

Die private State-DB, Budgetreservierungen und Arbeitsdateien zur Freigabe niemals
löschen. `resolve` für den alten Dokumentpilot und `reconcile-models` für lokale
Modell-Leases ersetzen `resolve-cloud` nicht.

Keine automatische Anonymisierung, kein Pseudonym-Vault, keine globale Kontrolle
anderer API-Kontonutzer und keine Live-Abnahme echter Modell-/GPU-/Cloudsysteme
werden behauptet. Der Stand bleibt ein Entwicklungstestpaket.
