<p align="center"><img src="docs/assets/banner.svg" width="100%" alt="GPT Switch"></p>
<h3 align="center">Lokal wechseln. Die Codex-Umgebung per SSH mitnehmen.</h3>
<p align="center">Verbindung und Anmeldung synchronisieren · Bestehende Chats migrieren · Vorschau · Sicherung · Prüfung</p>
<p align="center"><a href="README.md">English</a> | <a href="README_ZH.md">简体中文</a> | <a href="README_JA.md">日本語</a> | <strong>Deutsch</strong> | <a href="CHANGELOG.md">Änderungsprotokoll</a></p>

---

**GPT Switch** ist ein Windows-Werkzeug mit grafischer Oberfläche für lokale Codex-Umgebungen und Linux-Hosts über SSH. Nach einem lokalen Wechsel mit CC Switch lassen sich die benötigten Verbindungseinstellungen und unterstützten Zugangsdaten auf einen vertrauenswürdigen Host übertragen.

**Die Verbindungssynchronisierung bestimmt die Verbindung neuer Chats. Die Chat-Migration aktualisiert gespeicherte Modell- und Anbieterfelder. Beide Vorgänge sind getrennt.**

> **2.6 ist eine lokale Vorschauversion für den Benutzer-Abnahmetest und noch nicht veröffentlicht.** Das Projekt ist kein offizielles OpenAI-Produkt. Die Oberfläche ist derzeit auf vereinfachtem Chinesisch; Dokumentation ist auf Englisch, Chinesisch, Japanisch und Deutsch verfügbar.

## Funktionen

| Funktion | Beschreibung |
| --- | --- |
| Lokaler Verbindung folgen | Übernimmt gezielt die von CC Switch geschriebenen aktuellen Codex-Einstellungen |
| Authentifizierung prüfen | Unterscheidet geschriebene Dateien, bestätigte Kontoprüfungen, lediglich geladene Zugangsdaten und Fehler wie `workspace-401` |
| Gerätecode-Anmeldung | Startet bei unterstützten Konten eine eigene ChatGPT-Anmeldung auf dem Remote-Host |
| Bestehende Chats migrieren | Vorschau und Aktualisierung von `model` / `model_provider`, einschließlich archivierter Chats |
| SSH-Reparatur | Prüft konkrete PIDs, sichert Daten, beendet bestätigte Halter und kontrolliert das Ergebnis |
| Wiederherstellbare Änderungen | SQLite-Snapshots, geschützte Konfigurationssicherungen, Prüfung veralteter Vorschauen und Rücknahme fehlgeschlagener Schreibvorgänge |

## Schnellstart

- Lokal: Windows mit Python **3.11 oder 3.12** einschließlich Tcl/Tk.
- Remote: Linux mit Python **3.10+** für Synchronisierung; reine Prozessdiagnose auch mit Python 3.6+.
- Windows OpenSSH mit nichtinteraktiver Anmeldung. Den Hostschlüssel bei der ersten Verbindung zunächst im Terminal bestätigen.

```powershell
git clone https://github.com/hialoha/gpt-switch.git
cd gpt-switch
.\start.cmd
```

Kein `pip install` erforderlich. Der Starter bevorzugt ein verfügbares, mit Codex geliefertes Python, danach `python` im PATH. Startfehler lassen sich mit `python app.py` anzeigen. Zum Testen der unveröffentlichten Version 2.6 die bereitgestellte lokale Kopie verwenden.

## Verbindung und Anmeldung synchronisieren

1. Mit CC Switch wechseln und einen **neuen lokalen Chat** testen.
2. **SSH 远端同步…** öffnen, Host auswählen und **跟随本机连接与登录态…** anklicken.
3. Mit **预览本机 → 远端** Ziel, Authentifizierungsart, Endpunkt und zu beendende Backends prüfen. Die Vorschau überträgt einen Fingerabdruck, keine Zugangsdaten.
4. **备份并同步配置与登录态** überträgt nach der Sicherung die Zugangsdaten ausdrücklich über SSH an den angezeigten Host.
5. Prüfergebnis ansehen, SSH-Projekt neu verbinden und einen **neuen Remote-Chat** testen.
6. Bei weiterem ChatGPT-Fehler `workspace-401` die Gerätecode-Anmeldung unten verwenden und anschließend erneut prüfen. Das Konto bzw. der Workspace muss diese Anmeldung erlauben.

Synchronisiert werden Modell, Anbieter, Endpunkt, relevante Anmeldeoptionen und optional der Modellkatalog. Unterstützt werden dateibasierte ChatGPT-Anmeldungen und der API-Schlüssel des ausgewählten Anbieters. Ein ausgewählter Umgebungsvariablen-Schlüssel wird für eine dateibasierte OpenAI-kompatible Remote-Anmeldung normalisiert. Alte Verbindungswerte im aktiven Profil werden ebenfalls aktualisiert.

