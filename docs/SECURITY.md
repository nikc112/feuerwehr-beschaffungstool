# Sicherheitshinweise & Betrieb

Kurzüberblick über die Schutzmaßnahmen des Tools und die Punkte, die beim
**Deployment** beachtet werden müssen.

## Im Code umgesetzt

- **Authentisierung/Rollen:** `betrachter` < `beschaffer` < `admin`; jede API prüft die Rolle serverseitig.
- **Sitzungs-Timeout:** automatische Abmeldung nach **15 Minuten Inaktivität**
  (serverseitig erzwungen; kein dauerhaftes Remember-Cookie).
- **Betrachter-Zugriff (IDOR-Schutz):** Betrachter sehen Dateien, Angebote, Alternativen und
  Original-E-Mails **nur zu genehmigten** Vorschlägen.
- **Passwörter:** bcrypt/pbkdf2 (Werkzeug), Mindestlänge **8 Zeichen**.
- **Rate-Limiting:** Login und öffentliches Einreichen sind pro IP begrenzt (In-Memory, pro Worker).
- **XSS:** Ausgaben werden HTML-escaped; das öffentliche Formular akzeptiert für Kategorie/
  Anlass/Priorität/Ablauf/Abteilung serverseitig **nur bekannte Werte** (Allowlist).
- **Eingegangene E-Mails** werden ausschließlich in einem `sandbox`-iframe ohne Skript
  dargestellt; Antworten tragen `Content-Security-Policy: script-src 'none'`.
- **Uploads:** nur echte PDFs (Content-Type **und** Endung), Auslieferung mit `nosniff`.
- **Hochgeladene Logos** (auch SVG) werden mit restriktiver CSP + `sandbox` ausgeliefert.
- **Security-Header** (CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`)
  auf allen Antworten.
- **E-Mail-Header-Injection** wird durch Bereinigen der Header verhindert.
- **SECRET_KEY** wird, falls nicht per ENV gesetzt, einmalig zufällig erzeugt und im
  data-Volume persistiert (kein schwacher Default).

## Beim Deployment beachten

1. **HTTPS + `COOKIE_SECURE=true`** setzen, sobald das Tool hinter TLS läuft
   (sichert Session-/Remember-Cookies).
2. **`TRUSTED_PROXIES=<n>`** setzen, wenn ein Reverse-Proxy davor hängt – sonst greift
   das Rate-Limiting auf die Proxy-IP statt der Client-IP.
3. **`SECRET_KEY`** in Produktion explizit per ENV setzen (langer Zufallswert).

## Bekannter Kompromiss: Secrets in der Datenbank

SMTP-/IMAP-/Microsoft-365-Zugangsdaten werden **im Klartext** in der SQLite-Datenbank
(`data/database.db`) gespeichert. Das ist bewusst so: Wer Zugriff auf das `data`-Volume
hat, hat ohnehin vollständigen Zugriff auf die Anwendung. Entsprechend gilt:

- Das `data`-Verzeichnis/-Volume **wie ein Geheimnis behandeln** (Dateirechte, Backups
  verschlüsseln, nicht in Versionskontrolle).
- Für die Microsoft-365-App möglichst eine **Application Access Policy** einrichten, die den
  Zugriff auf genau ein Postfach beschränkt (siehe `MICROSOFT365-SETUP.md`).
- Bei Kompromittierungsverdacht die betroffenen Secrets (SMTP-Passwort, Client-Secret) rotieren.
