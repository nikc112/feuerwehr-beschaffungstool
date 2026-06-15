def test_create_requires_email(app, auth_client):
    r = auth_client.post('/api/users', json={'username': 'x', 'password': 'secret123', 'role': 'betrachter'})
    assert r.status_code == 400


def test_create_invalid_email(app, auth_client):
    r = auth_client.post('/api/users', json={'username': 'x', 'password': 'secret123',
                                             'role': 'betrachter', 'email': 'notanemail'})
    assert r.status_code == 400


def test_create_duplicate_email_rejected(app, auth_client):
    auth_client.post('/api/users', json={'username': 'a', 'password': 'secret123',
                                         'role': 'betrachter', 'email': 'dup@ff.de'})
    r = auth_client.post('/api/users', json={'username': 'b', 'password': 'secret123',
                                             'role': 'betrachter', 'email': 'DUP@ff.de'})
    assert r.status_code == 400


def test_create_with_unique_email_ok(app, auth_client):
    r = auth_client.post('/api/users', json={'username': 'c', 'password': 'secret123',
                                             'role': 'betrachter', 'email': 'c@ff.de'})
    assert r.status_code == 201


def test_update_duplicate_email_rejected(app, auth_client):
    auth_client.post('/api/users', json={'username': 'a', 'password': 'secret123',
                                         'role': 'betrachter', 'email': 'a@ff.de'})
    uid = auth_client.post('/api/users', json={'username': 'b', 'password': 'secret123',
                                               'role': 'betrachter', 'email': 'b@ff.de'}).get_json()['id']
    r = auth_client.put(f'/api/users/{uid}', json={'email': 'A@ff.de'})
    assert r.status_code == 400
