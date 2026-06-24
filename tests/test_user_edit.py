def _create(auth_client, username, email):
    r = auth_client.post('/api/users', json={
        'username': username, 'password': 'oldpass1', 'email': email, 'role': 'beschaffer'})
    assert r.status_code == 201
    return r.get_json()['id']


def test_admin_can_change_username_and_password(app, auth_client):
    uid = _create(auth_client, 'hans', 'hans@x.de')
    # Benutzername ändern
    r = auth_client.put('/api/users/' + str(uid), json={'username': 'hans2'})
    assert r.status_code == 200 and r.get_json()['username'] == 'hans2'
    # Passwort zurücksetzen
    assert auth_client.put('/api/users/' + str(uid), json={'password': 'newpass1'}).status_code == 200
    # Login mit neuem Passwort/Namen klappt, mit altem nicht
    c = app.test_client()
    assert c.post('/api/auth/login', json={'username': 'hans2', 'password': 'newpass1'}).status_code == 200
    assert app.test_client().post('/api/auth/login',
                                  json={'username': 'hans2', 'password': 'oldpass1'}).status_code == 401


def test_username_clash_rejected(app, auth_client):
    _create(auth_client, 'a1', 'a1@x.de')
    uid_b = _create(auth_client, 'b1', 'b1@x.de')
    r = auth_client.put('/api/users/' + str(uid_b), json={'username': 'A1'})  # case-insensitiv
    assert r.status_code == 400


def test_short_password_rejected(app, auth_client):
    uid = _create(auth_client, 'c1', 'c1@x.de')
    assert auth_client.put('/api/users/' + str(uid), json={'password': '123'}).status_code == 400


def test_empty_password_keeps_old(app, auth_client):
    uid = _create(auth_client, 'd1', 'd1@x.de')
    # leeres Passwort -> unverändert, kein Fehler
    assert auth_client.put('/api/users/' + str(uid), json={'username': 'd1', 'password': ''}).status_code == 200
    assert app.test_client().post('/api/auth/login',
                                  json={'username': 'd1', 'password': 'oldpass1'}).status_code == 200
