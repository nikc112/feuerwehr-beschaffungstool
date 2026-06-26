def _set(app, **kv):
    from app import db
    from app.models import Settings
    with app.app_context():
        for k, v in kv.items():
            Settings.set(k, v)
        db.session.commit()


def _post(client, monkeypatch, **extra):
    monkeypatch.setattr('app.api.notify_new_proposal', lambda *a, **k: None)
    data = {'bezeichnung': 'Kamera', 'einreicher_email': 'melder@example.com'}
    data.update(extra)
    return client.post('/api/proposals', data=data, content_type='multipart/form-data')


def test_get_abteilungen_filters_empty_and_flag(app):
    _set(app, abteilung_1='Einsatz', abteilung_2='', abteilung_3='Jugend', abteilung_required='true')
    from app.models import get_abteilungen
    with app.app_context():
        ab = get_abteilungen()
    assert ab['options'] == ['Einsatz', 'Jugend']   # leere werden gefiltert, Reihenfolge bleibt
    assert ab['required'] is True


def test_settings_persist_abteilungen(app, auth_client):
    auth_client.put('/api/settings', json={'abteilung_1': 'Einsatz', 'abteilung_required': 'true'})
    d = auth_client.get('/api/settings').get_json()
    assert d['abteilung_1'] == 'Einsatz'
    assert d['abteilung_required'] == 'true'


def test_create_proposal_stores_abteilung(app, client, auth_client, monkeypatch):
    _set(app, abteilung_1='Einsatz', abteilung_2='Jugend')
    nr = _post(client, monkeypatch, abteilung='Jugend').get_json()['nr']
    p = [x for x in auth_client.get('/api/proposals?status=pending').get_json() if x['nr'] == nr][0]
    assert p['abteilung'] == 'Jugend'


def test_abteilung_required_enforced(app, client, monkeypatch):
    _set(app, abteilung_1='Einsatz', abteilung_2='Jugend', abteilung_required='true')
    assert _post(client, monkeypatch).status_code == 400               # ohne Abteilung
    assert _post(client, monkeypatch, abteilung='Einsatz').status_code == 201


def test_abteilung_optional_when_not_required(app, client, monkeypatch):
    _set(app, abteilung_1='Einsatz', abteilung_required='false')
    assert _post(client, monkeypatch).status_code == 201               # ohne Abteilung erlaubt


def test_form_shows_configured_abteilungen(app, client):
    # ohne Konfiguration: kein Abteilungs-Block
    assert 'id="abteilung-grid"' not in client.get('/').get_data(as_text=True)
    _set(app, abteilung_1='Einsatzabteilung', abteilung_3='Jugendfeuerwehr')
    body = client.get('/').get_data(as_text=True)
    assert 'id="abteilung-grid"' in body
    assert 'Einsatzabteilung' in body and 'Jugendfeuerwehr' in body
