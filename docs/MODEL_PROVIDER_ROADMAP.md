# MinionOne – Modellprovider und Adaptervertrag (v0.20 WIP)

## Abgleich mit ursprünglichem Spezifikationsstand

Die frühere Traceability nennt explizit:

| Testfall | Vorgabe | Realität v0.20 |
| --- | --- | --- |
| T-034 / FR-35 | Flint über LM Studio | Begrenzter v0.18-SQLite-Testjob implementiert; echte Hardware-Integration offen |
| T-035 / FR-36 | Unsloth-internes Modell über native Funktionen wählen und aufrufen | Nicht implementiert; API-Fähigkeiten erst am laufenden Unsloth prüfen |
| T-036 / FR-37/38 | Cloud-API mit SecretStore, Sicherheitsgate | Nicht implementiert; standardmäßig verboten |
| T-038 / FR-39 | Modellprofil unabhängig vom Backend | Nicht implementiert; verbindlicher Adaptervertrag erforderlich |
| T-025/026 | Provider-Discovery plus kuratierte Modellprofile, `ready=false` falls down | LM-Studio-IDs best effort, nicht verifizierte Beschreibungen; Profile fehlen |
| T-039 | Parent-Residency: kein erzwungenes Evict | Nur Policy-/Teiltests; echter Provider-Wechsel offen |
| T-055 | HTTP401/429/503/Timeout strukturiert, budgetierte Retries | Nur für bestehenden Pilotadapter teilweise belegt |

## Verbindliche Zielaufteilung

**WorkerProfile** (vertrauenswürdige Managerkonfiguration, nicht aus LLM-Ausgabe abgeleitet):
`id`, `description`, `task_types`, `capability_requirements`, `provider_id`, `model_id`,
`context_limit`, `token_budget`, `latency_budget`, `data_policy`, `resource_policy`.

**ProviderAdapter** (keine Berechtigungsentscheidung):
- `discover()` → konkrete Modelle samt Lade-/Ready-Status und Zeitpunkt der Abfrage.
- `capabilities(model_id)` → belegbare Fähigkeiten, sonst UNKNOWN.
- `preflight(profile, parent_residency)` → READY / DENIED / UNKNOWN mit Begründung.
- `invoke_bounded(job_id, attempt_id, messages, tool_schema, budgets)` → normierte Tokens/Events.
- `cancel(attempt_id)` → bestätigtes Ende / QUARANTINED / UNKNOWN.

Provider dürfen **keine** ungeprüften Worker-Tool-Aufrufe selbst ausführen. Der lokale Manager ist alleiniger Owner für Datei-Capabilities, Jobstatus, Budgets und Veröffentlichung.

Provider-IDs: `lmstudio-local`, `unsloth-native`, `openai-cloud`; weitere OpenAI-kompatible lokale APIs können später eigene Adapter erhalten. Ein OpenAI-kompatibles HTTP-Format garantiert **nicht** einheitliche Semantik für Toolcalling, Modell-Residency, Cancellation oder Structured Outputs.

### Sicherheits- und Datenschutz-Gates

- Standard: **local-only**, Cloud-Egress deaktiviert.
- Unsloth-nativ: nur nach tatsächlich nachgewiesenen APIs; keine erfundenen internen Endpunkte.
- Externe OpenAI-API: explizite Operatorfreigabe je Job, sichere Credential-Verwahrung, erlaubte Datenklassen, DLP/PII-Egress-Gate und Budgetobergrenze; keine versteckte automatische Provider-Umschaltung.
- Kein Modellstart bei unsicherem NTFS-Broker, unbekannter Parent-Sitzung oder unklarem Parent-Unload.

## Release-Evidenz

Provider-Vertragstests dürfen als `PASS_LOCAL_CONTRACT_NOT_TARGET_E2E` gelten, echte Unsloth-/Windows-/Cloud-E2E-Gates erst mit nachgewiesenem Lauf. Unimplementierte Testfälle bleiben `NOT_VERIFIED`. Bei lokalen Providerfehlern kein stiller Cloud-Fallback.
