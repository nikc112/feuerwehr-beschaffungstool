# Microsoft 365 (Graph) einrichten – Admin-Anleitung

Diese Anleitung beschreibt, was ein **Microsoft-365-Administrator** einmalig
einrichten muss, damit das Beschaffungstool E-Mails über Microsoft 365
**versenden** (`Mail.Send`) und **abrufen** (`Mail.Read`) kann.

Das Tool nutzt **App-only OAuth2 (Client-Credentials)** über Microsoft Graph –
kein interaktiver Login, kein Benutzerpasswort. Es greift auf **genau ein
Postfach** zu (z. B. `beschaffung@deine-domain.de`).

> Tipp: Lege als Postfach am besten ein **freigegebenes Postfach (Shared
> Mailbox)** an – das braucht keine eigene Lizenz. Genau an dieses Postfach
> sollten die Lieferanten-Antworten gehen (Absender/Reply-To der Anfragen).

---

## 1. App-Registrierung anlegen (Entra ID)

1. <https://entra.microsoft.com> (oder Azure-Portal) → **Identität → Anwendungen → App-Registrierungen → Neue Registrierung**.
2. **Name:** z. B. `FF Beschaffungstool Mail`.
3. **Unterstützte Kontotypen:** „Nur Konten in diesem Organisationsverzeichnis (einzelner Mandant)".
4. **Umleitungs-URI:** leer lassen (wird nicht benötigt).
5. **Registrieren**.
6. Auf der **Übersichtsseite** notieren:
   - **Anwendungs-(Client-)ID**
   - **Verzeichnis-(Tenant-)ID**

## 2. Client-Secret erstellen

1. In der App → **Zertifikate & Geheimnisse → Geheime Clientschlüssel → Neuer geheimer Clientschlüssel**.
2. Beschreibung + Ablauf wählen (z. B. **24 Monate**) → **Hinzufügen**.
3. Den **Wert** (nicht die „Geheimnis-ID") **sofort kopieren** – er wird nur einmal angezeigt.
   > ⚠️ Vor Ablauf rechtzeitig erneuern, sonst bricht der Versand/Abruf ab.

## 3. Graph-Berechtigungen + Admin-Zustimmung

1. In der App → **API-Berechtigungen → Berechtigung hinzufügen → Microsoft Graph → Anwendungsberechtigungen**.
2. Hinzufügen:
   - **`Mail.Send`**
   - **`Mail.Read`**
3. **„Administratorzustimmung für <Organisation> erteilen"** klicken → bestätigen.
4. Beide Berechtigungen müssen den Status **„Erteilt"** (grün) zeigen.

> Die optionale Standard-Berechtigung `User.Read` (delegiert) kann bleiben oder
> entfernt werden – sie wird nicht benötigt.

## 4. Zugriff auf EIN Postfach beschränken (dringend empfohlen)

Ohne diese Einschränkung dürfte die App **auf ALLE Postfächer** des Tenants
zugreifen. Mit einer **Application Access Policy** wird der Zugriff auf das
Beschaffungspostfach begrenzt.

1. **Mail-aktivierte Sicherheitsgruppe** anlegen (Microsoft-365-Admin-Center →
   Teams & Gruppen, oder Exchange Admin Center), z. B.
   `grp-ffbeschaffung@deine-domain.de`, und **nur das Beschaffungspostfach**
   als Mitglied aufnehmen.
2. **Exchange Online PowerShell** verbinden:
   ```powershell
   Install-Module ExchangeOnlineManagement   # einmalig, falls nicht vorhanden
   Connect-ExchangeOnline
   ```
3. Policy anlegen (Client-ID aus Schritt 1 einsetzen):
   ```powershell
   New-ApplicationAccessPolicy `
     -AppId "<CLIENT-ID>" `
     -PolicyScopeGroupId "grp-ffbeschaffung@deine-domain.de" `
     -AccessRight RestrictAccess `
     -Description "FF Beschaffungstool: nur Beschaffungspostfach"
   ```
4. Prüfen:
   ```powershell
   Test-ApplicationAccessPolicy -AppId "<CLIENT-ID>" -Identity beschaffung@deine-domain.de   # -> Granted
   Test-ApplicationAccessPolicy -AppId "<CLIENT-ID>" -Identity irgendwer@deine-domain.de     # -> Denied
   ```
   > Die Policy kann bis zu ~30 Minuten brauchen, bis sie greift.

## 5. Im Beschaffungstool eintragen

**Einstellungen → E-Mail → Verbindungsart = „Microsoft 365 (Graph / OAuth2)"**, dann:

| Feld | Wert |
|---|---|
| Verzeichnis-(Tenant-)ID | aus Schritt 1 |
| Anwendungs-(Client-)ID | aus Schritt 1 |
| Client-Secret | der **Wert** aus Schritt 2 |
| Postfach (Absender/Empfang) | z. B. `beschaffung@deine-domain.de` |

→ **Microsoft 365 speichern** → **„Jetzt abrufen (Test)"** klicken.
Erfolg: „✓ … E-Mail(s) geprüft". Fehler werden mit der genauen Graph-Meldung angezeigt.

---

## Fehlerbehebung (häufige Graph-Meldungen)

| Meldung / Code | Ursache & Lösung |
|---|---|
| `Authorization_RequestDenied` / 403 | Admin-Zustimmung fehlt oder `Mail.Send`/`Mail.Read` nicht als **Anwendungsberechtigung** vergeben (Schritt 3). |
| `ErrorAccessDenied` beim Senden/Abrufen | **Application Access Policy** verweigert das Postfach (falsche Gruppe/Mitglied) oder Postfach-Adresse falsch (Schritt 4/5). |
| `invalid_client` / Token-Fehler | Client-Secret falsch oder **abgelaufen** → in „Zertifikate & Geheimnisse" neu erstellen. |
| 404 / „mailbox not found" | Postfach-Adresse falsch geschrieben oder existiert nicht. |
| Policy „Denied", obwohl korrekt | Bis zu 30 Min. Wartezeit nach Anlegen/Ändern. |

## Hinweise
- Solange die Verbindungsart auf **„IMAP/SMTP"** steht, ändert sich nichts –
  Umstellen und Zurückschalten ist jederzeit gefahrlos möglich.
- Es wird **kein** Benutzerpasswort gespeichert, nur Tenant-/Client-ID +
  Client-Secret (Secret in der Oberfläche maskiert).
- Empfang: Das Tool liest **ungelesene** Mails im Postfach-Eingang, ordnet sie
  über den Betreff-Tag `[FF-<Nr>]` dem Vorgang zu, speichert PDF-Anhänge und die
  Original-Mail (`.eml`) und markiert die Mail als gelesen.
