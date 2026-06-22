import io


def _make_user(app, role, username):
    from app import db
    from app.models import User
    with app.app_context():
        u = User(username=username, role=role)
        u.set_password('secret123')
        db.session.add(u)
        db.session.commit()


def test_default_logo_served_without_custom(app, client):
    r = client.get('/api/branding/logo')
    assert r.status_code == 200
    assert 'svg' in r.headers.get('Content-Type', '')
    assert b'<svg' in r.data


def test_logo_response_has_xss_protection_headers(app, client):
    r = client.get('/api/branding/logo')
    csp = r.headers.get('Content-Security-Policy', '')
    assert 'sandbox' in csp and "default-src 'none'" in csp
    assert r.headers.get('X-Content-Type-Options') == 'nosniff'


def test_settings_returns_brand_keys_and_defaults(app, auth_client):
    d = auth_client.get('/api/settings').get_json()
    for k in ('brand_name', 'brand_subtitle', 'brand_color_primary', 'brand_color_accent', 'brand_color_bg'):
        assert k in d
    assert d['_default_brand_color_primary'] == '#0785B7'
    assert d['_default_brand_name']


def test_put_brand_persists_and_renders(app, client, auth_client):
    r = auth_client.put('/api/settings', json={
        'brand_name': 'Stadt Testdorf',
        'brand_color_primary': '#123456',
    })
    assert r.status_code == 200
    body = client.get('/').get_data(as_text=True)
    assert 'Stadt Testdorf' in body
    assert '#123456' in body  # injizierte Primärfarbe


def test_brand_default_is_neutral_musterstadt(app, client):
    body = client.get('/').get_data(as_text=True)
    assert 'Freiwillige Feuerwehr Musterstadt' in body
    assert 'Moorrege' not in body


def test_email_default_uses_brand_name(app, auth_client):
    auth_client.put('/api/settings', json={'brand_name': 'Stadt Testdorf'})
    d = auth_client.get('/api/settings').get_json()
    assert 'Stadt Testdorf' in d['_default_subject']
    assert 'Stadt Testdorf' in d['_default_body']
    assert 'Moorrege' not in d['_default_body']


def test_logo_upload_and_delete(app, auth_client):
    png = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 32)
    r = auth_client.post('/api/branding/logo',
                         data={'logo': (io.BytesIO(png), 'logo.png')},
                         content_type='multipart/form-data')
    assert r.status_code == 200
    served = auth_client.get('/api/branding/logo')
    assert served.status_code == 200
    assert served.data.startswith(b'\x89PNG')
    # entfernen -> wieder Default-SVG
    assert auth_client.delete('/api/branding/logo').status_code == 200
    assert b'<svg' in auth_client.get('/api/branding/logo').data


def test_logo_upload_rejects_bad_extension(app, auth_client):
    r = auth_client.post('/api/branding/logo',
                         data={'logo': (io.BytesIO(b'x'), 'evil.exe')},
                         content_type='multipart/form-data')
    assert r.status_code == 400


def test_branding_write_requires_admin(app, client):
    _make_user(app, 'betrachter', 'bet')
    client.post('/api/auth/login', json={'username': 'bet', 'password': 'secret123'})
    assert client.put('/api/settings', json={'brand_name': 'X'}).status_code == 403
    assert client.post('/api/branding/logo', data={}, content_type='multipart/form-data').status_code == 403


def test_brand_address_default_and_persists(app, auth_client):
    d = auth_client.get('/api/settings').get_json()
    assert d['_default_brand_address']  # neutraler Default vorhanden
    assert 'Moorrege' not in d['_default_brand_address']
    auth_client.put('/api/settings', json={'brand_address': 'Teststr. 5 · 99999 Testdorf'})
    assert auth_client.get('/api/settings').get_json()['brand_address'] == 'Teststr. 5 · 99999 Testdorf'


def test_email_default_includes_brand_address(app, auth_client):
    auth_client.put('/api/settings', json={'brand_address': 'Teststr. 5 · 99999 Testdorf'})
    body = auth_client.get('/api/settings').get_json()['_default_body']
    assert 'Teststr. 5 · 99999 Testdorf' in body


def test_no_suppliers_in_delivery_state(app, auth_client):
    assert auth_client.get('/api/suppliers').get_json() == []


def test_manifest_reflects_brand(app, client, auth_client):
    auth_client.put('/api/settings', json={'brand_name': 'Stadt Testdorf', 'brand_color_primary': '#abcdef'})
    m = client.get('/manifest.webmanifest').get_json()
    assert 'Stadt Testdorf' in m['name']
    assert m['theme_color'] == '#abcdef'