Remote-Projekte, MCP-Server, Plugins, Berechtigungen, Sandbox-Einstellungen und unabhängige Umgebungsvariablen behalten ihre Werte. TOML wird neu serialisiert: **Kommentare und Formatierung können sich ändern**; die Originaldateien werden vollständig gesichert.

Windows-Anmeldespeicher, reine Arbeitsspeicher-Sitzungen und externe Authentifizierungshelfer werden nicht blind exportiert. ChatGPT-Kontotokens werden nicht in eine benutzerdefinierte Proxy-Konfiguration kopiert. Lokale HTTP-/Loopback-Proxys sind nicht direkt auf einem anderen Rechner nutzbar.

## Bestehende Chats

Nach erfolgreicher Verbindungssynchronisierung können alte Chats bei Bedarf mit **一键修复远端…** migriert werden. Erst werden Datensätze und konkrete PIDs angezeigt, dann Sicherung → Beenden bestätigter Halter → Transaktionsaktualisierung → Prüfung ausgeführt. Interne Auto-Review-Datensätze bleiben unverändert.

Elternprozesse und automatisch neu gestartete PIDs werden nicht beendet. Wenn ein Backend wieder erscheint, die Codex-/VS-Code-Remote-Verbindung zu diesem Host trennen.

Eine Modellliste garantiert keine Inferenzberechtigung. Die Verbindungsprüfung sendet keine Chat-Inferenz; bei API-Schlüsseln kann nur das Laden bestätigt werden. Deshalb einen neuen Chat tatsächlich testen. Das Kopieren von Dateien verleiht keine Workspace-Rechte.

## Sicherungen und Sicherheit

- Konfiguration/Anmeldung: `<CODEX_HOME>/gpt-switch-config-backups/<timestamp>/`.
- Chat-Datenbanken: `gpt-switch-backups/` neben der Datenbank.
- Bereinigte lokale Erfolgsbelege: `ssh-results/` im Werkzeugordner, ohne Zugangsdatenpaket.

Remote-Zugangsdaten erhalten `0600`, Konfigurations-Sicherungsordner `0700`. Auch Sicherungen enthalten nutzbare Zugangsdaten: nur an vertrauenswürdige Hosts und Benutzer übertragen, niemals in Git oder Issues hochladen. Kopierte Anmeldungen können nach Token-Aktualisierung oder Ablauf auseinanderlaufen; eine unabhängige Remote-Anmeldung kann nötig werden.

Nach dem Stoppen der relevanten Backends lässt sich die abgeschlossene Konfigurationssicherung im Verbindungsfenster wiederherstellen. Bei manueller DB-Wiederherstellung aktuelle SQLite-/WAL-/SHM-Dateien separat sichern, den Snapshot zurückspielen und alte WAL/SHM beiseitelegen, bevor Codex startet. Eine vollständige Wiederherstellung setzt auch spätere Datenbankänderungen zurück.

Bei SSH-Abbruch während eines Schreibvorgangs zuerst prüfen, nicht sofort wiederholen. Das X des Hauptfensters beendet das Werkzeug, wartet auf gesendete Schreibvorgänge und versucht, bekannte Gerätecode-Sitzungen abzubrechen. Bei Netzwerkausfall ist der Remote-Abbruch nicht garantiert.

## Fehlerbehebung

- **Neue Chats nutzen weiterhin den alten Anbieter:** Verbindungssynchronisierung statt reiner Chat-Migration verwenden; Profil-, Startargument- und Umgebungsüberschreibungen prüfen.
- **`workspace-401`:** Remote-Anmeldung und Workspace-Zugriff prüfen, gegebenenfalls unabhängig per Gerätecode anmelden.
- **Altes Standardmodell:** PATH kann auf einen älteren Client zeigen. Pfad und Version prüfen. Die Remote-Inspektion überschreibt das gewählte Ziel nicht.
- **Keyring oder Anmeldehelfer:** Unterstützten Dateicache oder eigenständige Remote-Anmeldung nutzen; keine Tokens in Issues einfügen.

## Entwicklung und Referenzen

```powershell
python -m unittest -v test_connection_sync.py test_sync.py test_discovery.py test_occupancy.py test_ssh_remote.py
```

[Tests](docs/TESTING.md) · [Sicherheit](SECURITY.md) · [Projektbeschreibungen](docs/PROJECT_METADATA.md). Live-Tests verwenden temporäre Verzeichnisse und synthetische Zugangsdaten; sie übertragen keine echte lokale Anmeldung und migrieren keine echten Chats.

Inspiriert von [CC Switch](https://github.com/farion1231/cc-switch) und [codex-model-sync](https://github.com/WangShuai666/codex-model-sync). Keine Abhängigkeit von der internen CC-Switch-Datenbank oder einer CC-Switch-Installation auf dem Server. Offizielle Referenzen: [Authentifizierung](https://learn.chatgpt.com/docs/auth) · [App Server](https://learn.chatgpt.com/docs/app-server).
