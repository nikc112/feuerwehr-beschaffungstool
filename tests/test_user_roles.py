def _create_user(client, username, role='betrachter'):
    r = client.post('/api/users', json={'username': username, 'password': 'secret123',
                                        'role': role, 'email': username + '@ff.de'})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()['id']


def test_admin_can_change_other_user_role(app, auth_client):
    uid = _create_user(auth_client, 'bob', 'betrachter')
    r = auth_client.put(f'/api/users/{uid}', json={'role': 'admin'})
    assert r.status_code == 200
    assert r.get_json()['role'] == 'admin'
    # Persistiert?
    users = {u['id']: u for u in auth_client.get('/api/users').get_json()}
    assert users[uid]['role'] == 'admin'


def test_change_role_to_beschaffer(app, auth_client):
    uid = _create_user(auth_client, 'carol', 'betrachter')
    r = auth_client.put(f'/api/users/{uid}', json={'role': 'beschaffer'})
    assert r.status_code == 200
    assert r.get_json()['role'] == 'beschaffer'


def test_invalid_role_rejected(app, auth_client):
    uid = _create_user(auth_client, 'dave')
    assert auth_client.put(f'/api/users/{uid}', json={'role': 'superuser'}).status_code == 400


def test_cannot_demote_own_admin(app, auth_client):
    me = auth_client.get('/api/auth/me').get_json()['user']
    r = auth_client.put(f"/api/users/{me['id']}", json={'role': 'betrachter'})
    assert r.status_code == 400


def test_role_change_requires_login(app, client):
    # Nicht eingeloggt → 401
    assert client.put('/api/users/1', json={'role': 'admin'}).status_code == 401
