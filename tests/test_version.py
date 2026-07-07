import io
import json
import urllib.error

import pytest

from app.version import parse_semver, base_version, _update_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    _update_cache.update(latest=None, checked=0.0, ok=False)
    yield
    _update_cache.update(latest=None, checked=0.0, ok=False)


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock_tags(monkeypatch, names, counter=None):
    payload = json.dumps([{'name': n} for n in names]).encode()

    def fake_urlopen(req, timeout=10):
        if counter is not None:
            counter.append(1)
        return _FakeResponse(payload)

    monkeypatch.setattr('app.version.urllib.request.urlopen', fake_urlopen)


# ── Versionsanzeige ─────────────────────────────────────────────────────────────

def test_version_endpoint_ohne_login(client):
    r = client.get('/api/version')
    assert r.status_code == 200
    d = r.get_json()
    assert d['version'] == 'dev'          # kein ENV im Test -> dev


def test_index_zeigt_version(client):
    body = client.get('/').get_data(as_text=True)
    assert 'Version dev' in body          # Login-Dialog


# ── Semver-Helfer ───────────────────────────────────────────────────────────────

def test_parse_semver():
    assert parse_semver('v1.10.2') == (1, 10, 2)
    assert parse_semver('dev') is None
    assert parse_semver('abc1234') is None
    assert parse_semver('v1.2') is None


def test_base_version():
    assert base_version('v1.2.3-4-gabc1234') == 'v1.2.3'
    assert base_version('v1.2.3') == 'v1.2.3'
    assert base_version('dev') == 'dev'


# ── Update-Check ────────────────────────────────────────────────────────────────

def test_update_check_erfordert_admin(client):
    assert client.get('/api/update-check').status_code == 401


def test_update_verfuegbar(app, auth_client, monkeypatch):
    monkeypatch.setenv('APP_VERSION', 'v1.0.0')
    _mock_tags(monkeypatch, ['v0.9.0', 'v1.1.0', 'v1.0.0', 'quatsch'])
    d = auth_client.get('/api/update-check').get_json()
    assert d['current'] == 'v1.0.0'
    assert d['latest'] == 'v1.1.0'        # höchste Semver-Version, 'quatsch' ignoriert
    assert d['update_available'] is True


def test_kein_update(app, auth_client, monkeypatch):
    monkeypatch.setenv('APP_VERSION', 'v1.1.0')
    _mock_tags(monkeypatch, ['v1.1.0', 'v1.0.0'])
    d = auth_client.get('/api/update-check').get_json()
    assert d['update_available'] is False


def test_dev_version_kein_vergleich(app, auth_client, monkeypatch):
    monkeypatch.delenv('APP_VERSION', raising=False)
    _mock_tags(monkeypatch, ['v1.1.0'])
    d = auth_client.get('/api/update-check').get_json()
    assert d['current'] == 'dev'
    assert d['update_available'] is None  # dev nicht vergleichbar


def test_netzwerkfehler_still(app, auth_client, monkeypatch):
    monkeypatch.setenv('APP_VERSION', 'v1.0.0')

    def boom(req, timeout=10):
        raise urllib.error.URLError('offline')

    monkeypatch.setattr('app.version.urllib.request.urlopen', boom)
    r = auth_client.get('/api/update-check')
    assert r.status_code == 200           # Fehler schlägt nie durch
    d = r.get_json()
    assert d['latest'] is None
    assert d['update_available'] is None


def test_cache_nur_ein_http_call(app, monkeypatch):
    from app.version import check_for_update
    monkeypatch.setenv('APP_VERSION', 'v1.0.0')
    calls = []
    _mock_tags(monkeypatch, ['v1.1.0'], counter=calls)
    with app.app_context():
        check_for_update()
        check_for_update()
    assert len(calls) == 1                # zweiter Aufruf kommt aus dem Cache
