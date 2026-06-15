def _create(client, username, role='betrachter', email='', notify=None):
    payload = {'username': username, 'password': 'secret123', 'role': role, 'email': email}
    if notify is not None:
        payload['notify'] = notify
    return client.post('/api/users', json=payload)


def test_create_user_stores_email_and_notify(app, auth_client):
    r = _create(auth_client, 'bob', 'beschaffer', 'bob@ff.de', True)
    assert r.status_code == 201
    d = r.get_json()
    assert d['email'] == 'bob@ff.de'
    assert d['notify'] is True


def test_create_beschaffer_defaults_notify_true(app, auth_client):
    r = _create(auth_client, 'b2', 'beschaffer', 'b2@ff.de')  # notify nicht gesetzt
    assert r.get_json()['notify'] is True


def test_create_admin_defaults_notify_false(app, auth_client):
    r = _create(auth_client, 'a2', 'admin', 'a2@ff.de')
    assert r.get_json()['notify'] is False


def test_update_user_email_and_notify(app, auth_client):
    uid = _create(auth_client, 'carol', 'betrachter', 'carolinit@ff.de').get_json()['id']
    r = auth_client.put(f'/api/users/{uid}', json={'email': 'carol@ff.de', 'notify': True})
    assert r.status_code == 200
    d = r.get_json()
    assert d['email'] == 'carol@ff.de'
    assert d['notify'] is True


def test_update_user_fields_admin_only(app, client):
    assert client.put('/api/users/1', json={'email': 'x@y.de'}).status_code == 401
