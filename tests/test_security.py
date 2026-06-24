import io


def _user(app, role, name):
    from app import db
    from app.models import User
    with app.app_context():
        u = User(username=name, role=role)
        u.set_password('secret123')
        db.session.add(u)
        db.session.commit()


def _login(client, name):
    return client.post('/api/auth/login', json={'username': name, 'password': 'secret123'})


def _create_proposal(client, monkeypatch, **extra):
    monkeypatch.setattr('app.api.notify_new_proposal', lambda *a, **k: None)
    data = {'bezeichnung': 'X'}
    data.update(extra)
    r = client.post('/api/proposals', data=data, content_type='multipart/form-data')
    return r.get_json()['nr']


# ── #2 Lieferanten: nur Beschaffer/Admin dürfen schreiben ──────────────────────
def test_supplier_write_requires_beschaffer(app, client):
    _user(app, 'betrachter', 'bet')
    _login(client, 'bet')
    assert client.post('/api/suppliers', json={'name': 'X', 'email': 'x@y.de'}).status_code == 403
    assert client.put('/api/suppliers/1', json={'name': 'X', 'email': 'x@y.de'}).status_code == 403
    assert client.delete('/api/suppliers/1').status_code == 403


def test_supplier_write_allowed_for_beschaffer(app, client):
    _user(app, 'beschaffer', 'bes')
    _login(client, 'bes')
    r = client.post('/api/suppliers', json={'name': 'Acme', 'email': 'a@acme.de'})
    assert r.status_code == 201


# ── #3 Vorschlag bearbeiten: nur Beschaffer/Admin ──────────────────────────────
def test_update_proposal_requires_beschaffer(app, client, monkeypatch):
    nr = _create_proposal(client, monkeypatch)
    _user(app, 'betrachter', 'bet')
    _login(client, 'bet')
    r = client.put('/api/proposals/' + nr, json={'notizen': 'hack'})
    assert r.status_code == 403


# ── #4 Upload: nur echte PDFs, nosniff beim Ausliefern ─────────────────────────
def test_upload_rejects_spoofed_html(app, client, auth_client, monkeypatch):
    nr = _create_proposal(client, monkeypatch,
                          files=(io.BytesIO(b'<script>alert(1)</script>'), 'evil.html', 'application/pdf'))
    p = [x for x in auth_client.get('/api/proposals?status=pending').get_json() if x['nr'] == nr][0]
    assert p['files'] == []


def test_upload_accepts_pdf_and_sets_nosniff(app, client, auth_client, monkeypatch):
    nr = _create_proposal(client, monkeypatch,
                          files=(io.BytesIO(b'%PDF-1.4 test'), 'doc.pdf', 'application/pdf'))
    p = [x for x in auth_client.get('/api/proposals?status=pending').get_json() if x['nr'] == nr][0]
    assert len(p['files']) == 1 and p['files'][0]['path'].endswith('.pdf')
    up = auth_client.get('/api/uploads/' + p['files'][0]['path'])
    assert up.status_code == 200
    assert up.headers.get('X-Content-Type-Options') == 'nosniff'


# ── #5 Remember-Cookie SameSite gesetzt ────────────────────────────────────────
def test_remember_cookie_samesite(app):
    assert app.config['REMEMBER_COOKIE_SAMESITE'] == 'Lax'
    assert app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'


# ── #6 SECRET_KEY: kein schwacher Default, persistent ──────────────────────────
def test_secret_key_not_weak_and_persistent(tmp_path, monkeypatch):
    from app import _resolve_secret_key
    monkeypatch.delenv('SECRET_KEY', raising=False)
    k1 = _resolve_secret_key(str(tmp_path))
    k2 = _resolve_secret_key(str(tmp_path))
    assert k1 == k2                      # persistent über Aufrufe
    assert k1 not in ('bitte-aendern', 'bitte-aendern-mit-langem-zufallswert', '')
    assert len(k1) >= 32

    monkeypatch.setenv('SECRET_KEY', 'bitte-aendern')
    assert _resolve_secret_key(str(tmp_path)) == k1   # schwacher Default wird ignoriert


# ── #7 E-Mail-Header-Injection verhindert ──────────────────────────────────────
def test_clean_header_strips_newlines():
    from app.email_service import _clean_header
    assert '\n' not in _clean_header('a\r\nBcc: evil@x')
    assert '\r' not in _clean_header('a\r\nBcc: evil@x')


def test_send_email_rejects_injected_recipient():
    from app.email_service import send_email
    import pytest
    with pytest.raises(ValueError):
        send_email('opfer@x.de\nBcc: evil@x.de', 'Name', 'Betreff', 'Body')


# ── #9 Rate-Limiting ───────────────────────────────────────────────────────────
def test_ratelimit_allowed_blocks_after_limit():
    from app.ratelimit import _allowed
    k = 'unit-test-key'
    assert _allowed(k, 2, 100, now=1000)[0] is True
    assert _allowed(k, 2, 100, now=1001)[0] is True
    ok, retry = _allowed(k, 2, 100, now=1002)
    assert ok is False and retry > 0
    assert _allowed(k, 2, 100, now=2000)[0] is True   # nach Fenster wieder frei


def test_login_rate_limit_returns_429(app, client, monkeypatch):
    monkeypatch.setattr('app.ratelimit._allowed', lambda *a, **k: (False, 7))
    app.config['RATELIMIT_FORCE'] = True
    r = client.post('/api/auth/login', json={'username': 'x', 'password': 'y'})
    assert r.status_code == 429
    assert r.headers.get('Retry-After') == '7'


# ── #11 IDOR: Betrachter nur Dateien genehmigter Vorschläge ────────────────────
def test_upload_idor_betrachter(app, client, monkeypatch):
    _user(app, 'admin', 'adm')
    _user(app, 'betrachter', 'bet')
    nr = _create_proposal(client, monkeypatch,
                          files=(io.BytesIO(b'%PDF-1.4 x'), 'd.pdf', 'application/pdf'))
    _login(client, 'adm')
    p = [x for x in client.get('/api/proposals?status=pending').get_json() if x['nr'] == nr][0]
    path = p['files'][0]['path']
    _login(client, 'bet')
    assert client.get('/api/uploads/' + path).status_code == 403   # pending -> verboten
    _login(client, 'adm')
    client.post('/api/proposals/' + nr + '/approve')
    _login(client, 'bet')
    assert client.get('/api/uploads/' + path).status_code == 200   # genehmigt -> erlaubt
