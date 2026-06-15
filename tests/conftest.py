import os
import pytest


@pytest.fixture
def app(tmp_path):
    # create_app() liest DATA_DIR aus der Umgebung – pro Test isolierter Ordner.
    os.environ['DATA_DIR'] = str(tmp_path)
    os.environ['SECRET_KEY'] = 'test-secret'
    from app import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def upload_dir(app):
    return app.config['UPLOAD_FOLDER']


@pytest.fixture
def auth_client(app, client):
    from app import db
    from app.models import User
    with app.app_context():
        u = User(username='tester', role='admin')
        u.set_password('secret123')
        db.session.add(u)
        db.session.commit()
    client.post('/api/auth/login', json={'username': 'tester', 'password': 'secret123'})
    return client
