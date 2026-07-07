"""Versionsinfos und Update-Check.

Die laufende Version wird beim Docker-Build als ENV (APP_VERSION, GIT_COMMIT,
BUILD_DATE) injiziert; ohne ENV (lokale Entwicklung) ist sie "dev".

Der Update-Check fragt die GitHub-Tags-API ab (best effort, gecacht) und
vergleicht die höchste vX.Y.Z-Version mit der laufenden. Fehler führen nie
zu einer Exception – dann gibt es schlicht keinen Update-Hinweis.
"""
import json
import logging
import os
import re
import time
import urllib.request

logger = logging.getLogger(__name__)

GITHUB_TAGS_URL = ('https://api.github.com/repos/'
                   'nikc112/feuerwehr-beschaffungstool/tags?per_page=100')
CACHE_TTL = 6 * 3600         # Erfolg: 6 h cachen
CACHE_TTL_ERROR = 15 * 60    # Fehler: 15 min, damit die API nicht gehämmert wird

_SEMVER_RE = re.compile(r'^v(\d+)\.(\d+)\.(\d+)$')
_DESCRIBE_SUFFIX_RE = re.compile(r'-\d+-g[0-9a-f]+$')

# {'latest': 'v1.2.3'|None, 'checked': epoch, 'ok': bool}
_update_cache = {'latest': None, 'checked': 0.0, 'ok': False}


def get_version_info():
    """Laufende Version aus dem Build (oder 'dev' in der Entwicklung)."""
    return {
        'version': os.environ.get('APP_VERSION') or 'dev',
        'commit': os.environ.get('GIT_COMMIT') or '',
        'build_date': os.environ.get('BUILD_DATE') or '',
    }


def parse_semver(tag):
    """'v1.2.3' -> (1, 2, 3); alles andere -> None."""
    m = _SEMVER_RE.match(tag or '')
    return tuple(int(g) for g in m.groups()) if m else None


def base_version(version):
    """git-describe-Suffix entfernen: 'v1.2.3-4-gabc1234' -> 'v1.2.3'."""
    return _DESCRIBE_SUFFIX_RE.sub('', version or '')


def fetch_latest_version():
    """Höchste vX.Y.Z-Version aus den GitHub-Tags (gecacht, best effort)."""
    now = time.time()
    ttl = CACHE_TTL if _update_cache['ok'] else CACHE_TTL_ERROR
    if now - _update_cache['checked'] < ttl:
        return _update_cache['latest']

    latest = None
    try:
        req = urllib.request.Request(GITHUB_TAGS_URL, headers={
            'User-Agent': 'FeuerwehrBeschaffungstool',   # von GitHub gefordert
            'Accept': 'application/vnd.github+json',
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            tags = json.loads(r.read().decode('utf-8'))
        versions = [(parse_semver(t.get('name')), t.get('name'))
                    for t in tags if isinstance(t, dict)]
        versions = [(v, name) for v, name in versions if v is not None]
        if versions:
            latest = max(versions)[1]
        _update_cache.update(latest=latest, checked=now, ok=True)
    except Exception as e:   # Netzwerk/JSON/HTTP – niemals durchschlagen lassen
        logger.warning('Update-Check fehlgeschlagen: %s', e)
        _update_cache.update(latest=None, checked=now, ok=False)
    return _update_cache['latest']


def check_for_update():
    """{'current', 'latest', 'update_available'} – update_available ist None,
    wenn kein Vergleich möglich ist (dev-Version oder API nicht erreichbar)."""
    current = get_version_info()['version']
    latest = fetch_latest_version()
    cur_sv = parse_semver(base_version(current))
    lat_sv = parse_semver(latest or '')
    available = None
    if cur_sv is not None and lat_sv is not None:
        available = lat_sv > cur_sv
    return {'current': current, 'latest': latest, 'update_available': available}
