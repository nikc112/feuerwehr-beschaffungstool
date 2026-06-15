from app import db
from app.models import User


def _mkuser(app, username, email, password='secret123', role='admin'):
    with app.app_context():
        u = User(username=username, email=email, role=role)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()


def test_login_by_username(app, client):
    _mkuser(app, 'max', 'max@ff.de')
    r = client.post('/api/auth/login', json={'username': 'max', 'password': 'secret123'})
    assert r.status_code == 200


def test_login_by_email_case_insensitive(app, client):
    _mkuser(app, 'max', 'Max@FF.de')
    r = client.post('/api/auth/login', json={'username': 'max@ff.de', 'password': 'secret123'})
    assert r.status_code == 200


def test_login_wrong_password(app, client):
    _mkuser(app, 'max', 'max@ff.de')
    assert client.post('/api/auth/login', json={'username': 'max', 'password': 'nope'}).status_code == 401


def test_login_unknown_identifier(app, client):
    assert client.post('/api/auth/login', json={'username': 'ghost@ff.de', 'password': 'x'}).status_code == 401


def test_setup_requires_email(app, client):
    r = client.post('/api/auth/setup', json={'username': 'admin', 'password': 'secret123'})
    assert r.status_code == 400


def test_setup_with_email_ok(app, client):
    r = client.post('/api/auth/setup', json={'username': 'admin', 'password': 'secret123', 'email': 'admin@ff.de'})
    assert r.status_code == 200
    assert r.get_json()['user']['email'] == 'admin@ff.de'
