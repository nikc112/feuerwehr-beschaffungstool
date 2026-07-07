import time


def _mk_user(app, name='timo'):
    from app import db
    from app.models import User
    with app.app_context():
        u = User(username=name, role='beschaffer', email=f'{name}@ff.de')
        u.set_password('secret123')
        db.session.add(u)
        db.session.commit()


def _login(client, name='timo'):
    return client.post('/api/auth/login', json={'username': name, 'password': 'secret123'})


def _age_session(client, seconds):
    with client.session_transaction() as s:
        s['last_seen'] = time.time() - seconds


def test_timeout_after_15_minutes_inactivity(app, client):
    _mk_user(app)
    _login(client)
    assert client.get('/api/proposals').status_code == 200
    _age_session(client, 16 * 60)                      # 16 min inaktiv
    assert client.get('/api/proposals').status_code == 401


def test_activity_extends_session(app, client):
    _mk_user(app)
    _login(client)
    _age_session(client, 10 * 60)                      # 10 min inaktiv -> noch gültig
    assert client.get('/api/proposals').status_code == 200
    with client.session_transaction() as s:
        assert time.time() - s['last_seen'] < 5        # Request hat verlängert
    _age_session(client, 14 * 60)
    assert client.get('/api/proposals').status_code == 200   # Aktivität hält Sitzung am Leben


def test_me_polling_does_not_extend_session(app, client):
    _mk_user(app)
    _login(client)
    _age_session(client, 10 * 60)
    assert client.get('/api/auth/me').status_code == 200     # noch angemeldet
    with client.session_transaction() as s:
        assert time.time() - s['last_seen'] > 9 * 60         # NICHT verlängert
    _age_session(client, 16 * 60)
    assert client.get('/api/auth/me').status_code == 401     # abgelaufen -> Polling meldet 401


def test_login_sets_no_remember_cookie(app, client):
    _mk_user(app)
    r = _login(client)
    cookies = ';'.join(r.headers.getlist('Set-Cookie'))
    assert 'remember_token' not in cookies
