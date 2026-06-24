def _create_proposal(client, monkeypatch, bez='Kamera'):
    monkeypatch.setattr('app.api.notify_new_proposal', lambda *a, **k: None)
    return client.post('/api/proposals',
                       data={'bezeichnung': bez, 'einreicher_email': 'melder@example.com'},
                       content_type='multipart/form-data').get_json()['nr']


def test_audit_records_create_and_approve(app, auth_client, monkeypatch):
    nr = _create_proposal(auth_client, monkeypatch)
    auth_client.post('/api/proposals/' + nr + '/approve')
    log = auth_client.get('/api/audit').get_json()
    actions = [e['action'] for e in log]
    assert 'proposal.create' in actions
    assert 'proposal.approve' in actions
    approve = next(e for e in log if e['action'] == 'proposal.approve')
    assert nr in approve['entity']


def test_audit_proposal_update_logs_field_diff(app, auth_client, monkeypatch):
    nr = _create_proposal(auth_client, monkeypatch)
    auth_client.put('/api/proposals/' + nr, json={'kosten': 5000})
    log = auth_client.get('/api/audit').get_json()
    upd = next(e for e in log if e['action'] == 'proposal.update')
    assert 'Kosten' in upd['details'] and '5000' in upd['details']


def test_audit_settings_logs_keys_not_secret(app, auth_client):
    auth_client.put('/api/settings', json={'m365_client_secret': 'SUPERSECRET', 'm365_tenant': 'contoso'})
    log = auth_client.get('/api/audit').get_json()
    s = next(e for e in log if e['action'] == 'settings.update')
    assert 'SUPERSECRET' not in s['details']       # Geheimwert NICHT protokollieren
    assert 'm365_client_secret' in s['details']    # nur der Schlüsselname


def test_audit_settings_logs_only_real_changes(app, auth_client):
    auth_client.put('/api/settings', json={'brand_name': 'Foo'})
    n1 = sum(1 for e in auth_client.get('/api/audit').get_json() if e['action'] == 'settings.update')
    auth_client.put('/api/settings', json={'brand_name': 'Foo'})   # identisch → keine Änderung
    n2 = sum(1 for e in auth_client.get('/api/audit').get_json() if e['action'] == 'settings.update')
    assert n2 == n1


def test_audit_filter_by_action_and_text(app, auth_client, monkeypatch):
    nr = _create_proposal(auth_client, monkeypatch)
    auth_client.post('/api/proposals/' + nr + '/approve')
    only = auth_client.get('/api/audit?action=proposal.approve').get_json()
    assert only and all(e['action'] == 'proposal.approve' for e in only)
    txt = auth_client.get('/api/audit?q=genehmigt').get_json()
    assert any('genehmigt' in e['details'].lower() for e in txt)


def test_audit_admin_only(app, client):
    from app import db
    from app.models import User
    with app.app_context():
        u = User(username='bet', role='betrachter')
        u.set_password('secret123')
        db.session.add(u)
        db.session.commit()
    client.post('/api/auth/login', json={'username': 'bet', 'password': 'secret123'})
    assert client.get('/api/audit').status_code == 403


def test_audit_logs_failed_login(app, client):
    client.post('/api/auth/login', json={'username': 'gibtsnicht', 'password': 'x'})
    # Admin anlegen + einloggen, dann Protokoll prüfen
    from app import db
    from app.models import User
    with app.app_context():
        u = User(username='adm', role='admin')
        u.set_password('secret123')
        db.session.add(u)
        db.session.commit()
    client.post('/api/auth/login', json={'username': 'adm', 'password': 'secret123'})
    log = client.get('/api/audit').get_json()
    assert any(e['action'] == 'auth.login_failed' for e in log)
