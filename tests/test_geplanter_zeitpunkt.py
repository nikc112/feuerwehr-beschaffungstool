def _make_user(app, role, username):
    from app import db
    from app.models import User
    with app.app_context():
        u = User(username=username, role=role)
        u.set_password('secret123')
        db.session.add(u)
        db.session.commit()


def _create_proposal(client, monkeypatch):
    monkeypatch.setattr('app.api.notify_new_proposal', lambda *a, **k: None)
    r = client.post('/api/proposals', data={'bezeichnung': 'Wärmebildkamera'},
                    content_type='multipart/form-data')
    return r.get_json()['nr']


def test_new_proposal_has_empty_zeitpunkt(app, client, monkeypatch):
    _create_proposal(client, monkeypatch)
    _make_user(app, 'admin', 'adm')
    client.post('/api/auth/login', json={'username': 'adm', 'password': 'secret123'})
    p = client.get('/api/proposals?status=pending').get_json()[0]
    assert p['geplanter_zeitpunkt'] == ''


def test_beschaffer_can_set_zeitpunkt(app, client, monkeypatch):
    nr = _create_proposal(client, monkeypatch)
    _make_user(app, 'beschaffer', 'bes')
    client.post('/api/auth/login', json={'username': 'bes', 'password': 'secret123'})
    r = client.put('/api/proposals/' + nr, json={'geplanter_zeitpunkt': '2028'})
    assert r.status_code == 200
    assert r.get_json()['geplanter_zeitpunkt'] == '2028'


def test_betrachter_cannot_set_zeitpunkt(app, client, monkeypatch):
    nr = _create_proposal(client, monkeypatch)
    _make_user(app, 'betrachter', 'bet')
    client.post('/api/auth/login', json={'username': 'bet', 'password': 'secret123'})
    r = client.put('/api/proposals/' + nr, json={'geplanter_zeitpunkt': '2028'})
    assert r.status_code == 200
    # Feld bleibt leer – Betrachter darf den Zeitpunkt nicht setzen
    assert r.get_json()['geplanter_zeitpunkt'] == ''
