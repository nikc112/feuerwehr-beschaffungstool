# Releases & Versionierung

Die App wird über **Git-Tags** versioniert (Semantic Versioning: `vMAJOR.MINOR.PATCH`).

## Neue Version veröffentlichen

```bash
git tag v1.1.0
git push origin v1.1.0
```

Das ist alles. Die CI (GitHub Actions) baut daraufhin automatisch:

| Auslöser | Docker-Image-Tags | Angezeigte Version |
|---|---|---|
| Tag-Push `v1.1.0` | `:v1.1.0` **und** `:latest` | `v1.1.0` |
| Push auf `main` (ohne Tag) | nur `:latest` | `v1.1.0-3-gabc1234` (letzter Tag + Abstand + Commit) |

Die Version wird beim Build per `git describe --tags` ermittelt und als
`APP_VERSION`/`GIT_COMMIT`/`BUILD_DATE` ins Image injiziert (siehe `Dockerfile`).

## Versionswahl (Semver)

- **PATCH** (`v1.0.0` → `v1.0.1`): Bugfixes, kleine Korrekturen
- **MINOR** (`v1.0.1` → `v1.1.0`): neue Funktionen, abwärtskompatibel
- **MAJOR** (`v1.1.0` → `v2.0.0`): grundlegende Änderungen (z. B. Datenformat)

## Wo die Version sichtbar ist

- **Login-Dialog**: dezent unter dem Formular („Version v1.1.0")
- **Einstellungen** (nur Admins): „Installierte Version" oben, inkl. Update-Hinweis
- **API**: `GET /api/version` (öffentlich) → `{version, commit, build_date}`

## Update-Hinweis

Beim Öffnen der Einstellungen fragt der Server die **GitHub-Tags-API** ab
(`app/version.py`, gecacht für 6 Stunden) und vergleicht die höchste `vX.Y.Z`-Version
mit der laufenden. Ist eine neuere verfügbar, erscheint oben der orange Hinweis
„Neue Version verfügbar" mit dem Update-Befehl:

```bash
docker compose pull && docker compose up -d
```

Grenzen (gewollt, best effort):

- Ohne Internetzugang des Servers gibt es **keinen** Hinweis (und keinen Fehler).
- Im Dev-Modus (Version `dev`) oder vor dem ersten Tag findet kein Vergleich statt.

## Server auf eine feste Version pinnen (optional)

In `docker-compose.yml` statt `:latest` einen Versions-Tag verwenden:

```yaml
image: ghcr.io/nikc112/feuerwehr-beschaffungstool:v1.1.0
```
