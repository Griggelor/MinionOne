# MinionOne v0.48 – unklare Cloud-Generierungen kontrolliert abschließen

Ergänzt den [v0.47-Vertrag](OWNER_REQUIREMENTS_V047.md) um dessen offenen
Cloud-Recovery-Punkt. Der universelle Auftrag kommt weiterhin von Flash: direkt
oder als vorbereitete TASK.md/Dateien, ohne impliziten Flash-Kontexttransfer.
Keine neue Rollen-, Reviewer- oder Textschnittstelle.

## Verhalten

`resolve-cloud` arbeitet ausschließlich offline unter der State-Prozesssperre.
Der lokale Operator bestätigt unabhängig das Ende der Provider-Generierung und
die Sichtprüfung der Arbeitsdateien. Der Befehl selbst kann das Ende einer
entfernten Generierung nicht beweisen und behauptet es nicht.

Zugelassen sind nur authentifizierte, threadgebundene WORKSPACE-Jobs mit intakter
versiegelter Cloud-Profilbindung, zugehörigen dauerhaften Kostenreservierungen
und unklarem aktiven/quarantinierten Zustand. Bei einem Prozessabbruch wird nur
der ausgewählte Job gefenced; andere Jobs und Modell-Leases werden nicht
global reconciled. Jede Modell-Lease des Zieljobs verhindert diesen Pfad.
Bereite Ergebnisse, abgeschlossene Jobs und pausierte Fragen werden abgelehnt.

Lesend geprüft werden versiegelte Eingaben, der vollständige native Work-Baum
einschließlich Verzeichnissen, das physische Worker-I/O gegen seine private
Hashkette, vollständige JSONL-Logs und vorhandene PUBLISHED-Dateien. PENDING-
Publikationen, Dateilinks und Integritätsfehler verhindern den Abschluss.
Es wird nichts repariert, neu veröffentlicht, gelöscht oder abgeschnitten.

Der Abschluss widerruft Fähigkeiten, hebt ausschließlich die unsichere
Generierungssperre dieses Jobs auf und setzt ihn auf FAILED. Eine unveränderliche
Operatorquittung speichert Herkunftszustand/-version, Prüfhashes, Kostenstand und
die Art der unabhängigen Bestätigung. Identische Befehle sind idempotent;
veränderte Anfragen und veraltete optionale CAS-Versionen werden abgelehnt.

Es gibt keinen erneuten Worker-/HTTP-Aufruf, keinen Modellwechsel, keine
Ergebnisannahme und keine Erstattung. Kostenreservierungen bleiben bytegleich;
principalweite Summen gelten über Neustarts und neue Jobs weiter. Ein normal
neu gestarteter Manager stellt die Fehlerrückgabe über die ursprüngliche native
Parent-Bindung zu. Die Zustellungs-/Quittierungsregeln bleiben unverändert.

## Nachweise und Grenzen

`tests/test_cloud_recovery_v048.py` prüft echte MCP-/Datei-/DB-Wege,
Bestätigungen, Autorisierung/CAS/Idempotenz, Prozesssperre, beschädigte Eingaben,
Logs und Dateilinks, Publikations-/Modellgrenzen, Neustart/Budgets und die native
Original-Chat-Fehlerrückgabe. Modellantworten/HTTPS-Transport sind kontrolliert.
Die Windows-EXE-Prüfung ergänzt einen Loopback-TLS-Abbruch nach Reservierung,
Offline-Abschluss, identische Wiederholung und Budget-Fencing nach Neustart.
Dabei gibt es keine echte Cloud-Generierung oder Rechnung.

Kein Pseudonym-Vault, keine automatische Anonymisierung und keine Live-Abnahme
von Studio/UI/GPU oder realen Providern. PRE-ALPHA und Produkt-NO-GO bleiben.
[Bedienung](../system_test/JOB_CONTINUATION_CLOUD_DE.md).
