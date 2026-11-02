# MinionOne v0.47 – Rückfragen fortsetzen und Cloud-Egress begrenzen

Ergänzt [v0.46](OWNER_REQUIREMENTS_V046.md). Die verbindlichen zwei Auftragswege
aus [v0.45](OWNER_REQUIREMENTS_V045.md) bleiben bestehen: direkter Auftrag oder
vorbereitete TASK.md. Flash bleibt der Assistent; ein Worker erhält ausschließlich
explizite Auftragsdaten und ausdrücklich beantwortete Rückfragen.

## Rückfrage und Antwort im selben Job

`ask_question` beendet den aktuellen Worker-Versuch. Vor WAITING_FOR_PARENT
speichert der Manager einen unveränderlichen, gehashten Checkpoint: ausschließlich
Worker-Historie, Arbeitsdatei-/Verzeichnisinventar, Publikationsbelege, Profilbindung,
verbrauchte Budgets und die Frage. Es wird kein Flash-Gespräch übernommen.
Logs werden geschlossen, die Worker-Capability entzogen und lokale Modelle wie
zuvor freigegeben beziehungsweise Flash wiederhergestellt. Der native Parent
erhält die Rückfrage automatisch im ursprünglichen Chat.

`minionone_answer_question` verlangt Job-ID, aktuelle Version, exakten
`question_digest`, expliziten Antworttext und idempotente `operation_id`. Die
Authentifizierung, derselbe Thread, Input-Seal, Checkpoint und Work-Inventar werden
geprüft. Ein neuer Versuch mit neuer Capability setzt denselben Job fort. Dateien,
Worker-Historie, bereits verbrauchte Schleifen/Tools/Tokens und aktive Laufzeit
bleiben erhalten. Das Laufzeitbudget wird beim ersten Start versiegelt; ein
Neustart mit anderer Konfiguration verlängert es nicht. Maximal 20 Versuche,
insgesamt 50 Modellschritte, 60 Toolaufrufe, 150.000 Tokens und drei Denials;
Wartezeit auf Flash zählt nicht als aktive Workerzeit.

Physische Worker- und Manager-Logs werden nur nach vollständiger Prüfung ihres
bisherigen Inhalts zum Anhängen geöffnet. Veränderte Dateien oder Verzeichnisse,
veraltete Fragen/Versionen und konkurrierende Antworten starten keinen zweiten
Versuch. Ein Neustart während WAITING_FOR_PARENT erhält die Fortsetzbarkeit. Ein
Absturz nach Annahme einer Antwort vor belegtem Dispatch wird gesperrt/abgebrochen,
nicht blind erneut ausgeführt. Alte v0.46-Rückfragen ohne Checkpoint werden nicht
nachträglich als fortsetzbar ausgegeben. Dies ist kein allgemeines Timeout-Retry.

Mehrere Benachrichtigungen desselben Jobs werden einzeln protokolliert und über
ihren Digest unterschieden. Alte HTTP-Antworten dürfen keine neuere Rückgabe
überschreiben. Auch eine Antwort während der noch laufenden nativen Run-Annahme
wird lesend mit dem Original-Chat korreliert. Zustellung ersetzt keinen Ergebnis-ACK.

## Cloud-Daten- und Kostenpolicy

Cloud benötigt weiterhin Operatorfreigabe im Profil und `allow_cloud=true` pro
Auftrag. Zusätzlich ist eine explizite `cloud_policy` mit deklarierter Datenklasse,
Text-Token-Abrechnungsvertrag, konservativen Preisobergrenzen und Job-/Gesamtlimit
erforderlich. Alte Cloudprofile ohne Policy können weiterhin gelesen werden,
senden aber keine Inferenz. Eine Profilwahl ist keine automatische Aussage, dass
die tatsächlich übergebenen Daten zur deklarierten Klasse passen; Flash und der
Operator müssen diese Zuordnung prüfen.

Jeder vollständig serialisierte Request wird **vor Journalisierung des Requests
und vor HTTPS** geprüft. Das umfasst später gelesene Dateien, Dateinamen,
Toolantworten, Worker-Historie und Antworten von Flash. Bekannte Schlüssel-/Secret-
Muster und tatsächlich verfügbare Schlüsselwerte werden blockiert. Konservative
PII-Regeln prüfen unter anderem E-Mail, Telefon, Kontodaten, typische Namenpaare,
Adressen und private Benutzerpfade; zusätzliche `sensitive_terms` kommen ausschließlich
aus der Operatorpolicy. Exakte öffentliche Begriffe können über `public_terms`+freigegeben werden; sie überschreiben weder Secret-Sperren noch sensitive_terms.

**Nachweisgrenze:** Dies ist ein regelbasierter Egress-Filter, keine vollständige
semantische PII-Erkennung und kein automatischer Pseudonymisierer. Unbekannte Namen,
Kontextbezüge und beliebige Kodierungen können nicht allgemein erkannt werden.
`public`, `synthetic` oder `pseudonymized` sind explizite Datendeklarationen, keine
maschinellen Zertifikate. Eine lokale Prüfung beziehungsweise vorherige
Pseudonymisierung bleibt notwendig. Ein Pseudonym-Vault und dessen Rückübersetzung
nach T-011 sowie ein zusätzlicher OS-SecretStore werden damit nicht als fertig markiert.

Vor jedem HTTP-Aufruf wird unter einer SQLite-Schreibtransaktion ein konservativer
USD-Betrag reserviert. Joblimit und bisheriger Gesamtverbrauch des Principals
werden gemeinsam geprüft; Profilwechsel, neue Jobs, Antworten und Neustarts
setzen den Gesamtverbrauch nicht zurück. Die Reservierung ist unveränderlich und
wird weder wegen kleinerer gemeldeter Tokenzahlen noch nach Timeout/Verbindungsfehler
erstattet. Ein unbekannter Ausgang bleibt zudem durch das bestehende Inferenz-Fencing
gesperrt. Der Status nennt ausdrücklich reservierte Obergrenzen, keine Rechnungsbeträge.

Die Self-Service-Operator-Recovery für unklare Cloud-Generierungen in Workspace-Jobs
bleibt offen; die Sperre wird nicht durch einen Neustart oder ein Löschen der
Budgetbelege umgangen. Die bestehenden Dokumentpilot-/Modell-Recoverybefehle sind
dafür nicht automatisch freigegeben.

Das Limit gilt unter dem vom Operator eingetragenen Text-Token-/Preisvertrag des
Providers. Eingangstoken-Obergrenze, Werkzeug-/Formatierungsaufwand, billbare
Ausgabe einschließlich Reasoning und Preise müssen zum konkreten Modell passen.
Zusatzgebühren, Bild-/Audioabrechnung, andere Clients desselben API-Kontos und ein
Provider, der den vereinbarten Vertrag verletzt, sind keine durch MinionOne
kontrollierte Rechnung. Ein zusätzliches Limit beim Provider ist dafür separat
einzurichten. Es werden keine aktuellen Preise geraten oder automatisch ersetzt.

## Bedienung und Nachweise

[Deutsche Anleitung](../system_test/JOB_CONTINUATION_CLOUD_DE.md).
Tests verwenden reale MCP-/HTTP-/Datei-/SQLite-Wege mit kontrollierten Modellantworten.
Die gebaute Windows-EXE prüft Frage → explizite Antwort → derselbe Job → zweite
Original-Chat-Rückgabe sowie Cloud-Ablehnungen vor dem Senden. Es gibt keine
Behauptung über echte Cloudabrechnung oder Zielhost-/GPU-Abnahme. PRE-ALPHA / NO-GO bleibt.
